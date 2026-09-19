# AI Office of the CFO — Architecture Contract

This document is the binding contract for every module. Implementers MUST follow the
interfaces here exactly; integration is done by a separate pass that assumes these names.

## 0. Thesis (what judges must see)
Specialized finance agents (AP/AR, Reconciliation, Close, Forecast, Audit, Report) run under a
CFO orchestrator and share a persistent **Financial Memory Graph**. Every exception, hypothesis,
decision, human correction and learned rule is a node with provenance edges. Agents retrieve
precedent from the graph, so **human corrections in January change behaviour in February**
without retraining. Accuracy is measured against hidden ground truth in the synthetic dataset.

Key differentiators to preserve:
1. Hypothesis → evidence → confidence loop (real multi-step tool use), not a chatbot.
2. Rate-based adjustments (fees, discounts, FX) are NEVER guessed by built-in logic — they must
   come from a learned Rule in memory. That is what makes learning visible.
3. Confidence tiers: HIGH ≥ 0.85 execute; MEDIUM 0.60–0.85 Audit agent re-performs; LOW < 0.60 human.
4. Rule trust grows with confirmations: Jan human-verified → Feb audit-verified → Mar autonomous.
5. Precedent drift: when a rule nearly fits (e.g. fee 2% → 2.5%) the agent flags drift instead of
   forcing the match; human confirms → new Rule version SUPERSEDES old.
6. Teach-once propagation: resolving one exception with a new rule immediately re-runs sibling
   open exceptions in the same period.
7. LLM-optional: with OPENAI_API_KEY the OpenAI Agents SDK proposes novel hypotheses, writes
   narratives and plays skeptic; without it the deterministic engine produces identical
   decisions for all planted cases. Demo never depends on network.

## 1. Repository layout
```
cfo-office/
  ARCHITECTURE.md
  README.md
  Makefile
  scripts/demo.sh
  backend/            (Python 3.12, uv, FastAPI)  -> port 8000
    app/
      config.py        settings (env)                                  [core, done]
      schemas.py       Pydantic models shared everywhere               [core, done]
      events.py        in-process event bus + SSE                      [core, done]
      db/
        models.py      SQLAlchemy tables (financial truth)             [core, done]
        session.py     engine/session helpers                          [core, done]
      data/
        generator.py   synthetic company + planted exceptions + truth  [A]
        seed.py        CLI: python -m app.data.seed [--reset]          [A]
      memory/
        graph.py       GraphStore (embedded, SQLite persisted, networkx) [B]
        neo4j_store.py optional adapter, same interface                [B]
        service.py     MemoryService (rules, precedents, provenance)   [B]
        embeddings.py  local hashed-TFIDF embedder (+OpenAI optional)  [B]
      agents/
        base.py        BaseAgent, AgentContext, Tool registry          [C]
        brain.py       Brain interface + HeuristicBrain + OpenAIBrain  [C]
        hypotheses.py  hypothesis generators + evidence tests + scoring[C]
        tools.py       finance tools (lookup, compute, memory search)  [C]
        cfo.py         orchestrator: period close pipeline             [C]
        ap_ar.py, recon.py, close.py, forecast.py, audit.py, report.py [C]
      api/
        routes_*.py    FastAPI routers                                 [D]
        runs.py        RunManager (background runs, metrics)           [D]
      main.py          FastAPI app factory                             [D]
    tests/                                                             [F]
  frontend/           (Next.js 15 app router, TS, Tailwind)  -> port 3001  [E]
```
Letters = implementation owner. Owners only write inside their files.

## 2. Financial truth (SQLite via SQLAlchemy) — see backend/app/db/models.py
Tables: periods, vendors, customers, employees, gl_accounts, purchase_orders, goods_receipts,
ap_invoices, ar_invoices, payments, bank_transactions, ledger_entries, payroll_runs,
ground_truth. IDs are human-readable strings (V001, C003, PO-2026-01-0007, INV-…, AR-…,
PAY-…, BT-…). Bank amounts are signed: negative = cash out.

`ground_truth` is hidden from agents (never exposed through tools). It stores, per bank
transaction / invoice, the true explanation: `exception_type` ('clean' or a planted type),
`pattern`, `params` (json), `matched_ids` (json list). Metrics compare decisions to it.

