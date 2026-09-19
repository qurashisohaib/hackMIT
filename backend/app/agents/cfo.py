from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from time import perf_counter

from sqlalchemy import delete, select, update

from app.agents import ap_ar, audit, close, forecast, recon, report
from app.agents.base import AgentContext, BaseAgent
from app.agents.brain import get_brain
from app.agents.evidence import ap_checks, bank_checks
from app.agents.hypotheses import (
    Investigation,
    _gather_bank_facts,
    execute_decision,
    gen_memory_rules,
    reinvestigate_exception,
    tier,
)
from app.agents.records import OPEN_STATUSES, memory_for, remember
from app.config import settings
from app.db.models import APInvoice, ARInvoice, BankTransaction, GroundTruth, LedgerEntry, Payment, PayrollRun, Period
from app.db.session import new_session
from app.events import bus
from app.memory.service import MemoryService, get_memory_service
from app.schemas import (
    Decision,
    DecisionStatus,
    ForecastView,
    HumanCorrection,
    Hypothesis,
    ResolveRequest,
    ResolveResponse,
    RuleSpec,
    RunMetrics,
    RunSummary,
    new_id,
)


def make_context(period_id: str, run_id: str | None = None, step_delay_ms: int | None = None) -> AgentContext:
    return AgentContext(
        period_id=period_id, run_id=run_id or new_id("RUN"),
        memory=get_memory_service(), brain=get_brain(),
        step_delay_ms=settings.step_delay_ms if step_delay_ms is None else step_delay_ms,
    )


def _snapshot(ctx: AgentContext) -> None:
    memory = memory_for(ctx)
    if memory.store.find_nodes("Observation", key="execution_baseline", scope_id=ctx.period_id):
        return
    with ctx.session() as session:
        aps = session.scalars(select(APInvoice).where(APInvoice.period_id <= ctx.period_id)).all()
        ars = session.scalars(select(ARInvoice).where(ARInvoice.period_id <= ctx.period_id)).all()
        payments = session.scalars(select(Payment).where(Payment.period_id <= ctx.period_id)).all()
        payrolls = session.scalars(select(PayrollRun).where(PayrollRun.period_id <= ctx.period_id)).all()
        value = {
            "ap": [{"id": row.id, "status": row.status, "paid_amount": row.paid_amount, "matched_bank_txn_ids": row.matched_bank_txn_ids, "description": row.description, "approved_by": row.approved_by} for row in aps],
            "ar": [{"id": row.id, "status": row.status, "paid_amount": row.paid_amount, "matched_bank_txn_ids": row.matched_bank_txn_ids} for row in ars],
            "payments": [{"id": row.id, "status": row.status, "bank_txn_id": row.bank_txn_id} for row in payments],
            "payroll": [{"id": row.id, "bank_txn_id": row.bank_txn_id} for row in payrolls],
        }
    memory.record_observation("global", ctx.period_id, "execution_baseline", value, ctx.period_id)


def reset_period_state(period_id: str, memory: MemoryService | None = None) -> None:
    memory = memory or get_memory_service()
    later = [node for node in memory.store.find_nodes("AgentRun", status="completed") if node.props["period_id"] > period_id and not node.props.get("superseded")]
    if later:
        raise ValueError("Reset later completed periods first to preserve cross-period settlements")
    baseline = memory.store.find_nodes("Observation", key="execution_baseline", scope_id=period_id)
    with new_session() as session:
        period = session.get(Period, period_id)
        if period is None:
            raise ValueError(f"Unknown period {period_id}")
        if baseline:
            values = baseline[0].props["value"]
            for table, rows in ((APInvoice, values["ap"]), (ARInvoice, values["ar"]), (Payment, values["payments"]), (PayrollRun, values["payroll"])):
                if rows:
                    session.execute(update(table), rows)
        for bank in session.scalars(select(BankTransaction).where(BankTransaction.period_id == period_id)):
            bank.reconciled = False
            bank.reconciled_with = []
            bank.reconciliation_note = ""
            bank.status = "unreconciled"
        session.execute(delete(LedgerEntry).where(LedgerEntry.period_id == period_id, LedgerEntry.created_by.like("agent:%")))
        period.status = "open"
        period.closed_at = None
        session.commit()
    for label in ("Exception", "Decision", "AgentRun"):
        for node in memory.store.find_nodes(label, period_id=period_id):
            props: dict = {"superseded": True}
            if label == "Exception":
                props["status"] = "superseded"
            memory.store.update_node(node.id, props)
    for node in memory.store.find_nodes("Observation", period_id=period_id):
        if node.props["key"] in {"forecast", "report", "close_checklist", "avg_days_to_pay", "execution_baseline"}:
            memory.store.upsert_node(node.id, "SupersededObservation", {**node.props, "superseded": True})


