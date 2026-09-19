from __future__ import annotations

from hashlib import sha256
from math import ceil
from time import perf_counter

from sqlalchemy import select

from app.agents.base import AgentContext, BaseAgent
from app.agents.evidence import ap_checks, bank_checks, check
from app.agents.hypotheses import execute_decision
from app.agents.records import decision_hypothesis, memory_for
from app.config import settings
from app.db.models import LedgerEntry
from app.schemas import AuditFinding, AuditVerdict, DecisionStatus, ExceptionRecord, ExceptionStatus, GraphNode, Tier


class AuditAgent(BaseAgent):
    name = "audit"
    display_name = "Independent audit"

    async def review(self, node: GraphNode) -> bool:
        started = perf_counter()
        steps = self.steps
        memory = memory_for(self.ctx)
        decision, hypothesis = decision_hypothesis(self.ctx, node)
        is_bank = node.props["entity_type"] == "bank_transaction"
        entity = self.call_tool("get_bank_txn" if is_bank else "get_ap_invoice", id=node.props["entity_id"])
        challenge = "Re-perform counterparty, amount, policy and ledger evidence"
        self.ctx.bump("audit_challenges")
        self.emit("audit.challenge", challenge, decision.explanation, decision_id=decision.id, exception_id=decision.exception_id)
        checks = bank_checks(self, entity, hypothesis) if is_bank else ap_checks(self, entity, hypothesis)
        if decision.status == DecisionStatus.EXECUTED and is_bank:
            with self.ctx.session() as session:
                entries = session.scalars(select(LedgerEntry).where(LedgerEntry.source_id == entity["id"], LedgerEntry.created_by.like("agent:%"))).all()
                checks.append(check("balanced_ledger", bool(entries) and abs(sum(e.debit - e.credit for e in entries)) <= 0.01, "Recalculate posted debits and credits"))
        response = "; ".join(f"{c.name}: {'pass' if c.passed else 'fail'} ({c.detail})" for c in checks)
        self.emit("audit.response", "Evidence re-performed", response, decision_id=decision.id, checks=[c.model_dump() for c in checks])
        passed = all(c.passed for c in checks)
        verdict = AuditVerdict(verdict="approved" if passed else "rejected", reasoning=response, checks=checks)
        if passed and decision.status == DecisionStatus.PENDING_AUDIT:
            passed = await execute_decision(self.ctx, self, decision, hypothesis, entity)
            if not passed:
                verdict.verdict = "escalated"
                verdict.reasoning += "; execution failed"
        decision.steps += self.steps - steps
        decision.ms += (perf_counter() - started) * 1000
        if passed:
            self.ctx.bump("audit_passed")
            if node.props.get("status") == "pending_audit":
                self.ctx.bump("auto_resolved")
                self.ctx.bump("pending_audit", -1)
            if decision.rule_id:
                memory.confirm_rule(decision.rule_id, decision.id)
        else:
            if decision.status == DecisionStatus.EXECUTED and is_bank:
                for execution in reversed(hypothesis.params.get("executions", [])):
                    self.call_tool("undo_reconciliation", execution=execution)
            decision.status = DecisionStatus.REJECTED
            if decision.rule_id:
                memory.refute_rule(decision.rule_id, decision.id)
            if is_bank:
                self.call_tool("mark_exception", bank_txn_id=entity["id"], note="Audit rejected; human review required")
            if memory.store.get_node(decision.exception_id) is None:
                exception = ExceptionRecord(period_id=self.ctx.period_id, run_id=self.ctx.run_id, entity_type=node.props["entity_type"], entity_id=entity["id"], category="audit_rejection", title="Audit requires controller review", description=response, status=ExceptionStatus.NEEDS_HUMAN, confidence=decision.confidence, tier=decision.tier, agent="audit")
                memory.record_exception(exception)
                decision.exception_id = exception.id
                memory.record_hypothesis(exception.id, hypothesis)
                memory.store.add_edge(exception.id, "RESOLVED_BY", decision.id)
        memory.record_hypothesis(decision.exception_id, hypothesis)
        memory.store.update_node(decision.id, decision.model_dump(mode="json"))
        exception_node = memory.store.get_node(decision.exception_id)
        if exception_node:
            memory.store.update_node(exception_node.id, {
                "status": "resolved" if passed else "needs_human",
                "resolved_by": "audit" if passed else None,
                "resolved_at": decision.executed_at.isoformat() if passed and decision.executed_at else None,
                "steps": decision.steps,
                "ms": decision.ms,
            })
        finding = AuditFinding(
            decision_id=decision.id, exception_id=decision.exception_id,
            challenge=challenge, response=response, verdict=verdict.verdict,
            reasoning=verdict.reasoning, checks=checks,
        )
        memory.record_audit_finding(decision.id, finding)
        self.emit("audit.verdict", f"Audit {verdict.verdict}", verdict.reasoning, decision_id=decision.id, finding=finding.model_dump(mode="json"))
        return passed

    async def run(self) -> None:
        self.started()
        memory = memory_for(self.ctx)
        decisions = memory.store.find_nodes("Decision", run_id=self.ctx.run_id)
        pending = [node for node in decisions if node.props.get("status") == "pending_audit"]
        high = [node for node in decisions if node.props.get("status") == "executed" and node.props.get("tier") == Tier.HIGH.value]
        high.sort(key=lambda node: sha256(str(node.props.get("entity_id")).encode()).hexdigest())
        rule_high = [node for node in high if node.props.get("rule_id")]
        direct_high = [node for node in high if not node.props.get("rule_id")]
        sample = rule_high[:ceil(len(rule_high) * settings.audit_sample_rate)] + direct_high[:ceil(len(direct_high) * settings.audit_sample_rate)]
        for node in pending + sample:
            await self.review(node)
        self.finished()


async def run(ctx: AgentContext) -> None:
    await AuditAgent(ctx).run()
