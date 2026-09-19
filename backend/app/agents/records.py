from __future__ import annotations

from app.agents.base import AgentContext
from app.agents.hypotheses import Investigation
from app.memory.service import MemoryService
from app.schemas import Decision, GraphNode, Hypothesis


OPEN_STATUSES = {"open", "needs_human", "pending_audit", "escalated"}


def memory_for(ctx: AgentContext) -> MemoryService:
    if ctx.memory is None:
        raise ValueError("AgentContext requires a memory service")
    return ctx.memory


def remember(ctx: AgentContext, result: Investigation) -> None:
    memory = memory_for(ctx)
    if result.decision is not None:
        decision = result.decision
        decision.steps = result.steps
        decision.ms = result.ms
        if result.best:
            memory.record_hypothesis(decision.exception_id, result.best)
        if memory.store.get_node(decision.id):
            memory.store.update_node(decision.id, decision.model_dump(mode="json"))
        else:
            memory.record_decision(decision.exception_id, decision)
        memory.store.update_node(decision.id, {
            "entity_id": result.entity_id,
            "entity_type": result.entity_type,
            "hypothesis_kind": result.best.kind if result.best else None,
            "sibling_txn_id": result.best.params.get("sibling_txn_id") if result.best else None,
        })
    if result.exception:
        memory.store.update_node(result.exception.id, {"steps": result.steps, "ms": result.ms})


def decision_hypothesis(ctx: AgentContext, node: GraphNode) -> tuple[Decision, Hypothesis]:
    decision = Decision.model_validate(node.props)
    hypothesis = memory_for(ctx).store.get_node(decision.hypothesis_id or "")
    if hypothesis is None:
        raise ValueError(f"Missing hypothesis for decision {node.id}")
    return decision, Hypothesis.model_validate(hypothesis.props)


def open_exceptions(ctx: AgentContext) -> list[GraphNode]:
    return [
        node for node in memory_for(ctx).store.find_nodes("Exception", period_id=ctx.period_id)
        if node.props.get("status") in OPEN_STATUSES
    ]