def compute_metrics(ctx: AgentContext, wall_ms: float = 0) -> RunMetrics:
    """Benchmark bank decisions and AP control exceptions, without double-counting AR settlements."""
    memory = memory_for(ctx)
    exceptions = [node for node in memory.store.find_nodes("Exception", period_id=ctx.period_id) if not node.props.get("superseded")]
    decisions = [node for node in memory.store.find_nodes("Decision", period_id=ctx.period_id) if not node.props.get("superseded") and node.props.get("status") == "executed"]
    by_entity = {}
    for node in sorted(decisions, key=lambda item: item.props["created_at"]):
        by_entity[node.props.get("entity_id")] = node
        if node.props.get("sibling_txn_id"):
            by_entity[node.props["sibling_txn_id"]] = node
    correct = decided = 0
    automatic = precedent = 0
    correct_types: Counter[str] = Counter()
    with ctx.session() as session:
        truth = session.scalars(select(GroundTruth).where(GroundTruth.period_id == ctx.period_id)).all()
        control_patterns = {"duplicate", "policy_no_po", "policy_price_variance", "timing_lag"}
        benchmark = [item for item in truth if item.entity_type == "bank_transaction" or item.entity_type == "ap_invoice" and item.pattern in control_patterns]
        truth_types = {item.entity_id: item.exception_type for item in truth}
        payment_links = {payment.id: payment.ap_invoice_ids for payment in session.scalars(select(Payment))}

        def normalized(ids: list[str]) -> set[str]:
            result = set()
            for entity_id in ids:
                result.update(payment_links.get(entity_id, [entity_id]))
            return result

        for item in benchmark:
            found = by_entity.get(item.entity_id)
            if found is None:
                continue
            node = found
            if item.entity_type == "bank_transaction":
                bank = session.get(BankTransaction, item.entity_id)
                if bank is None or not bank.reconciled:
                    continue
                actual = bank.reconciled_with
            else:
                actual = node.props.get("matched_ids", [])
            decided += 1
            automatic += node.props["agent"] != "human"
            precedent += bool(node.props.get("rule_id")) and node.props["agent"] != "human"
            kind = node.props.get("hypothesis_kind")
            pattern_ok = kind == item.pattern or {kind, item.pattern}.issubset({"exact_match", "counterparty_exact"})
            match_ok = normalized(actual) == normalized(item.matched_ids)
            parameter_ok = True
            if item.pattern.startswith("rule_"):
                hypothesis = memory.store.get_node(node.props.get("hypothesis_id", ""))
                params = hypothesis.props.get("params", {}) if hypothesis else {}
                learned = params.get("rule", {}).get("params", {})
                for key in ("rate", "amount", "direction", "tolerance_pct"):
                    if key in item.params:
                        observed = learned.get(key)
                        expected = item.params[key]
                        if isinstance(expected, (int, float)):
                            parameter_ok &= isinstance(observed, (int, float)) and abs(observed - expected) <= 0.00001
                        else:
                            parameter_ok &= observed == expected
            if pattern_ok and match_ok and parameter_ok:
                correct += 1
                correct_types[item.exception_type] += 1
    pending = [node for node in exceptions if node.props["status"] in OPEN_STATUSES]
    kinds = Counter(truth_types.get(node.props["entity_id"], node.props["category"]) for node in exceptions)
    human_kinds = Counter(truth_types.get(node.props["entity_id"], node.props["category"]) for node in pending)
    measured = [node for node in exceptions if "ms" in node.props and "steps" in node.props]
    current_rules = {node.props["rule_id"] for node in decisions if node.props.get("rule_id")}
    period_decisions = {node.id for node in memory.store.find_nodes("Decision", period_id=ctx.period_id) if not node.props.get("superseded")}
    audits = [node for node in memory.store.find_nodes("AuditFinding") if node.props["decision_id"] in period_decisions]
    forecasts = memory.store.find_nodes("Observation", key="forecast", scope_id=ctx.period_id)
    error = forecasts[0].props["value"].get("error_pct") if forecasts else None
    runs = [node for node in memory.store.find_nodes("AgentRun", period_id=ctx.period_id) if not node.props.get("superseded") and node.props.get("status") == "completed"]
    saved_wall_ms = runs[-1].props["metrics"]["wall_ms"] if runs else 0
    return RunMetrics(
        period_id=ctx.period_id, run_id=ctx.run_id or "", total_items=len(benchmark),
        exceptions_raised=len(exceptions),
        auto_resolved=automatic,
        precedent_hits=precedent,
        human_reviews=len(pending), audit_challenges=len(audits),
        audit_passed=sum(node.props.get("verdict") == "approved" for node in audits),
        accuracy=correct / decided if decided else None,
        coverage=decided / len(benchmark) if benchmark else None,
        avg_steps_per_exception=mean(float(node.props["steps"]) for node in measured) if measured else None,
        avg_ms_per_exception=mean(float(node.props["ms"]) for node in measured) if measured else None,
        wall_ms=wall_ms or ctx.extra.get("wall_ms", saved_wall_ms), llm_tokens=ctx.counters["llm_tokens"],
        llm_cost_usd=ctx.counters["llm_cost_micro_usd"] / 1_000_000,
        exceptions_by_type=dict(kinds), human_review_by_type=dict(human_kinds),
        correct_by_type=dict(correct_types), rules_used=len(current_rules),
        rules_learned=len(memory.store.find_nodes("Rule", learned_in_period=ctx.period_id)),
        forecast_error_pct=error,
    )