Planted exception types (data owner A must produce ALL of these, each period):
| type | description | who resolves |
|---|---|---|
| processor_fee | Stripe payouts arrive net of 3.0% fee | learned Rule (percentage_fee) |
| vendor_fee | CloudSpan invoices paid with +2.0% surcharge (Mar: 2.5% → drift) | learned Rule |
| early_pay_discount | Apex Manufacturing pays AR invoices less 2% | learned Rule |
| wire_fee | intl wires arrive/leave with $25 bank fee | learned Rule (fixed_fee) |
| fx_variance | EUR vendor paid in USD, ±0.5–1.5% variance | learned Rule (fx_tolerance) |
| split_payment | one AP invoice paid in two bank txns | built-in |
| combined_payment | customer pays several AR invoices with one deposit | built-in |
| timing_lag | payment initiated last days of period, clears next period | built-in |
| duplicate_invoice | vendor sends same bill twice w/ different invoice number | built-in (hold) |
| no_po_invoice | AP invoice > $5k without PO | policy: human approval always |
| price_variance | invoice exceeds PO by > 5% | human (Jan) |
| unknown_deposit | deposit with no invoice (overpayment) | human always |
| amount_collision | two open invoices same amount, different vendors (trap) | built-in via counterparty |

Counts per period (Jan/Feb/Mar): processor_fee 3/3/3, vendor_fee 2/2/2(Mar both at 2.5%),
early_pay_discount 2/2/2, wire_fee 2/2/2, fx_variance 1/1/1, split 2/2/2, combined 2/2/2,
timing_lag 2/2/2, duplicate 2/2/2, no_po 1/1/1, price_variance 1/1/1, unknown_deposit 1/1/1,
amount_collision 1/1/1. Everything else is clean and must reconcile exactly.
Scale: ~180–220 bank txns, ~60 AP invoices, ~40 AR invoices, ~45 POs, 2 payroll runs / period.
Generator must be deterministic (seeded RNG) so demos are repeatable.

## 3. Financial Memory Graph — backend/app/memory
Embedded property graph: tables graph_nodes(id, label, props JSON, created_at) and
graph_edges(id, rel, src, dst, props JSON, created_at) in the same SQLite file, mirrored in a
networkx.MultiDiGraph for traversal. `NEO4J_URI` set → Neo4jStore with the same interface.

Node labels: Vendor, Customer, Employee, Account, Period, Transaction, Invoice, PurchaseOrder,
Payment, Exception, Hypothesis, Decision, HumanCorrection, Rule, AuditFinding, AgentRun,
Observation.
Edge types: PAID_TO, BILLED_BY, MATCHES, PARTIALLY_MATCHES, APPROVED_BY, DUPLICATE_OF,
CAUSED_BY, RESOLVED_BY, CORRECTED_BY, USED_PRECEDENT, DERIVED_FROM, VERIFIED_BY, CHALLENGED_BY,
IN_PERIOD, APPLIES_TO, SUPERSEDES, OBSERVED_IN, LEARNED_FROM, PROPOSED_BY, TESTED, HANDED_OFF_TO,
FLAGGED, ABOUT.

Rule node props: pattern_type (percentage_fee | fixed_fee | early_pay_discount | fx_tolerance |
timing_window | price_tolerance | approval_policy | custom), scope_type (vendor|customer|global|
account), scope_id, params (json e.g. {"rate":0.03,"direction":"deduct"}), description,
status (active|superseded), version, trust {times_applied, times_confirmed, times_refuted,
human_verified: bool}, learned_in_period, created_by (human|agent), embedding (list[float]).

