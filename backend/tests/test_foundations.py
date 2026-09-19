import json
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from sqlite3 import Cursor

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Connection, ExecutionContext

from app.agents.base import AgentContext, BaseAgent, ToolError
from app.agents.brain import HeuristicBrain
from app.agents.hypotheses import (
    BankFacts,
    _gather_bank_facts,
    gen_counterparty_exact,
    gen_exact_match,
    gen_memory_rules,
    gen_observed_pattern,
    gen_precedent_drift,
    gen_unresolved_amount_match,
    investigate_bank_txn,
    tier,
)
from app.agents.tools import registry
from app.config import settings
from app.db import session as db_session
from app.db.models import (
    APInvoice,
    ARInvoice,
    BankTransaction,
    Customer,
    GroundTruth,
    LedgerEntry,
    Period,
    Vendor,
)
from app.events import bus
from app.memory.embeddings import HashedEmbedder
from app.memory.graph import GraphStore
from app.memory.service import MemoryService, compute_trust
from app.schemas import (
    AuditFinding,
    Decision,
    DecisionStatus,
    ExceptionRecord,
    HumanCorrection,
    Hypothesis,
    PatternType,
    PrecedentQuery,
    RuleSpec,
    RuleTrust,
    ScopeType,
    Tier,
)


class AcceptanceAgent(BaseAgent):
    name = "foundation_acceptance"

    async def run(self, **kwargs: object) -> None:
        raise NotImplementedError("Tests invoke the real hypothesis engine directly")


@pytest.fixture
def foundation_memory(tmp_path: Path) -> Iterator[MemoryService]:
    original_path = settings.db_path
    db_session.reset_engine()
    settings.db_path = str(tmp_path / "foundation-acceptance.sqlite")
    try:
        yield MemoryService(GraphStore(settings.db_path), HashedEmbedder())
    finally:
        db_session.reset_engine()
        settings.db_path = original_path


