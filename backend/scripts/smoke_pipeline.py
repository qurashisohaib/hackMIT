"""Deterministic offline integration smoke. Run: uv run python -m scripts.smoke_pipeline."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.agents import cfo
from app.agents.base import AgentContext
from app.agents.records import memory_for, open_exceptions
from app.config import settings
from app.data.seed import seed_database
from app.db.session import reset_engine
from app.events import bus
from app.memory.service import reset_memory_service
from app.schemas import ResolveRequest, RuleSpec, RunMetrics


TEACHINGS = [
    RuleSpec(pattern_type="percentage_fee", scope_type="vendor", scope_id="V-STRIPE", params={"rate": 0.03, "direction": "deduct"}, description="Stripe deducts a 3% processing fee"),
    RuleSpec(pattern_type="percentage_fee", scope_type="vendor", scope_id="V001", params={"rate": 0.02, "direction": "add"}, description="CloudSpan adds a 2% surcharge"),
    RuleSpec(pattern_type="fixed_fee", scope_type="global", params={"amount": 25, "direction": "add"}, description="Outgoing wire transfers add a $25 bank fee"),
    RuleSpec(pattern_type="early_pay_discount", scope_type="customer", scope_id="C001", params={"rate": 0.02, "direction": "deduct"}, description="Apex Manufacturing takes a 2% early payment discount"),
    RuleSpec(pattern_type="fx_tolerance", scope_type="vendor", scope_id="V002", params={"tolerance_pct": 0.015}, description="Accept up to 1.5% FX variance for Bosch"),
]


def print_metrics(label: str, metrics: RunMetrics) -> None:
    print(json.dumps({
        "stage": label, "accuracy": metrics.accuracy, "human_reviews": metrics.human_reviews,
        "precedent_hits": metrics.precedent_hits, "avg_steps": metrics.avg_steps_per_exception,
        "avg_ms": metrics.avg_ms_per_exception, "coverage": metrics.coverage, "wall_ms": metrics.wall_ms,
    }), flush=True)


async def teach(ctx: AgentContext, spec: RuleSpec) -> str:
    candidates = open_exceptions(ctx)
    if spec.scope_type.value != "global":
        candidates = [node for node in candidates if node.props["counterparty_id"] == spec.scope_id]
    candidates.sort(key=lambda node: node.props["entity_id"])
    failures = []
    for node in candidates:
        if node.props["entity_type"] != "bank_transaction":
            continue
        try:
            result = await cfo.resolve_exception(node.id, ResolveRequest(action="teach", explanation=spec.description), ctx)
        except ValueError as exc:
            failures.append(str(exc))
            continue
        assert result.rule_id
        print(json.dumps({"taught": spec.description, "propagated": len(result.propagated)}), flush=True)
        return result.rule_id
    raise AssertionError(f"No financial evidence supports {spec.description}: {failures}")


async def exercise(step_delay_ms: int = 0) -> dict[str, RunMetrics]:
    january = cfo.make_context("2026-01", step_delay_ms=step_delay_ms)
    run = await cfo.run_period_close("2026-01", ctx=january)
    assert run.metrics
    jan = run.metrics
    print_metrics("January unlearned", jan)
    assert jan.human_reviews >= 8, jan
    assert jan.precedent_hits == 0, jan
    for rule in TEACHINGS:
        await teach(january, rule)
    jan_corrected = cfo.compute_metrics(january)
    print_metrics("January after teaching", jan_corrected)
    february = cfo.make_context("2026-02", step_delay_ms=step_delay_ms)
    run = await cfo.run_period_close("2026-02", ctx=february)
    assert run.metrics
    feb = run.metrics
    print_metrics("February learned", feb)
    assert feb.precedent_hits >= 10, feb
    assert feb.human_reviews <= 4, feb
    assert feb.accuracy is not None and feb.accuracy >= 0.9, feb
    march = cfo.make_context("2026-03", step_delay_ms=step_delay_ms)
    run = await cfo.run_period_close("2026-03", ctx=march)
    assert run.metrics
    mar = run.metrics
    print_metrics("March before correction", mar)
    drift = [node for node in open_exceptions(march) if node.props["counterparty_id"] == "V001" and "precedent_drift" in node.props["tags"]]
    assert len(drift) == 2, [(node.props["entity_id"], node.props["tags"]) for node in open_exceptions(march)]
    new_rule = await teach(march, RuleSpec(pattern_type="percentage_fee", scope_type="vendor", scope_id="V001", params={"rate": 0.025, "direction": "add"}, description="CloudSpan now adds a 2.5% surcharge"))
    memory = memory_for(march)
    superseded = memory.store.neighbors(new_rule, rel="SUPERSEDES", direction="out", label="Rule")
    assert len(superseded) == 1 and superseded[0].props["params"]["rate"] == 0.02
    assert all(memory.store.get_node(node.id).props["status"] == "resolved" for node in drift)
    mar_corrected = cfo.compute_metrics(march)
    print_metrics("March corrected", mar_corrected)
    return {"january": jan, "january_corrected": jan_corrected, "february": feb, "march": mar, "march_corrected": mar_corrected}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step-delay-ms", type=int, default=0)
    args = parser.parse_args()
    if args.step_delay_ms < 0:
        parser.error("--step-delay-ms must be non-negative")
    root = Path.home() / ".cfo-smoke"
    root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="pipeline-", dir=root) as isolated:
        settings.db_path = str(Path(isolated) / "smoke.sqlite")
        settings.llm_enabled = False
        settings.neo4j_uri = None
        settings.step_delay_ms = 0
        reset_engine()
        bus.clear()
        seed_database(reset=True)
        reset_memory_service()
        asyncio.run(exercise(args.step_delay_ms))
        reset_engine()
    print("Offline pipeline acceptance passed.", flush=True)


if __name__ == "__main__":
    main()