### GraphStore interface (memory/graph.py)
```python
class GraphStore:
    def __init__(self, db_path: str): ...
    def add_node(self, label: str, props: dict, node_id: str | None = None) -> str
    def upsert_node(self, node_id: str, label: str, props: dict) -> str   # merge props
    def get_node(self, node_id: str) -> GraphNode | None
    def update_node(self, node_id: str, props: dict) -> None                # merge
    def add_edge(self, src: str, rel: str, dst: str, props: dict | None = None) -> str
    def has_edge(self, src: str, rel: str, dst: str) -> bool
    def edges_of(self, node_id: str, rel: str | None = None, direction: str = "both") -> list[GraphEdge]
    def neighbors(self, node_id: str, rel: str | None = None, direction: str = "out", label: str | None = None) -> list[GraphNode]
    def find_nodes(self, label: str | None = None, **prop_filters) -> list[GraphNode]
    def provenance_chain(self, node_id: str, max_depth: int = 8) -> GraphSubgraph
    def subgraph(self, node_ids: list[str], include_edges: bool = True) -> GraphSubgraph
    def export_cytoscape(self, labels: list[str] | None = None, period_id: str | None = None, limit: int = 600, focus: str | None = None) -> dict  # {"nodes":[{"data":{...}}], "edges":[{"data":{...}}]}
    def stats(self) -> dict     # {"nodes": n, "edges": m, "by_label": {...}, "by_rel": {...}}
    def clear(self) -> None
```
provenance_chain follows, backwards from a node: USED_PRECEDENT, DERIVED_FROM, LEARNED_FROM,
CORRECTED_BY, RESOLVED_BY, CAUSED_BY, VERIFIED_BY, SUPERSEDES, ABOUT, APPLIES_TO, PROPOSED_BY —
and returns nodes + edges in the order traversed (for UI highlighting).

### MemoryService interface (memory/service.py)
```python
class MemoryService:
    def __init__(self, store: GraphStore, embedder: Embedder): ...
    # entity mirrors (idempotent) — id == financial-truth id
    def ensure_entity(self, label: str, entity_id: str, props: dict) -> str
    # exception lifecycle
    def record_exception(self, exc: ExceptionRecord) -> str
    def record_hypothesis(self, exception_id: str, hyp: Hypothesis) -> str
    def record_decision(self, exception_id: str, decision: Decision) -> str
    def record_audit_finding(self, decision_id: str, finding: AuditFinding) -> str
    def record_agent_run(self, run: RunSummary) -> str
    def record_observation(self, scope_type: str, scope_id: str, key: str, value: Any, period_id: str) -> str
    # learning
    def learn_rule(self, spec: RuleSpec, source: str, period_id: str, human_verified: bool) -> str  # source = HumanCorrection or Decision node id; handles SUPERSEDES when same scope+pattern exists
    def record_human_correction(self, exception_id: str, correction: HumanCorrection) -> str
    def confirm_rule(self, rule_id: str, by: str) -> None      # times_confirmed += 1
    def refute_rule(self, rule_id: str, by: str) -> None       # times_refuted += 1
    def mark_applied(self, rule_id: str, decision_id: str) -> None  # times_applied += 1 and USED_PRECEDENT edge
    def rule_trust(self, rule_id: str) -> float                # 0..1 (see §5)
    # retrieval
    def find_precedents(self, query: PrecedentQuery) -> list[PrecedentMatch]
    def active_rules(self, scope_type: str | None = None, scope_id: str | None = None, pattern_type: str | None = None) -> list[GraphNode]
    def observations(self, scope_type: str, scope_id: str, key: str) -> list[GraphNode]
    # views
    def exceptions(self, period_id: str | None = None, status: str | None = None) -> list[dict]
    def exception_detail(self, exception_id: str) -> dict   # exception + hypotheses + decision + audit + corrections + rule links
    def rules(self) -> list[dict]
    def provenance(self, node_id: str) -> dict               # GraphSubgraph.model_dump()
```
find_precedents: candidate rules = active rules whose scope matches (scope_id == counterparty
or global) AND (pattern_type in query.pattern_hints or hints empty); score = 0.55*structural +
0.30*cosine(embedding(query.text), rule.embedding) + 0.15*trust. Return sorted desc.

## 4. Agents — backend/app/agents
Pipeline for `run_period_close(period_id)` (cfo.py), executed by RunManager in a background
task; every step emits events (see §6). Steps:
1. CFO: open run, load period stats, emit plan.
2. AP/AR agent: 3-way match (PO ↔ receipt ↔ invoice), duplicate detection, no-PO policy,
   price variance, AR aging; raises Exceptions for failures.
