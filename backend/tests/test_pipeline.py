from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents import cfo
from app.agents.audit import AuditAgent
from app.agents.hypotheses import investigate_bank_txn
from app.agents.recon import ReconAgent
from app.agents.records import open_exceptions, remember
from app.config import settings
from app.data.seed import seed_database
from app.db.models import APInvoice, ARInvoice, BankTransaction, GroundTruth, LedgerEntry, Payment
from app.db.session import reset_engine
from app.events import bus
from app.memory.service import MemoryService, reset_memory_service
from app.schemas import CloseReport, ForecastView, ResolveRequest, RuleSpec
from scripts.smoke_pipeline import TEACHINGS, exercise, teach


@pytest.fixture
def isolated(tmp_path: Path) -> Iterator[MemoryService]:
    previous = settings.db_path, settings.llm_enabled, settings.neo4j_uri, settings.step_delay_ms
    settings.db_path = str(tmp_path / "pipeline.sqlite")
    settings.llm_enabled = False
    settings.neo4j_uri = None
    settings.step_delay_ms = 0
    reset_engine()
    bus.clear()
    seed_database(reset=True)
    memory = reset_memory_service()
    yield memory
    reset_engine()
    settings.db_path, settings.llm_enabled, settings.neo4j_uri, settings.step_delay_ms = previous


async def test_offline_learning_curve_and_persisted_deliverables(isolated: MemoryService) -> None:
    metrics = await exercise()
    for period in ("2026-01", "2026-02", "2026-03"):
        nodes = isolated.store.find_nodes("Observation", key="forecast", scope_id=period)
        assert len(nodes) == 1 and nodes[0].props["scope_type"] == "global"
        view = ForecastView.model_validate(nodes[0].props["value"])
        assert view.period_id == period and len(view.weeks) == 13
        cash = view.opening_cash
        for week in view.weeks:
            assert week.net == pytest.approx(week.inflows - week.outflows, abs=0.01)
            cash += week.net
            assert week.ending_cash == pytest.approx(cash, abs=0.01)
        assert any(item["source"] == "observation" for item in view.assumptions)
        reports = isolated.store.find_nodes("Observation", key="report", scope_id=period)
        report = CloseReport.model_validate(reports[0].props["value"])
        assert report.period_id == period and report.checklist and report.metrics
        assert report.metrics.auto_resolved <= report.metrics.total_items
    assert metrics["february"].forecast_error_pct is not None
    assert metrics["february"].audit_challenges > 0
    assert metrics["february"].audit_challenges == metrics["february"].audit_passed
    assert metrics["january"].wall_ms > 0
    assert metrics["january"].avg_ms_per_exception > 0
    assert metrics["january"].accuracy == 1
    assert metrics["january"].coverage < 1
    assert len(isolated.store.find_nodes("AgentRun", status="completed")) == 3
    kinds = {event.kind for event in bus.history()}
    assert {"run.started", "run.completed", "audit.challenge", "audit.response", "audit.verdict", "forecast.updated", "report.ready"} <= kinds


async def test_unlearned_stripe_stays_at_42_percent(isolated: MemoryService) -> None:
    ctx = cfo.make_context("2026-01", step_delay_ms=0)
    with ctx.session() as session:
        truth = session.scalar(select(GroundTruth).where(GroundTruth.period_id == ctx.period_id, GroundTruth.entity_type == "bank_transaction", GroundTruth.exception_type == "processor_fee"))
        txn_id = truth.entity_id
    result = await investigate_bank_txn(ctx, ReconAgent(ctx), txn_id)
    assert result.best.kind == "observed_pattern"
    assert result.best.confidence == 0.42
    assert result.status == "needs_human" and result.decision is None
    with ctx.session() as session:
        assert not session.get(BankTransaction, txn_id).reconciled
        assert not session.scalars(select(LedgerEntry).where(LedgerEntry.source_id == txn_id, LedgerEntry.created_by.like("agent:%"))).all()


async def test_teaching_rejects_wrong_scope_rate_and_evidence_without_writes(isolated: MemoryService) -> None:
    ctx = cfo.make_context("2026-01", step_delay_ms=0)
    await cfo.run_period_close(ctx.period_id, ctx=ctx)
    exception = next(node for node in open_exceptions(ctx) if node.props["counterparty_id"] == "V-STRIPE")
    requests = [
        ResolveRequest(action="teach", rule=TEACHINGS[1]),
        ResolveRequest(action="teach", rule=RuleSpec(pattern_type="percentage_fee", scope_type="vendor", scope_id="V-STRIPE", params={"rate": 0.031, "direction": "deduct"}, description="Unsupported fee")),
        ResolveRequest(action="teach", rule=TEACHINGS[0], matched_ids=["nonexistent"]),
        ResolveRequest(action="manual_match", matched_ids=["AR-2026-01-0001", "AR-2026-01-0001"]),
    ]
    for request in requests:
        with pytest.raises(ValueError):
            await cfo.resolve_exception(exception.id, request, ctx)
    assert not isolated.store.find_nodes("Rule")
    assert not isolated.store.find_nodes("HumanCorrection")
    assert isolated.store.get_node(exception.id).props["status"] == "needs_human"