@pytest.fixture
def foundation_agent(foundation_memory: MemoryService) -> Iterator[AcceptanceAgent]:
    with db_session.get_session() as session:
        session.add(
            Period(
                id="2026-01",
                name="January 2026",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
            )
        )
        session.add_all(
            [
                Vendor(
                    id="V-STRIPE",
                    name="Stripe",
                    category="processor",
                    bank_descriptor="STRIPE",
                    is_payment_processor=True,
                ),
                Vendor(
                    id="V-ALPHA",
                    name="Alpha Components",
                    category="components",
                    bank_descriptor="ALPHA COMPONENTS",
                ),
                Vendor(
                    id="V-BETA",
                    name="Beta Logistics",
                    category="logistics",
                    bank_descriptor="BETA LOGISTICS",
                ),
                Customer(
                    id="C-ACME",
                    name="Acme Labs",
                    channel="stripe",
                    bank_descriptor="ACME LABS",
                ),
                Customer(
                    id="C-OTHER",
                    name="Other Customer",
                    channel="direct",
                    bank_descriptor="OTHER CUSTOMER",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                ARInvoice(
                    id=identifier,
                    customer_id=customer_id,
                    period_id="2026-01",
                    date=date(2026, 1, 5),
                    due_date=date(2026, 2, 5),
                    amount=1_000,
                )
                for identifier, customer_id in (
                    ("AR-TEST-MAIN", "C-ACME"),
                    ("AR-TEST-FOREIGN", "C-OTHER"),
                )
            ]
        )
        session.add_all(
            [
                APInvoice(
                    id=f"INV-TEST-{suffix}",
                    vendor_id=f"V-{suffix}",
                    vendor_invoice_number=f"BILL-{suffix}-001",
                    period_id="2026-01",
                    date=date(2026, 1, 5),
                    due_date=date(2026, 2, 5),
                    amount=500,
                )
                for suffix in ("ALPHA", "BETA")
            ]
        )
        session.add_all(
            [
                BankTransaction(
                    id=identifier,
                    period_id="2026-01",
                    date=date(2026, 1, 10),
                    amount=amount,
                    description=memo,
                    type="deposit" if amount > 0 else "ach",
                )
                for identifier, amount, memo in (
                    ("BT-FEE", 970, "STRIPE PAYOUT REF AR-TEST-MAIN"),
                    ("BT-ALPHA", -500, "ALPHA COMPONENTS ACH"),
                    ("BT-BETA", -500, "BETA LOGISTICS ACH"),
                    ("BT-UNKNOWN", -500, "ACH DEBIT UNIDENTIFIED"),
                    ("BT-FALSE-REF", 1_000, "ACME LABS REF AR-TEST-FOREIGN"),
                )
            ]
        )
    agent = AcceptanceAgent(
        AgentContext(
            run_id="RUN-FOUNDATION-ACCEPTANCE",
            period_id="2026-01",
            memory=foundation_memory,
            brain=HeuristicBrain(),
            session_factory=db_session.new_session,
            step_delay_ms=0,
        ),
        registry,
    )
    try:
        yield agent
    finally:
        bus.clear()


def _spec(rate: float = 0.03, scope_id: str = "V-STRIPE") -> RuleSpec:
    return RuleSpec(
        pattern_type=PatternType.PERCENTAGE_FEE,
        scope_type=ScopeType.VENDOR,
        scope_id=scope_id,
        params={"rate": rate, "direction": "deduct"},
        description=f"{scope_id} deducts a {rate:.1%} processing fee",
    )


def _teach(
    memory: MemoryService,
    spec: RuleSpec | None = None,
    period_id: str = "2026-01",
    human_verified: bool = True,
) -> tuple[str, str, str]:
    spec = spec or _spec()
    exception = ExceptionRecord(
        period_id=period_id,
        entity_type="bank_transaction",
        entity_id=f"BT-{period_id}-TEACHING",
        counterparty_type=spec.scope_type.value,
        counterparty_id=spec.scope_id,
        title="Explain the settlement difference",
    )
    memory.record_exception(exception)
    correction = HumanCorrection(
        exception_id=exception.id,
        action="teach",
        explanation=spec.description,
        rule=spec,
    )
    memory.record_human_correction(exception.id, correction)
    rule_id = memory.learn_rule(spec, correction.id, period_id, human_verified)
    return rule_id, exception.id, correction.id


def _facts(agent: AcceptanceAgent, transaction_id: str = "BT-FEE") -> BankFacts:
    transaction = agent.call_tool("get_bank_txn", id=transaction_id)
    assert transaction
    return _gather_bank_facts(agent.ctx, agent, transaction)


def test_rule_learning_confirmation_and_trust_updates(
    foundation_memory: MemoryService,
) -> None:
    memory = foundation_memory
    rule_id, _, correction_id = _teach(memory, human_verified=False)
    assert memory.rule_trust(rule_id) == 0.5

    repeated_id, _, repeated_source = _teach(memory, period_id="2026-02")
    assert repeated_id == rule_id
    assert len(memory.rules()) == 1
    assert memory.rule_trust(rule_id) == pytest.approx(0.80)
    assert memory.store.has_edge(rule_id, "LEARNED_FROM", correction_id)
    assert memory.store.has_edge(rule_id, "LEARNED_FROM", repeated_source)
    assert memory.store.has_edge(rule_id, "APPLIES_TO", "V-STRIPE")

    memory.confirm_rule(rule_id, repeated_source)
    assert memory.rule_trust(rule_id) == pytest.approx(0.85)
    memory.refute_rule(rule_id, correction_id)
    assert memory.rule_trust(rule_id) == pytest.approx(0.70)
    memory.ensure_entity("Decision", "DEC-APPLIED", {"period_id": "2026-02"})
    memory.mark_applied(rule_id, "DEC-APPLIED")
    assert memory.rule_trust(rule_id) == pytest.approx(0.70)
    assert memory.rules()[0]["trust"] == {
        "times_applied": 1,
        "times_confirmed": 2,
        "times_refuted": 1,
        "human_verified": True,
    }
    assert memory.store.has_edge("DEC-APPLIED", "USED_PRECEDENT", rule_id)
    assert memory.store.has_edge(rule_id, "VERIFIED_BY", repeated_source)
    assert memory.store.has_edge(rule_id, "CHALLENGED_BY", correction_id)


@pytest.mark.parametrize(
    ("verified", "confirmed", "refuted", "expected"),
    [
        (False, 0, 0, 0.5),
        (True, 0, 0, 0.75),
        (True, 2, 1, 0.7),
        (True, 20, 0, 1.0),
        (False, 0, 20, 0.0),
    ],
)
def test_trust_formula_and_clamping(
    verified: bool, confirmed: int, refuted: int, expected: float
) -> None:
    trust = RuleTrust(
        human_verified=verified,
        times_confirmed=confirmed,
        times_refuted=refuted,
        times_applied=100,
    )
    assert compute_trust(trust) == pytest.approx(expected)


def test_changed_rule_supersedes_only_its_scope_and_survives_reload(
    foundation_memory: MemoryService,
) -> None:
    memory = foundation_memory
    original, _, _ = _teach(memory)
    unrelated, _, _ = _teach(memory, _spec(scope_id="V-OTHER"))
    updated, _, source = _teach(memory, _spec(0.025), "2026-03")

    assert updated != original
    assert {node.id for node in memory.active_rules()} == {updated, unrelated}
    rules = {rule["id"]: rule for rule in memory.rules()}
    assert rules[original]["status"] == "superseded"
    assert rules[original]["params"]["rate"] == 0.03
    assert rules[updated]["version"] == 2
    assert rules[updated]["params"]["rate"] == 0.025
    assert rules[updated]["supersedes"] == original
    assert memory.store.has_edge(updated, "SUPERSEDES", original)
    assert memory.store.has_edge(updated, "LEARNED_FROM", source)
    assert memory.store.has_edge(updated, "IN_PERIOD", "2026-03")

    reloaded = MemoryService(GraphStore(settings.db_path), HashedEmbedder())
    assert {rule["id"]: rule for rule in reloaded.rules()} == rules
    assert {node.id for node in reloaded.active_rules()} == {updated, unrelated}
    assert reloaded.store.has_edge(updated, "SUPERSEDES", original)
    assert reloaded.rule_trust(updated) == pytest.approx(0.75)


def test_decision_provenance_reaches_corrections_audit_and_old_rule_after_reload(
    foundation_memory: MemoryService,
) -> None:
    memory = foundation_memory
    original, _, original_source = _teach(memory)
    current, exception_id, current_source = _teach(memory, _spec(0.025), "2026-03")
    hypothesis = Hypothesis(
        kind="rule_percentage_fee",
        description="Settlement agrees with corrected fee",
        rule_id=current,
        confidence=0.88,
        tested=True,
        passed=True,
        candidate_ids=["AR-TEST-MAIN"],
    )
    memory.record_hypothesis(exception_id, hypothesis)
    decision = Decision(
        exception_id=exception_id,
        hypothesis_id=hypothesis.id,
        action="reconcile",
        confidence=0.88,
        tier=Tier.HIGH,
        status=DecisionStatus.EXECUTED,
        rule_id=current,
        period_id="2026-03",
        matched_ids=["AR-TEST-MAIN"],
    )
    memory.record_decision(exception_id, decision)
    finding = AuditFinding(
        decision_id=decision.id,
        exception_id=exception_id,
        challenge="Reperform arithmetic",
        response="Gross less 2.5% equals settlement",
        verdict="approved",
    )
    memory.record_audit_finding(decision.id, finding)
    memory.ensure_entity(
        "HumanCorrection", "HC-UNRELATED", {"explanation": "Unrelated"}
    )

    reloaded = MemoryService(GraphStore(settings.db_path), HashedEmbedder())
    provenance = reloaded.provenance(decision.id)
    nodes = {node["id"] for node in provenance["nodes"]}
    assert {
        decision.id,
        hypothesis.id,
        exception_id,
        finding.id,
        current,
        original,
        current_source,
        original_source,
        "V-STRIPE",
        "2026-03",
    } <= nodes
    assert "HC-UNRELATED" not in nodes
    assert provenance["root"] == decision.id
    assert provenance["order"][0] == decision.id
    assert len(provenance["order"]) == len(set(provenance["order"])) == len(nodes)
    edges = {(edge["src"], edge["rel"], edge["dst"]) for edge in provenance["edges"]}
    assert {
        (decision.id, "USED_PRECEDENT", current),
        (decision.id, "TESTED", hypothesis.id),
        (decision.id, "VERIFIED_BY", finding.id),
        (exception_id, "RESOLVED_BY", decision.id),
        (exception_id, "CORRECTED_BY", current_source),
        (current, "SUPERSEDES", original),
        (current, "LEARNED_FROM", current_source),
        (original, "LEARNED_FROM", original_source),
    } <= edges
    detail = reloaded.exception_detail(exception_id)
    assert detail["exception"]["status"] == "resolved"
    assert detail["decision"]["id"] == decision.id
    assert [rule["id"] for rule in detail["rules_used"]] == [current]
    assert detail == memory.exception_detail(exception_id)
    assert (
        next(rule for rule in reloaded.rules() if rule["id"] == current)["trust"][
            "times_applied"
        ]
        == 1
    )


def test_precedent_retrieval_respects_scope_pattern_and_weighted_score(
    foundation_memory: MemoryService,
) -> None:
    memory = foundation_memory
    spec = _spec()
    scoped, _, _ = _teach(memory, spec)
    foreign, _, _ = _teach(memory, _spec(scope_id="V-OTHER"))
    global_spec = spec.model_copy(
        update={"scope_type": ScopeType.GLOBAL, "scope_id": None}
    )
    global_rule, _, _ = _teach(memory, global_spec)
    query = PrecedentQuery(
        counterparty_type="vendor",
        counterparty_id="V-STRIPE",
        pattern_hints=["percentage_fee"],
        text=spec.description,
    )

    matches = memory.find_precedents(query)
    assert [match.rule_id for match in matches] == [scoped, global_rule]
    assert foreign not in {match.rule_id for match in matches}
    assert matches[0].structural == 1
    assert matches[0].semantic == pytest.approx(1)
    assert matches[0].trust == pytest.approx(0.75)
    assert matches[0].score == pytest.approx(0.9625)
    assert matches[1].structural == 0.6
    assert matches[1].score == pytest.approx(0.7425)
    assert memory.find_precedents(query.model_copy(update={"limit": 1})) == matches[:1]
    assert (
        memory.find_precedents(
            query.model_copy(update={"pattern_hints": ["fx_tolerance"]})
        )
        == []
    )


@pytest.mark.parametrize(
    ("text", "pattern", "params"),
    [
        (
            "Stripe deducts a 3% processing fee",
            PatternType.PERCENTAGE_FEE,
            {"rate": 0.03, "direction": "deduct"},
        ),
        (
            "$25 wire fee",
            PatternType.FIXED_FEE,
            {"amount": 25.0, "direction": "either"},
        ),
        (
            "2% early payment discount",
            PatternType.EARLY_PAY_DISCOUNT,
            {"rate": 0.02, "direction": "deduct"},
        ),
        (
            "accept up to 1.5% FX variance",
            PatternType.FX_TOLERANCE,
            {"tolerance_pct": 0.015},
        ),
        (
            "2.5% surcharge",
            PatternType.PERCENTAGE_FEE,
            {"rate": 0.025, "direction": "add"},
        ),
    ],
)
async def test_heuristic_brain_parses_all_teaching_phrases(
    text: str, pattern: PatternType, params: dict[str, float | str]
) -> None:
    result = await HeuristicBrain().parse_correction(
        text, {"counterparty_type": "vendor", "counterparty_id": "V-STRIPE"}
    )

    assert result is not None
    assert result.pattern_type == pattern
    assert result.params == params
    assert result.scope_type == ScopeType.VENDOR
    assert result.scope_id == "V-STRIPE"
    assert result.description


@pytest.mark.parametrize("text", ["", "   ", "Please reconcile this transaction"])
async def test_heuristic_brain_does_not_invent_a_rule(text: str) -> None:
    assert await HeuristicBrain().parse_correction(text, {}) is None


async def test_heuristic_brain_supports_global_and_customer_scope() -> None:
    brain = HeuristicBrain()
    global_rule = await brain.parse_correction(
        "$25 wire fee for all vendors",
        {"counterparty_type": "vendor", "counterparty_id": "V-STRIPE"},
    )
    assert global_rule is not None
    assert global_rule.scope_type == ScopeType.GLOBAL
    assert global_rule.scope_id is None
    customer_rule = await brain.parse_correction(
        "2% early payment discount",
        {"counterparty_type": "customer", "counterparty_id": "C-ACME"},
    )
    assert customer_rule is not None
    assert customer_rule.scope_type == ScopeType.CUSTOMER
    assert customer_rule.scope_id == "C-ACME"


@pytest.mark.parametrize(
    ("transaction_id", "vendor_id", "invoice_id"),
    [
        ("BT-ALPHA", "V-ALPHA", "INV-TEST-ALPHA"),
        ("BT-BETA", "V-BETA", "INV-TEST-BETA"),
    ],
)
def test_same_amount_matches_only_the_identified_vendor(
    foundation_agent: AcceptanceAgent,
    transaction_id: str,
    vendor_id: str,
    invoice_id: str,
) -> None:
    facts = _facts(foundation_agent, transaction_id)
    assert facts.counterparty["id"] == vendor_id
    matches = foundation_agent.call_tool(
        "find_amount_candidates",
        amount=500,
        side="out",
        period_id="2026-01",
        counterparty=facts.counterparty,
    )
    assert [candidate["id"] for candidate in matches] == [invoice_id]
    hypotheses = gen_counterparty_exact(facts)
    assert len(hypotheses) == 1
    assert hypotheses[0].candidate_ids == [invoice_id]
    assert hypotheses[0].passed
    assert tier(hypotheses[0].confidence) == Tier.HIGH


def test_unidentified_counterparty_cannot_resolve_an_amount_collision(
    foundation_agent: AcceptanceAgent,
) -> None:
    facts = _facts(foundation_agent, "BT-UNKNOWN")
    assert facts.counterparty["type"] == "unknown"
    hypotheses = gen_unresolved_amount_match(facts, foundation_agent)
    assert len(hypotheses) == 1
    assert set(hypotheses[0].candidate_ids) == {"INV-TEST-ALPHA", "INV-TEST-BETA"}
    assert not hypotheses[0].passed
    assert tier(hypotheses[0].confidence) == Tier.LOW


def test_memo_reference_cannot_override_conflicting_counterparty(
    foundation_agent: AcceptanceAgent,
) -> None:
    facts = _facts(foundation_agent, "BT-FALSE-REF")
    assert facts.counterparty["id"] == "C-ACME"
    hypotheses = gen_exact_match(facts)
    assert not any(hypothesis.passed for hypothesis in hypotheses), (
        "A reference to another customer's invoice must fail counterparty verification"
    )


def test_fee_without_learned_rule_is_only_a_low_confidence_observation(
    foundation_agent: AcceptanceAgent,
) -> None:
    facts = _facts(foundation_agent)
    assert facts.rules == []
    assert gen_memory_rules(facts, foundation_agent) == []
    hypotheses = gen_observed_pattern(facts, foundation_agent)
    assert len(hypotheses) == 1
    observation = hypotheses[0]
    assert observation.kind == "observed_pattern"
    assert observation.passed and observation.tested
    assert observation.confidence == 0.42
    assert tier(observation.confidence) == Tier.LOW
    assert observation.rule_id is None
    assert observation.candidate_ids == ["AR-TEST-MAIN"]
    assert observation.params["suggested_params"] == {
        "rate": 0.03,
        "direction": "deduct",
    }
    assert any(
        evidence.kind == "precedent" and not evidence.supports
        for evidence in observation.evidence
    )
    assert foundation_agent.ctx.memory is not None
    assert foundation_agent.ctx.memory.rules() == []
    with db_session.get_session() as session:
        transaction = session.get(BankTransaction, "BT-FEE")
        assert transaction is not None and not transaction.reconciled
        assert session.scalars(select(LedgerEntry)).all() == []


@pytest.mark.parametrize(
    ("verified", "confirmed", "refuted", "expected", "expected_tier"),
    [
        (False, 0, 0, 0.70, Tier.MEDIUM),
        (True, 0, 0, 0.82, Tier.MEDIUM),
        (True, 1, 0, 0.85, Tier.HIGH),
        (True, 5, 0, 0.96, Tier.HIGH),
        (True, 20, 0, 0.96, Tier.HIGH),
        (True, 2, 1, 0.78, Tier.MEDIUM),
        (True, 0, 10, 0.10, Tier.LOW),
    ],
)
def test_learned_rule_confidence_tracks_real_confirmation_and_refutation(
    foundation_agent: AcceptanceAgent,
    verified: bool,
    confirmed: int,
    refuted: int,
    expected: float,
    expected_tier: Tier,
) -> None:
    memory = foundation_agent.ctx.memory
    assert memory is not None
    rule_id, _, source = _teach(memory, human_verified=verified)
    for _ in range(confirmed):
        memory.confirm_rule(rule_id, source)
    for _ in range(refuted):
        memory.refute_rule(rule_id, source)

    hypotheses = gen_memory_rules(_facts(foundation_agent), foundation_agent)
    assert len(hypotheses) == 1
    hypothesis = hypotheses[0]
    assert hypothesis.kind == "rule_percentage_fee"
    assert hypothesis.passed
    assert hypothesis.rule_id == rule_id
    assert hypothesis.generated_by == "memory"
    assert hypothesis.candidate_ids == ["AR-TEST-MAIN"]
    assert hypothesis.params["expected"] == 970
    assert hypothesis.confidence == pytest.approx(expected)
    assert tier(hypothesis.confidence) == expected_tier


def test_changed_fee_requires_review_until_the_rule_is_revised(
    foundation_agent: AcceptanceAgent,
) -> None:
    memory = foundation_agent.ctx.memory
    assert memory is not None
    rule_id, _, _ = _teach(memory)
    with db_session.get_session() as session:
        transaction = session.get(BankTransaction, "BT-FEE")
        assert transaction is not None
        transaction.amount = 975

    facts = _facts(foundation_agent)
    assert not any(
        hypothesis.passed for hypothesis in gen_memory_rules(facts, foundation_agent)
    )
    drift = gen_precedent_drift(facts, foundation_agent)
    assert len(drift) == 1
    assert drift[0].rule_id == rule_id
    assert drift[0].confidence == 0.55
    assert tier(drift[0].confidence) == Tier.LOW
    updated, _, _ = _teach(memory, _spec(0.025))
    revised = gen_memory_rules(_facts(foundation_agent), foundation_agent)
    assert len(revised) == 1 and revised[0].passed
    assert revised[0].rule_id == updated


def test_learned_rule_cannot_match_a_foreign_referenced_invoice(
    foundation_agent: AcceptanceAgent,
) -> None:
    memory = foundation_agent.ctx.memory
    assert memory is not None
    spec = _spec().model_copy(
        update={"scope_type": ScopeType.CUSTOMER, "scope_id": "C-ACME"}
    )
    _teach(memory, spec)
    with db_session.get_session() as session:
        transaction = session.get(BankTransaction, "BT-FALSE-REF")
        assert transaction is not None
        transaction.amount = 970
    facts = _facts(foundation_agent, "BT-FALSE-REF")
    assert facts.counterparty["id"] == "C-ACME"
    hypotheses = gen_memory_rules(facts, foundation_agent)
    assert not any(
        hypothesis.passed and "AR-TEST-FOREIGN" in hypothesis.candidate_ids
        for hypothesis in hypotheses
    ), "A learned fee does not authorize settlement against another customer's invoice"


def test_customer_rule_does_not_leak_to_another_processor_customer(
    foundation_agent: AcceptanceAgent,
) -> None:
    memory = foundation_agent.ctx.memory
    assert memory is not None
    spec = _spec().model_copy(
        update={"scope_type": ScopeType.CUSTOMER, "scope_id": "C-ACME"}
    )
    _teach(memory, spec)
    with db_session.get_session() as session:
        customer = session.get(Customer, "C-OTHER")
        transaction = session.get(BankTransaction, "BT-FEE")
        assert customer is not None and transaction is not None
        customer.channel = "stripe"
        transaction.description = "STRIPE PAYOUT REF AR-TEST-FOREIGN"
    facts = _facts(foundation_agent)
    assert facts.counterparty["id"] == "V-STRIPE"
    hypotheses = gen_memory_rules(facts, foundation_agent)
    assert not any(
        hypothesis.passed and "AR-TEST-FOREIGN" in hypothesis.candidate_ids
        for hypothesis in hypotheses
    ), "A customer-scoped rule must not apply to another customer on the same processor"


async def test_bank_investigation_keeps_unlearned_fee_for_human_review(
    foundation_agent: AcceptanceAgent,
) -> None:
    investigation = await investigate_bank_txn(
        foundation_agent.ctx, foundation_agent, "BT-FEE"
    )
    assert investigation.best is not None
    assert investigation.best.kind == "observed_pattern"
    assert investigation.best.confidence == 0.42
    assert investigation.status == "needs_human"
    assert not investigation.executed


async def test_bank_investigation_uses_confirmed_rule_to_reconcile(
    foundation_agent: AcceptanceAgent,
) -> None:
    memory = foundation_agent.ctx.memory
    assert memory is not None
    rule_id, _, source = _teach(memory)
    memory.confirm_rule(rule_id, source)
    investigation = await investigate_bank_txn(
        foundation_agent.ctx, foundation_agent, "BT-FEE"
    )
    assert investigation.best is not None
    assert investigation.best.rule_id == rule_id
    assert investigation.executed and investigation.status == "reconciled"
    with db_session.get_session() as session:
        transaction = session.get(BankTransaction, "BT-FEE")
        invoice = session.get(ARInvoice, "AR-TEST-MAIN")
        assert transaction is not None and transaction.reconciled
        assert invoice is not None and invoice.paid_amount == 1_000
        postings = session.scalars(select(LedgerEntry)).all()
        assert postings
        assert sum(entry.debit - entry.credit for entry in postings) == pytest.approx(0)


def test_hidden_ground_truth_has_no_tool_or_arbitrary_sql_interface() -> None:
    specs = registry.list_specs(include_mutating=True)
    names = registry.names()
    assert {"get_bank_txn", "resolve_counterparty", "counterparty_candidates"} <= set(
        names
    )
    encoded = json.dumps(specs).lower()
    assert not re.search(r"ground[ _]?truth", encoded)
    for name in ("get_ground_truth", "ground_truth", "execute_sql", "query_sql"):
        assert name not in names
        with pytest.raises(ToolError, match="unknown tool"):
            registry.get(name)
    for tool in registry.tools():
        assert not {"sql", "query", "table", "table_name"} & {
            parameter.name for parameter in tool.parameters()
        }


def test_real_reasoning_tools_never_query_or_return_hidden_truth(
    foundation_agent: AcceptanceAgent,
) -> None:
    canary = "HIDDEN-TRUTH-MUST-NOT-REACH-AGENTS"
    with db_session.get_session() as session:
        session.add(
            GroundTruth(
                entity_type="bank_transaction",
                entity_id="BT-FEE",
                period_id="2026-01",
                exception_type="unknown_deposit",
                pattern="unknown",
                explanation=canary,
                params={"secret": canary},
                matched_ids=["AR-TEST-FOREIGN"],
                requires_human=True,
            )
        )
    statements: list[str] = []

    def record_query(
        connection: Connection,
        cursor: Cursor,
        statement: str,
        parameters: object,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        statements.append(statement)
        assert "ground_truth" not in statement.lower()

    engine = db_session.get_engine()
    event.listen(engine, "before_cursor_execute", record_query)
    try:
        facts = _facts(foundation_agent)
        hypotheses = gen_observed_pattern(facts, foundation_agent)
        assert hypotheses[0].candidate_ids == ["AR-TEST-MAIN"]
        payload = json.dumps(
            {
                "facts": facts.bt,
                "hypotheses": [
                    hypothesis.model_dump(mode="json") for hypothesis in hypotheses
                ],
                "events": [
                    item.model_dump(mode="json")
                    for item in bus.history(foundation_agent.ctx.run_id)
                ],
            }
        )
        assert canary not in payload
    finally:
        event.remove(engine, "before_cursor_execute", record_query)
    assert all("ground_truth" not in statement.lower() for statement in statements)
    assert any("bank_transactions" in statement for statement in statements)
    assert any("ar_invoices" in statement for statement in statements)
