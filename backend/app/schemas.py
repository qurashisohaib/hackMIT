"""Shared Pydantic models. Every module imports from here — do not redefine these shapes."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- enums
class Tier(str, Enum):
    HIGH = "high"      # execute autonomously
    MEDIUM = "medium"  # audit agent re-performs
    LOW = "low"        # human review


class ExceptionStatus(str, Enum):
    OPEN = "open"                    # investigation in progress
    PENDING_AUDIT = "pending_audit"  # medium confidence, awaiting audit
    NEEDS_HUMAN = "needs_human"      # low confidence or policy
    RESOLVED = "resolved"            # executed (auto, audit-approved, or human)
    DISMISSED = "dismissed"


class DecisionStatus(str, Enum):
    PROPOSED = "proposed"
    PENDING_AUDIT = "pending_audit"
    EXECUTED = "executed"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class PatternType(str, Enum):
    PERCENTAGE_FEE = "percentage_fee"
    FIXED_FEE = "fixed_fee"
    EARLY_PAY_DISCOUNT = "early_pay_discount"
    FX_TOLERANCE = "fx_tolerance"
    TIMING_WINDOW = "timing_window"
    PRICE_TOLERANCE = "price_tolerance"
    APPROVAL_POLICY = "approval_policy"
    CUSTOM = "custom"


class ScopeType(str, Enum):
    VENDOR = "vendor"
    CUSTOMER = "customer"
    ACCOUNT = "account"
    GLOBAL = "global"


# Hypothesis kinds produced by app.agents.hypotheses (keep in sync with ground_truth.pattern)
HypothesisKind = Literal[
    "exact_match", "counterparty_exact", "combined_invoices", "split_payment", "partial_payment",
    "timing_lag", "duplicate", "rule_percentage_fee", "rule_fixed_fee", "rule_early_pay_discount",
    "rule_fx_tolerance", "rule_price_tolerance", "precedent_drift", "observed_pattern",
    "llm_proposed", "policy_no_po", "policy_price_variance", "unknown", "payroll", "bank_fee",
]

EXCEPTION_TYPES = [
    "processor_fee", "vendor_fee", "early_pay_discount", "wire_fee", "fx_variance", "split_payment",
    "combined_payment", "timing_lag", "duplicate_invoice", "no_po_invoice", "price_variance",
    "unknown_deposit", "amount_collision", "clean",
]


# ---------------------------------------------------------------- graph primitives
class GraphNode(BaseModel):
    id: str
    label: str
    props: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    id: str
    rel: str
    src: str
    dst: str
    props: dict[str, Any] = Field(default_factory=dict)


class GraphSubgraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)  # traversal order of node ids (for highlighting)
    root: str | None = None


# ---------------------------------------------------------------- reasoning objects
class Evidence(BaseModel):
    kind: str                      # amount_match | counterparty_match | rule_fit | timing | subset_sum | memo_reference | ...
    description: str
    supports: bool = True
    weight: float = 1.0
    data: dict[str, Any] = Field(default_factory=dict)


class HypothesisSpec(BaseModel):
    """What a Brain proposes before evidence testing."""
    kind: str
    description: str
    params: dict[str, Any] = Field(default_factory=dict)
    candidate_ids: list[str] = Field(default_factory=list)
    rule_id: str | None = None


class Hypothesis(BaseModel):
    id: str = Field(default_factory=lambda: new_id("HYP"))
    kind: str
    description: str
    params: dict[str, Any] = Field(default_factory=dict)
    candidate_ids: list[str] = Field(default_factory=list)   # invoice/txn ids this would match
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.0
    rule_id: str | None = None
    tested: bool = False
    passed: bool = False
    generated_by: str = "engine"   # engine | memory | llm | human
    steps: int = 0                 # tool calls consumed while testing


class Decision(BaseModel):
    id: str = Field(default_factory=lambda: new_id("DEC"))
    exception_id: str
    hypothesis_id: str | None = None
    action: str                                  # reconcile | hold | void | approve | escalate | dismiss
    matched_ids: list[str] = Field(default_factory=list)
    confidence: float
    tier: Tier
    status: DecisionStatus = DecisionStatus.PROPOSED
    explanation: str = ""
    agent: str = "recon"
    rule_id: str | None = None
    steps: int = 0
    ms: float = 0.0
    period_id: str
    run_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    executed_at: datetime | None = None


class ExceptionRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("EXC"))
    period_id: str
    run_id: str | None = None
    entity_type: str                 # bank_transaction | ap_invoice | ar_invoice | purchase_order
    entity_id: str
    counterparty_type: str | None = None   # vendor | customer | bank | payroll
    counterparty_id: str | None = None
    counterparty_name: str | None = None
    category: str = "unexplained"    # unexplained_difference | unmatched_txn | duplicate | policy | price_variance | unknown_deposit
    title: str
    description: str = ""
    amount: float | None = None      # observed
    expected_amount: float | None = None
    difference: float | None = None
    status: ExceptionStatus = ExceptionStatus.OPEN
    confidence: float = 0.0
    tier: Tier | None = None
    agent: str = "recon"
    hypothesis_ids: list[str] = Field(default_factory=list)
    decision_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: datetime | None = None
    resolved_by: str | None = None   # agent:<name> | audit | human:<name> | propagation


class RuleSpec(BaseModel):
    pattern_type: PatternType
    scope_type: ScopeType
    scope_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)  # {"rate":0.03,"direction":"deduct"} | {"amount":25} | {"tolerance_pct":0.015} | {"days":7}
    description: str


class HumanCorrection(BaseModel):
    id: str = Field(default_factory=lambda: new_id("HC"))
    exception_id: str
    action: str                          # approve_hypothesis | teach | manual_match | dismiss | reject_decision
    explanation: str
    rule: RuleSpec | None = None
    hypothesis_id: str | None = None
    matched_ids: list[str] = Field(default_factory=list)
    by: str = "controller"
    created_at: datetime = Field(default_factory=utcnow)


class RuleTrust(BaseModel):
    times_applied: int = 0
    times_confirmed: int = 0
    times_refuted: int = 0
    human_verified: bool = False


class RuleView(BaseModel):
    id: str
    pattern_type: str
    scope_type: str
    scope_id: str | None
    scope_name: str | None = None
    params: dict[str, Any]
    description: str
    status: str
    version: int
    trust: RuleTrust
    trust_score: float
    learned_in_period: str | None
    created_by: str
    source_id: str | None = None
    supersedes: str | None = None
    created_at: str | None = None


class AuditCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class AuditVerdict(BaseModel):
    verdict: Literal["approved", "escalated", "rejected"]
    reasoning: str
    checks: list[AuditCheck] = Field(default_factory=list)


class AuditFinding(BaseModel):
    id: str = Field(default_factory=lambda: new_id("AUD"))
    decision_id: str
    exception_id: str
    challenge: str
    response: str
    verdict: str
    reasoning: str = ""
    checks: list[AuditCheck] = Field(default_factory=list)
    agent: str = "audit"
    created_at: datetime = Field(default_factory=utcnow)


class PrecedentQuery(BaseModel):
    counterparty_type: str | None = None
    counterparty_id: str | None = None
    pattern_hints: list[str] = Field(default_factory=list)
    amount: float | None = None
    expected_amount: float | None = None
    text: str = ""
    period_id: str | None = None
    limit: int = 5


class PrecedentMatch(BaseModel):
    rule_id: str
    rule: dict[str, Any]
    score: float
    structural: float
    semantic: float
    trust: float


class Observation(BaseModel):
    scope_type: str
    scope_id: str
    key: str            # avg_days_to_pay | fee_rate | payment_day_of_month | ...
    value: Any
    period_id: str
    confidence: float = 0.8


# ---------------------------------------------------------------- runs / metrics / events
class RunMetrics(BaseModel):
    period_id: str
    run_id: str
    total_items: int = 0
    exceptions_raised: int = 0
    auto_resolved: int = 0
    precedent_hits: int = 0
    human_reviews: int = 0
    audit_challenges: int = 0
    audit_passed: int = 0
    accuracy: float | None = None       # decided items correct / decided items
    coverage: float | None = None       # decided items / total
    avg_steps_per_exception: float | None = None
    avg_ms_per_exception: float | None = None
    wall_ms: float = 0.0
    llm_tokens: int = 0
    llm_cost_usd: float = 0.0
    forecast_error_pct: float | None = None
    exceptions_by_type: dict[str, int] = Field(default_factory=dict)
    human_review_by_type: dict[str, int] = Field(default_factory=dict)
    correct_by_type: dict[str, int] = Field(default_factory=dict)
    rules_learned: int = 0
    rules_used: int = 0


class RunView(BaseModel):
    run_id: str
    period_id: str
    status: str                 # queued|running|completed|failed
    brain: str = "heuristic"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    counts: dict[str, int] = Field(default_factory=dict)   # live counters: reconciled, matched, exceptions, human, precedent_hits
    metrics: RunMetrics | None = None
    error: str | None = None
    current_step: str | None = None


class RunSummary(RunView):
    pass


class AgentEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("EV"))
    run_id: str | None
    seq: int = 0
    ts: datetime = Field(default_factory=utcnow)
    agent: str            # cfo | ap_ar | recon | audit | close | forecast | report | human | system
    kind: str             # see ARCHITECTURE §6
    title: str
    detail: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class PeriodView(BaseModel):
    id: str
    name: str
    start_date: str
    end_date: str
    status: str
    stats: dict[str, Any] = Field(default_factory=dict)
    last_run_id: str | None = None


# ---------------------------------------------------------------- API views
class ExceptionView(BaseModel):
    id: str
    period_id: str
    run_id: str | None
    entity_type: str
    entity_id: str
    counterparty_type: str | None
    counterparty_id: str | None
    counterparty_name: str | None
    category: str
    title: str
    description: str
    amount: float | None
    expected_amount: float | None
    difference: float | None
    status: str
    confidence: float
    tier: str | None
    agent: str
    decision_id: str | None
    tags: list[str] = Field(default_factory=list)
    created_at: str | None = None
    resolved_at: str | None = None
    resolved_by: str | None = None
    best_hypothesis: dict[str, Any] | None = None
    ground_truth_type: str | None = None   # only populated for demo transparency (metrics view), may be None


class ExceptionDetail(BaseModel):
    exception: ExceptionView
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    decision: Decision | None = None
    audit: list[AuditFinding] = Field(default_factory=list)
    corrections: list[HumanCorrection] = Field(default_factory=list)
    rules_used: list[RuleView] = Field(default_factory=list)
    entity: dict[str, Any] = Field(default_factory=dict)          # the bank txn / invoice row
    related: list[dict[str, Any]] = Field(default_factory=list)   # candidate invoices/txns
    trace: list[AgentEvent] = Field(default_factory=list)


class ResolveRequest(BaseModel):
    action: Literal["approve_hypothesis", "teach", "manual_match", "dismiss"]
    hypothesis_id: str | None = None
    rule: RuleSpec | None = None
    explanation: str = ""
    matched_ids: list[str] = Field(default_factory=list)
    by: str = "controller"


class ResolveResponse(BaseModel):
    exception_id: str
    status: str
    decision_id: str | None = None
    correction_id: str | None = None
    rule_id: str | None = None
    propagated: list[str] = Field(default_factory=list)   # sibling exception ids auto-resolved
    events: int = 0
    message: str = ""


class ForecastWeek(BaseModel):
    week_start: str
    inflows: float
    outflows: float
    net: float
    ending_cash: float
    detail: dict[str, float] = Field(default_factory=dict)  # ar_collections, ap_payments, payroll, fees, ...


class ForecastView(BaseModel):
    period_id: str
    as_of: str
    opening_cash: float
    weeks: list[ForecastWeek]
    assumptions: list[dict[str, Any]]     # {source: rule|observation|default, description, node_id, value}
    error_pct: float | None = None
    actual_vs_forecast: list[dict[str, Any]] = Field(default_factory=list)
    memory_adjustment_total: float = 0.0  # $ impact of memory-derived assumptions


class ChecklistItem(BaseModel):
    name: str
    status: Literal["done", "warning", "blocked"]
    detail: str = ""
    count: int | None = None


class ReportSection(BaseModel):
    title: str
    body: str
    evidence_ids: list[str] = Field(default_factory=list)  # node ids to link into /memory
    data: dict[str, Any] = Field(default_factory=dict)


class CloseReport(BaseModel):
    period_id: str
    run_id: str | None
    status: str
    summary: str
    sections: list[ReportSection]
    checklist: list[ChecklistItem]
    open_items: list[dict[str, Any]] = Field(default_factory=list)
    metrics: RunMetrics | None = None
    generated_at: str


class MetricsResponse(BaseModel):
    periods: list[RunMetrics]
    deltas: dict[str, Any] = Field(default_factory=dict)
    history: list[RunMetrics] = Field(default_factory=list)