3. Recon agent: for every unreconciled bank txn: hypothesis loop (see below); exact/built-in
   matches execute; else Exception with hypotheses + confidence tiering.
4. Audit agent: re-performs every MEDIUM decision and a 20% sample of HIGH decisions; challenge/
   response is emitted as audit.challenge / audit.response events; approves (confirm_rule) or
   escalates to human (status → needs_human).
5. Close agent: checklist (unreconciled count, open exceptions, accruals for received-not-
   invoiced POs, evidence completeness); period status → 'pending_review' if human items remain,
   else 'closed'.
6. Forecast agent: 13-week cash forecast from open AR/AP/payroll + memory observations
   (customer days-to-pay, processor fee rules); stores Observation nodes; computes error vs
   actuals for closed periods.
7. Report agent: close report narrative (what changed, why, evidence links, what needs human).
8. CFO: compute RunMetrics vs ground truth, record AgentRun node, emit run.completed.

Hypothesis loop (hypotheses.py) for a bank txn `bt` (or an unpaid invoice):
- generators (in order): exact_match, counterparty_exact (same amount, same counterparty),
  combined_invoices (subset-sum over open invoices of counterparty, ≤4 items), split_payment
  (this txn + another txn = invoice), partial_payment, timing_lag (match in adjacent period),
  duplicate, memory_rules (each applicable Rule → parameterized hypothesis, e.g. invoice*(1-rate)),
  observed_pattern (difference is a round % or round fixed amount → *observation only*, conf ≤ 0.45),
  llm_proposed (OpenAIBrain only; heuristic returns []).
- each Hypothesis has: id, kind, description, params, candidate_ids, evidence: list[Evidence],
  confidence: float, rule_id (if from memory), tested: bool, passed: bool.
- decision = best passed hypothesis; tier by confidence. Execute → mark bank txn reconciled,
  ledger entry, MATCHES edges; record Decision. MEDIUM → decision.status='pending_audit'.
  LOW → Exception.status='needs_human'.
- confidence table (hypotheses.score): exact 0.98; counterparty_exact 0.97; combined 0.92;
  split 0.90; timing_lag 0.88; duplicate 0.95; partial 0.80; rule-based = 0.70 + 0.12*human_verified
  + 0.03*min(times_confirmed,5) - 0.10*times_refuted, capped 0.96; observed_pattern 0.42; none 0.10.
  Drift: rule nearly fits (|implied_rate - rule_rate| ≤ 1.0pp but ≠) → kind='precedent_drift',
  confidence 0.55, description states implied vs known rate.

Human resolution (`resolve_exception` in cfo.py, used by API):
- actions: approve_hypothesis(hypothesis_id) | teach(rule: RuleSpec, explanation) |
  manual_match(matched_ids, explanation) | dismiss(explanation).
- creates HumanCorrection node (+CORRECTED_BY), Rule via learn_rule when teaching, executes the
  match, then **propagates**: re-runs the hypothesis loop for every other open Exception in the
  same period (and any other open period) — returns list of exception ids auto-resolved.

Brain (brain.py):
```python
class Brain(Protocol):
    name: str
    async def propose_hypotheses(self, ctx: dict) -> list[HypothesisSpec]
    async def explain_decision(self, ctx: dict) -> str
    async def parse_correction(self, text: str, ctx: dict) -> RuleSpec | None
    async def audit_challenge(self, ctx: dict) -> AuditVerdict
    async def narrate_report(self, ctx: dict) -> str
def get_brain() -> Brain   # OpenAIBrain if settings.openai_api_key else HeuristicBrain
```
OpenAIBrain uses the `agents` package (OpenAI Agents SDK): one `Agent` per role with function
tools wrapping tools.py; `Runner.run` with structured outputs (pydantic output_type). Must
degrade to HeuristicBrain on any exception (log + event `brain.fallback`). Token usage is
recorded into RunMetrics.llm_tokens / llm_cost_usd.
HeuristicBrain.parse_correction must parse text like "Stripe deducts a 3% processing fee",
"$25 wire fee", "2% early payment discount", "accept up to 1.5% FX variance", "2.5% surcharge".