def evaluate_forecast(ctx: AgentContext, view: ForecastView) -> None:
    """Cash-flow WAPE over fully observed weeks of the next period."""
    with ctx.session() as session:
        next_period = session.scalar(select(Period).where(Period.start_date > date.fromisoformat(view.as_of)).order_by(Period.start_date))
        if next_period is None:
            return
        bank = list(session.scalars(select(BankTransaction).where(BankTransaction.period_id == next_period.id)))
    if not bank:
        return
    through = min(next_period.end_date, max(item.date for item in bank))
    error = actual_total = 0.0
    for week in view.weeks:
        start = date.fromisoformat(week.week_start)
        end = start + timedelta(days=6)
        if end > through:
            continue
        transactions = [item for item in bank if start <= item.date <= end]
        inflow = sum(item.amount for item in transactions if item.amount > 0)
        outflow = -sum(item.amount for item in transactions if item.amount < 0)
        error += abs(week.inflows - inflow) + abs(week.outflows - outflow)
        actual_total += inflow + outflow
        view.actual_vs_forecast.append({"week_start": week.week_start, "forecast_inflows": week.inflows, "forecast_outflows": week.outflows, "actual_inflows": round(inflow, 2), "actual_outflows": round(outflow, 2), "method": "cash_flow_wape"})
    view.error_pct = round(100 * error / actual_total, 2) if actual_total else None
    memory_for(ctx).record_observation("global", ctx.period_id, "forecast", view.model_dump(mode="json"), ctx.period_id)


class CFOAgent(BaseAgent):
    name = "cfo"
    display_name = "CFO"

    async def run(self) -> RunSummary:
        return await run_period_close(self.ctx.period_id, ctx=self.ctx)


async def run_period_close(period_id: str, run_id: str | None = None, ctx: AgentContext | None = None) -> RunSummary:
    ctx = ctx or make_context(period_id, run_id)
    if ctx.period_id != period_id or run_id and ctx.run_id != run_id:
        raise ValueError("Context does not match the requested period/run")
    ctx.run_id = ctx.run_id or run_id or new_id("RUN")
    memory = memory_for(ctx)
    with ctx.session() as session:
        period = session.get(Period, period_id)
        if period is None:
            raise ValueError(f"Unknown period {period_id}")
        if period.status != "open":
            raise ValueError("Reset the period before rerunning its close")
        period.status = "in_progress"
    _snapshot(ctx)
    started_at = datetime.now(timezone.utc)
    started = perf_counter()
    ctx.emit("cfo", "run.started", f"Closing {period_id}", period_id=period_id)
    ctx.emit("cfo", "run.plan", "AP / AR → reconciliation → audit → close → forecast → report", period_id=period_id)
    try:
        await ap_ar.run(ctx)
        await recon.run(ctx)
        await audit.run(ctx)
        ap_ar.observe_collections(ctx)
        await close.run(ctx)
        evaluate_forecast(ctx, await forecast.build_forecast(ctx))
        metrics = compute_metrics(ctx, (perf_counter() - started) * 1000)
        ctx.extra["metrics"] = metrics
        await report.build_report(ctx)
        metrics.wall_ms = (perf_counter() - started) * 1000
        ctx.extra["wall_ms"] = metrics.wall_ms
        ctx.counters["human"] = metrics.human_reviews
        summary = RunSummary(run_id=ctx.run_id, period_id=period_id, status="completed", brain=ctx.brain_name, started_at=started_at, finished_at=datetime.now(timezone.utc), counts=dict(ctx.counters), metrics=metrics)
        ctx.emit("cfo", "run.completed", f"Completed {period_id}", metrics=metrics.model_dump(mode="json"), counts=summary.counts)
        memory.record_agent_run(summary, bus.history(ctx.run_id))
        return summary
    except Exception as exc:
        with ctx.session() as session:
            period = session.get(Period, period_id)
            if period:
                period.status = "pending_review"
        ctx.emit("cfo", "run.failed", f"Close failed: {period_id}", str(exc))
        raise