@pytest.mark.parametrize("execute_before_audit", [False, True])
async def test_audit_reloads_financial_evidence_and_refutes_rule(isolated: MemoryService, execute_before_audit: bool) -> None:
    ctx = cfo.make_context("2026-01", step_delay_ms=0)
    rule_id = isolated.learn_rule(TEACHINGS[0], "controller", ctx.period_id, human_verified=True)
    if execute_before_audit:
        isolated.confirm_rule(rule_id, "controller")
        isolated.confirm_rule(rule_id, "controller")
    with ctx.session() as session:
        truth = session.scalar(select(GroundTruth).where(GroundTruth.period_id == ctx.period_id, GroundTruth.entity_type == "bank_transaction", GroundTruth.exception_type == "processor_fee"))
        txn_id = truth.entity_id
        invoice_ids = truth.matched_ids
        before = {iid: session.get(ARInvoice, iid).paid_amount for iid in invoice_ids}
    result = await investigate_bank_txn(ctx, ReconAgent(ctx), txn_id)
    remember(ctx, result)
    assert result.executed == execute_before_audit
    assert result.decision.status.value == ("executed" if execute_before_audit else "pending_audit")
    with ctx.session() as session:
        session.get(BankTransaction, txn_id).amount += 123.45
    decision = isolated.store.get_node(result.decision.id)
    assert not await AuditAgent(ctx).review(decision)
    assert isolated.store.get_node(rule_id).props["trust"]["times_refuted"] == 1
    assert isolated.store.get_node(result.exception.id).props["status"] == "needs_human"
    with ctx.session() as session:
        assert not session.get(BankTransaction, txn_id).reconciled
        assert {iid: session.get(ARInvoice, iid).paid_amount for iid in invoice_ids} == before
        assert not session.scalars(select(LedgerEntry).where(LedgerEntry.source_id == txn_id, LedgerEntry.created_by.like("agent:%"))).all()
    events = [event.kind for event in bus.history(ctx.run_id) if event.kind.startswith("audit.")]
    assert events == ["audit.challenge", "audit.response", "audit.verdict"]


async def test_reset_preserves_learning_and_replays_without_duplicate_postings(isolated: MemoryService) -> None:
    ctx = cfo.make_context("2026-01", step_delay_ms=0)
    await cfo.run_period_close(ctx.period_id, ctx=ctx)
    rule_id = await teach(ctx, TEACHINGS[0])
    old_exceptions = isolated.store.find_nodes("Exception", period_id=ctx.period_id)
    corrections = isolated.store.find_nodes("HumanCorrection")
    with ctx.session() as session:
        old_ledger_count = len(session.scalars(select(LedgerEntry).where(LedgerEntry.created_by.like("agent:%"))).all())
    cfo.reset_period_state(ctx.period_id, isolated)
    assert isolated.store.get_node(rule_id).props["status"] == "active"
    assert isolated.store.find_nodes("HumanCorrection") == corrections
    assert all(isolated.store.get_node(node.id).props["status"] == "superseded" for node in old_exceptions)
    assert not isolated.store.find_nodes("Observation", key="forecast", scope_id=ctx.period_id)
    with ctx.session() as session:
        assert all(not bank.reconciled for bank in session.scalars(select(BankTransaction).where(BankTransaction.period_id == ctx.period_id)))
        assert not session.scalars(select(LedgerEntry).where(LedgerEntry.created_by.like("agent:%"))).all()
    fresh = cfo.make_context(ctx.period_id, step_delay_ms=0)
    run = await cfo.run_period_close(fresh.period_id, ctx=fresh)
    assert run.metrics.precedent_hits == 3 and run.metrics.human_reviews == 10
    assert run.metrics.accuracy == 1
    with ctx.session() as session:
        assert len(session.scalars(select(LedgerEntry).where(LedgerEntry.created_by.like("agent:%"))).all()) == old_ledger_count
    await cfo.run_period_close("2026-02")
    with pytest.raises(ValueError, match="later completed periods"):
        cfo.reset_period_state(ctx.period_id, isolated)