## 5. Trust & metrics
rule_trust = clamp(0.5 + 0.25*human_verified + 0.05*times_confirmed - 0.15*times_refuted, 0, 1).
RunMetrics (schemas.RunMetrics): period_id, run_id, total_items, auto_resolved, precedent_hits,
human_reviews, audit_challenges, audit_passed, accuracy (vs ground truth over decided items),
coverage (decided/total), avg_steps_per_exception, avg_ms_per_exception, wall_ms, llm_tokens,
llm_cost_usd, forecast_error_pct (None if no actuals), exceptions_by_type {type: count},
human_review_by_type.

## 6. Events (events.py) — SSE at GET /api/runs/{run_id}/events and GET /api/events/stream
AgentEvent {id, run_id, seq, ts, agent, kind, title, detail, data}. kinds:
run.started, run.plan, run.progress, run.completed, run.failed, agent.started, agent.finished,
agent.step, tool.call, tool.result, hypothesis.generated, hypothesis.tested, decision.made,
decision.executed, exception.raised, exception.resolved, memory.read, memory.write,
memory.precedent_hit, handoff, audit.challenge, audit.response, audit.verdict,
human.review_requested, human.correction, propagation, forecast.updated, report.ready,
brain.fallback, close.checklist.
RunManager keeps the full event list per run in memory (and persists to `runs` JSON in the
graph AgentRun node on completion) so /trace can replay it.
Cinematic pacing: settings.step_delay_ms (default 120) sleep between exception investigations
so viewers can watch; NOT counted in avg_ms_per_exception.

## 7. HTTP API (FastAPI, prefix /api, CORS *)
GET  /health
GET  /periods                         -> list[PeriodView]
POST /runs {period_id}                -> {run_id}
GET  /runs                            -> list[RunView]
GET  /runs/{run_id}                   -> RunView (status, metrics, counts)
GET  /runs/{run_id}/events  (SSE)     -> replays past events then streams
GET  /runs/{run_id}/trace             -> list[AgentEvent]
GET  /events/stream (SSE, all runs)
GET  /exceptions?period_id&status     -> list[ExceptionView]
GET  /exceptions/{id}                 -> ExceptionDetail
POST /exceptions/{id}/resolve  ResolveRequest -> ResolveResponse {exception_id, rule_id, propagated: list[str], events: int}
GET  /memory/graph?labels=&period_id=&limit=&focus=  -> cytoscape elements
GET  /memory/nodes/{id}               -> {node, edges, neighbors}
GET  /memory/provenance/{id}          -> GraphSubgraph
GET  /memory/rules                    -> list[RuleView]
GET  /memory/stats
GET  /metrics                         -> list[RunMetrics] (latest per period) + deltas
GET  /forecast?period_id             -> ForecastView
GET  /reports/{period_id}             -> CloseReport
GET  /data/{table}?period_id          -> rows (bank_transactions, ap_invoices, ar_invoices, purchase_orders, payments, ledger_entries)
POST /reset                           -> reseed truth + clear memory
Frontend calls `${NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/...`.

## 8. Frontend (Next.js app router, TS, Tailwind v4, lucide-react, cytoscape, recharts)
Pages: `/` dashboard (period cards + Run buttons, live SSE progress, metrics chart Jan→Feb→Mar,
memory stats), `/exceptions` (queue + detail drawer + Teach form), `/memory` (Cytoscape graph,
node panel, provenance highlight), `/trace` (agent lanes timeline, expandable steps, audit
challenge/response), `/forecast`, `/reports`. Dark "finance terminal" aesthetic, polished.
Shared: src/lib/api.ts (typed client, mirrors §7 + schemas), src/lib/sse.ts, src/components/ui/*.

## 9. Demo script (README)
1 Run January → watch counts → 2 open Stripe exception (42%) → Teach "3% processing fee" →
graph animates new Rule, sibling exceptions propagate → 3 Run February → precedent hits, audit
verifies → 4 metrics chart → 5 Run March → drift detection on CloudSpan 2.5% → 6 click a March
decision in /memory → provenance chain highlights back to the January human correction.