def _selected_hypothesis(agent: BaseAgent, entity: dict, request: ResolveRequest, exception: dict, hypotheses: list[dict]) -> tuple[Hypothesis | None, RuleSpec | None]:
    if request.action == "dismiss":
        if not request.explanation.strip():
            raise ValueError("Dismissal requires an explanation")
        return None, None
    spec = request.rule if request.action == "teach" else None
    if request.action == "teach":
        if spec is None:
            raise ValueError("Teaching requires a valid rule")
        if exception["entity_type"] != "bank_transaction":
            raise ValueError("Teach financial settlement rules on a bank exception")
        facts = _gather_bank_facts(agent.ctx, agent, entity)
        facts.rules = [{
            **spec.model_dump(mode="json"), "id": "pending-human-rule", "version": 1,
            "trust": {"human_verified": True}, "trust_score": 0.75,
            "learned_in_period": agent.ctx.period_id,
        }]
        candidates = [h for h in gen_memory_rules(facts, agent) if h.passed]
        if request.matched_ids:
            candidates = [h for h in candidates if set(request.matched_ids).issubset(set(h.candidate_ids))]
        if len(candidates) != 1:
            raise ValueError("Rule does not uniquely explain the selected financial evidence")
        hypothesis = candidates[0]
    elif request.action == "approve_hypothesis":
        choices = [h for h in hypotheses if h["id"] == request.hypothesis_id]
        if not choices:
            raise ValueError("Hypothesis does not belong to this exception")
        hypothesis = Hypothesis.model_validate(choices[0])
        if not hypothesis.passed or hypothesis.kind in {"unknown", "precedent_drift", "observed_pattern"}:
            raise ValueError("Teach a verified rule or provide a manual match for unexplained differences")
        if hypothesis.kind.startswith("policy_") and not request.explanation.strip():
            raise ValueError("Policy approval requires the controller's explanation")
    else:
        if not request.matched_ids or len(set(request.matched_ids)) != len(request.matched_ids):
            raise ValueError("Manual match requires distinct financial evidence IDs")
        if exception["entity_type"] != "bank_transaction":
            raise ValueError("Manual matching requires a bank transaction")
        facts = _gather_bank_facts(agent.ctx, agent, entity)
        selected = [c for c in facts.own_candidates() if c["id"] in request.matched_ids]
        if {c["id"] for c in selected} != set(request.matched_ids):
            raise ValueError("Selected items are not eligible open evidence of this counterparty")
        gross = round(sum(float(c["expected_usd"]) for c in selected), 2)
        hypothesis = Hypothesis(kind="exact_match", description=request.explanation or "Controller matched financial evidence", candidate_ids=request.matched_ids, confidence=0.98, tested=True, passed=True, generated_by="human", params={"gross": gross, "candidates": selected})
    checks = bank_checks(agent, entity, hypothesis, spec) if exception["entity_type"] == "bank_transaction" else ap_checks(agent, entity, hypothesis, human_approval=True)
    failures = [item.detail for item in checks if not item.passed]
    if failures:
        raise ValueError("Financial evidence validation failed: " + "; ".join(failures))
    return hypothesis, spec