async def test_metrics_detect_wrong_actual_match(isolated: MemoryService) -> None:
    ctx = cfo.make_context("2026-01", step_delay_ms=0)
    await cfo.run_period_close(ctx.period_id, ctx=ctx)
    before = cfo.compute_metrics(ctx)
    with ctx.session() as session:
        truth = session.scalar(select(GroundTruth).where(GroundTruth.period_id == ctx.period_id, GroundTruth.entity_type == "bank_transaction", GroundTruth.pattern == "exact_match"))
        bank = session.get(BankTransaction, truth.entity_id)
        assert bank.reconciled
        bank.reconciled_with = []
    after = cfo.compute_metrics(ctx)
    assert before.accuracy == 1 and after.accuracy < before.accuracy
    assert after.coverage == before.coverage and after.wall_ms == before.wall_ms
    assert cfo.compute_metrics(cfo.make_context(ctx.period_id)).wall_ms == before.wall_ms


async def test_explicit_controller_policy_approval_and_dismissal(isolated: MemoryService) -> None:
    ctx = cfo.make_context("2026-01", step_delay_ms=0)
    await cfo.run_period_close(ctx.period_id, ctx=ctx)
    for kind in ("policy_no_po", "policy_price_variance"):
        exception = next(node for node in open_exceptions(ctx) if kind in node.props["tags"])
        detail = isolated.exception_detail(exception.id)
        hypothesis = next(item for item in detail["hypotheses"] if item["kind"] == kind and item["passed"])
        response = await cfo.resolve_exception(exception.id, ResolveRequest(action="approve_hypothesis", hypothesis_id=hypothesis["id"], explanation="Controller verified and approved the documented policy exception"), ctx)
        assert response.status == "resolved"
        with ctx.session() as session:
            assert session.get(APInvoice, exception.props["entity_id"]).status == "approved"
    unknown = next(node for node in open_exceptions(ctx) if node.props["category"] == "unknown_deposit")
    response = await cfo.resolve_exception(unknown.id, ResolveRequest(action="dismiss", explanation="Controller will investigate the deposit separately"), ctx)
    assert response.status == "dismissed"
    with ctx.session() as session:
        assert not session.get(BankTransaction, unknown.props["entity_id"]).reconciled
    assert not isolated.store.find_nodes("Rule")


async def test_controller_can_teach_with_natural_language(isolated: MemoryService) -> None:
    ctx = cfo.make_context("2026-01", step_delay_ms=0)
    await cfo.run_period_close(ctx.period_id, ctx=ctx)
    exception = next(node for node in open_exceptions(ctx) if node.props["counterparty_id"] == "V-STRIPE")
    result = await cfo.resolve_exception(exception.id, ResolveRequest(action="teach", explanation="Stripe deducts a 3% processing fee"), ctx)
    assert result.rule_id and len(result.propagated) == 2
    rule = isolated.store.get_node(result.rule_id)
    assert rule.props["params"] == {"rate": 0.03, "direction": "deduct"}
    assert rule.props["scope_id"] == "V-STRIPE"


async def test_full_memory_january_rerun_preserves_settlements(isolated: MemoryService) -> None:
    first = cfo.make_context("2026-01", step_delay_ms=0)
    cold = await cfo.run_period_close(first.period_id, ctx=first)
    for spec in TEACHINGS:
        await teach(first, spec)
    with first.session() as session:
        before = {
            row.id: row.paid_amount
            for model in (APInvoice, ARInvoice)
            for row in session.scalars(select(model).where(model.period_id == first.period_id))
        }
        entries_before = len(session.scalars(select(LedgerEntry).where(LedgerEntry.created_by.like("agent:%"))).all())
    cfo.reset_period_state(first.period_id, isolated)
    fresh = cfo.make_context(first.period_id, step_delay_ms=0)
    learned = await cfo.run_period_close(fresh.period_id, ctx=fresh)
    assert cold.metrics and cold.metrics.human_reviews == 13 and cold.metrics.precedent_hits == 0
    assert learned.metrics and learned.metrics.human_reviews == 3 and learned.metrics.precedent_hits == 10
    assert learned.metrics.accuracy == 1
    assert isolated.store.get_node(cold.run_id).props["metrics"]["human_reviews"] == 13
    with fresh.session() as session:
        after = {
            row.id: row.paid_amount
            for model in (APInvoice, ARInvoice)
            for row in session.scalars(select(model).where(model.period_id == fresh.period_id))
        }
        assert after == before
        entries_after = len(session.scalars(select(LedgerEntry).where(LedgerEntry.created_by.like("agent:%"))).all())
        assert entries_after == entries_before
        for payment in session.scalars(select(Payment).where(Payment.bank_txn_id.is_not(None))):
            transaction = session.get(BankTransaction, payment.bank_txn_id)
            assert transaction and transaction.reconciled
            assert payment.id in transaction.reconciled_with