async def resolve_exception(exception_id: str, request: ResolveRequest, ctx: AgentContext | None = None) -> ResolveResponse:
    memory = memory_for(ctx) if ctx else get_memory_service()
    detail = memory.exception_detail(exception_id)
    exception = detail["exception"]
    if exception["status"] not in OPEN_STATUSES:
        raise ValueError("Exception is no longer open")
    ctx = ctx or make_context(exception["period_id"], exception["run_id"], step_delay_ms=0)
    if ctx.period_id != exception["period_id"]:
        raise ValueError("Context does not match the exception period")
    agent = CFOAgent(ctx)
    agent.name = "human"
    started = perf_counter()
    events_before = len(bus.history(ctx.run_id))
    entity = agent.call_tool("get_bank_txn" if exception["entity_type"] == "bank_transaction" else "get_ap_invoice", id=exception["entity_id"])
    if request.action == "teach" and request.rule is None and ctx.brain:
        spec = await ctx.brain.parse_correction(request.explanation, {
            "counterparty": {"type": exception["counterparty_type"], "id": exception["counterparty_id"], "name": exception["counterparty_name"]},
            "scope_type": exception["counterparty_type"], "scope_id": exception["counterparty_id"],
        })
        request = request.model_copy(update={"rule": spec})
    hypothesis, spec = _selected_hypothesis(agent, entity, request, exception, detail["hypotheses"])
    correction = HumanCorrection(exception_id=exception_id, action=request.action, explanation=request.explanation or (spec.description if spec else "Controller approval"), rule=spec, hypothesis_id=hypothesis.id if hypothesis else None, matched_ids=hypothesis.candidate_ids if hypothesis else [], by=request.by)
    rule_id = None
    propagated: list[str] = []
    decision_id = None
    if hypothesis:
        action = "hold" if hypothesis.kind == "duplicate" else "accept_in_transit" if exception["entity_type"] == "ap_invoice" and hypothesis.kind == "timing_lag" else "approve" if exception["entity_type"] == "ap_invoice" else "reconcile"
        decision = Decision(exception_id=exception_id, hypothesis_id=hypothesis.id, action=action, matched_ids=[cid for cid in hypothesis.candidate_ids if not cid.startswith("BT-")], confidence=hypothesis.confidence, tier=tier(hypothesis.confidence), agent="human", rule_id=rule_id, period_id=ctx.period_id, run_id=ctx.run_id, explanation=correction.explanation)
        if not await execute_decision(ctx, agent, decision, hypothesis, entity):
            raise ValueError("Could not execute validated correction")
        memory.record_human_correction(exception_id, correction)
        if spec:
            rule_id = memory.learn_rule(spec, correction.id, ctx.period_id, human_verified=True)
            hypothesis.rule_id = rule_id
            hypothesis.params["rule"]["id"] = rule_id
            decision.rule_id = rule_id
        result = Investigation(entity_type=exception["entity_type"], entity_id=exception["entity_id"], period_id=ctx.period_id, best=hypothesis, decision=decision, steps=agent.steps, ms=(perf_counter() - started) * 1000)
        remember(ctx, result)
        memory.store.update_node(exception_id, {"resolved_by": f"human:{request.by}", "steps": result.steps, "ms": result.ms})
        decision_id = decision.id
        if rule_id:
            memory.confirm_rule(rule_id, correction.id)
    else:
        memory.record_human_correction(exception_id, correction)
        memory.store.update_node(exception_id, {"status": "dismissed", "resolved_by": f"human:{request.by}", "resolved_at": datetime.now(timezone.utc).isoformat()})
    agent.emit("human.correction", f"Controller resolved {exception_id}", correction.explanation, correction=correction.model_dump(mode="json"), rule_id=rule_id)
    if spec:
        for sibling in memory.store.find_nodes("Exception"):
            props = sibling.props
            if sibling.id == exception_id or props["status"] not in OPEN_STATUSES or props.get("superseded"):
                continue
            if spec.scope_type.value != "global" and (props.get("counterparty_type"), props.get("counterparty_id")) != (spec.scope_type.value, spec.scope_id):
                continue
            sibling_ctx = AgentContext(period_id=props["period_id"], run_id=props["run_id"], memory=memory, brain=ctx.brain, session_factory=ctx.session_factory, step_delay_ms=0)
            sibling_agent = recon.ReconAgent(sibling_ctx)
            result = await reinvestigate_exception(sibling_ctx, sibling_agent, sibling.id)
            remember(sibling_ctx, result)
            if result.decision and result.decision.status == DecisionStatus.PENDING_AUDIT:
                node = memory.store.get_node(result.decision.id)
                if node:
                    await audit.AuditAgent(sibling_ctx).review(node)
            current = memory.store.get_node(sibling.id)
            if current and current.props["status"] == "resolved":
                propagated.append(sibling.id)
                memory.store.update_node(sibling.id, {"resolved_by": "propagation"})
        agent.emit("propagation", f"Resolved {len(propagated)} matching sibling exceptions", exception_ids=propagated, rule_id=rule_id)
    ap_ar.observe_collections(ctx)
    await close.run(ctx)
    evaluate_forecast(ctx, await forecast.build_forecast(ctx))
    ctx.extra["metrics"] = compute_metrics(ctx)
    await report.build_report(ctx)
    current = memory.store.get_node(exception_id)
    return ResolveResponse(exception_id=exception_id, status=current.props["status"] if current else "resolved", decision_id=decision_id, correction_id=correction.id, rule_id=rule_id, propagated=propagated, events=len(bus.history(ctx.run_id)) - events_before, message=f"Correction saved; {len(propagated)} sibling exceptions resolved")
