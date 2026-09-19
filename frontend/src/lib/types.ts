/**
 * TypeScript mirrors of `backend/app/schemas.py`.
 *
 * Every shape here is exactly what the FastAPI backend returns (or accepts).
 * Datetimes arrive as ISO-8601 strings. Keep this file in sync with the
 * Pydantic models — do not invent fields the backend does not send.
 */

// ---------------------------------------------------------------- enums

/** Confidence tier: HIGH ≥ 0.85 executes, MEDIUM 0.60–0.85 is audited, LOW < 0.60 goes to a human. */
export type Tier = "high" | "medium" | "low";

export type ExceptionStatus =
  | "open"
  | "pending_audit"
  | "needs_human"
  | "resolved"
  | "dismissed";

export type DecisionStatus =
  | "proposed"
  | "pending_audit"
  | "executed"
  | "rejected"
  | "escalated";

export type PatternType =
  | "percentage_fee"
  | "fixed_fee"
  | "early_pay_discount"
  | "fx_tolerance"
  | "timing_window"
  | "price_tolerance"
  | "approval_policy"
  | "custom";

export type ScopeType = "vendor" | "customer" | "account" | "global";

export type HypothesisKind =
  | "exact_match"
  | "counterparty_exact"
  | "combined_invoices"
  | "split_payment"
  | "partial_payment"
  | "timing_lag"
  | "duplicate"
  | "rule_percentage_fee"
  | "rule_fixed_fee"
  | "rule_early_pay_discount"
  | "rule_fx_tolerance"
  | "rule_price_tolerance"
  | "precedent_drift"
  | "observed_pattern"
  | "llm_proposed"
  | "policy_no_po"
  | "policy_price_variance"
  | "unknown"
  | "payroll"
  | "bank_fee";

export type ExceptionType =
  | "processor_fee"
  | "vendor_fee"
  | "early_pay_discount"
  | "wire_fee"
  | "fx_variance"
  | "split_payment"
  | "combined_payment"
  | "timing_lag"
  | "duplicate_invoice"
  | "no_po_invoice"
  | "price_variance"
  | "unknown_deposit"
  | "amount_collision"
  | "clean";

/** Agent identifiers used in `AgentEvent.agent`, `Decision.agent`, etc. */
export type AgentId =
  | "cfo"
  | "ap_ar"
  | "recon"
  | "audit"
  | "close"
  | "forecast"
  | "report"
  | "human"
  | "system";

/** Event kinds emitted on the SSE stream (ARCHITECTURE §6). */
export type EventKind =
  | "run.started"
  | "run.plan"
  | "run.progress"
  | "run.completed"
  | "run.failed"
  | "agent.started"
  | "agent.finished"
  | "agent.step"
  | "tool.call"
  | "tool.result"
  | "hypothesis.generated"
  | "hypothesis.tested"
  | "decision.made"
  | "decision.executed"
  | "exception.raised"
  | "exception.resolved"
  | "memory.read"
  | "memory.write"
  | "memory.precedent_hit"
  | "handoff"
  | "audit.challenge"
  | "audit.response"
  | "audit.verdict"
  | "human.review_requested"
  | "human.correction"
  | "propagation"
  | "forecast.updated"
  | "report.ready"
  | "brain.fallback"
  | "close.checklist";

export type RunStatus = "queued" | "running" | "completed" | "failed";

/** Loosely-typed JSON object (mirrors `dict[str, Any]`). */
export type JsonObject = Record<string, unknown>;

// ---------------------------------------------------------------- graph primitives

export interface GraphNode {
  id: string;
  label: string;
  props: JsonObject;
}

export interface GraphEdge {
  id: string;
  rel: string;
  src: string;
  dst: string;
  props: JsonObject;
}

export interface GraphSubgraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Traversal order of node ids (for provenance highlighting). */
  order: string[];
  root: string | null;
}

/** Cytoscape.js element shapes returned by GET /api/memory/graph. */
export interface CytoscapeNodeData {
  id: string;
  label: string;
  [key: string]: unknown;
}

export interface CytoscapeEdgeData {
  id: string;
  source: string;
  target: string;
  rel?: string;
  [key: string]: unknown;
}

export interface CytoscapeElements {
  nodes: { data: CytoscapeNodeData }[];
  edges: { data: CytoscapeEdgeData }[];
}

/** GET /api/memory/nodes/{id} */
export interface NodeDetail {
  node: GraphNode;
  edges: GraphEdge[];
  neighbors: GraphNode[];
}

/** GET /api/memory/stats (GraphStore.stats) */
export interface MemoryStats {
  nodes: number;
  edges: number;
  by_label: Record<string, number>;
  by_rel: Record<string, number>;
  /** Convenience counters some backends add; treat as optional. */
  rules?: number;
  corrections?: number;
  [key: string]: unknown;
}

// ---------------------------------------------------------------- reasoning objects

export interface Evidence {
  kind: string;
  description: string;
  supports: boolean;
  weight: number;
  data: JsonObject;
}

export interface HypothesisSpec {
  kind: string;
  description: string;
  params: JsonObject;
  candidate_ids: string[];
  rule_id: string | null;
}

export interface Hypothesis {
  id: string;
  kind: HypothesisKind | string;
  description: string;
  params: JsonObject;
  candidate_ids: string[];
  evidence: Evidence[];
  confidence: number;
  rule_id: string | null;
  tested: boolean;
  passed: boolean;
  generated_by: "engine" | "memory" | "llm" | "human" | string;
  steps: number;
}

export interface Decision {
  id: string;
  exception_id: string;
  hypothesis_id: string | null;
  action: "reconcile" | "hold" | "void" | "approve" | "escalate" | "dismiss" | string;
  matched_ids: string[];
  confidence: number;
  tier: Tier;
  status: DecisionStatus;
  explanation: string;
  agent: AgentId | string;
  rule_id: string | null;
  steps: number;
  ms: number;
  period_id: string;
  run_id: string | null;
  created_at: string;
  executed_at: string | null;
}

export interface RuleSpec {
  pattern_type: PatternType;
  scope_type: ScopeType;
  scope_id?: string | null;
  /** e.g. {"rate":0.03,"direction":"deduct"} | {"amount":25} | {"tolerance_pct":0.015} | {"days":7} */
  params: JsonObject;
  description: string;
}

export interface HumanCorrection {
  id: string;
  exception_id: string;
  action: "approve_hypothesis" | "teach" | "manual_match" | "dismiss" | "reject_decision" | string;
  explanation: string;
  rule: RuleSpec | null;
  hypothesis_id: string | null;
  matched_ids: string[];
  by: string;
  created_at: string;
}

export interface RuleTrust {
  times_applied: number;
  times_confirmed: number;
  times_refuted: number;
  human_verified: boolean;
}

export interface RuleView {
  id: string;
  pattern_type: PatternType | string;
  scope_type: ScopeType | string;
  scope_id: string | null;
  scope_name: string | null;
  params: JsonObject;
  description: string;
  status: "active" | "superseded" | string;
  version: number;
  trust: RuleTrust;
  trust_score: number;
  learned_in_period: string | null;
  created_by: "human" | "agent" | string;
  source_id: string | null;
  supersedes: string | null;
  created_at: string | null;
}

export interface AuditCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface AuditVerdict {
  verdict: "approved" | "escalated" | "rejected";
  reasoning: string;
  checks: AuditCheck[];
}

export interface AuditFinding {
  id: string;
  decision_id: string;
  exception_id: string;
  challenge: string;
  response: string;
  verdict: string;
  reasoning: string;
  checks: AuditCheck[];
  agent: string;
  created_at: string;
}

export interface PrecedentMatch {
  rule_id: string;
  rule: JsonObject;
  score: number;
  structural: number;
  semantic: number;
  trust: number;
}

// ---------------------------------------------------------------- runs / metrics / events

export interface RunMetrics {
  period_id: string;
  run_id: string;
  total_items: number;
  exceptions_raised: number;
  auto_resolved: number;
  precedent_hits: number;
  human_reviews: number;
  audit_challenges: number;
  audit_passed: number;
  /** decided items correct / decided items (0..1) */
  accuracy: number | null;
  /** decided items / total (0..1) */
  coverage: number | null;
  avg_steps_per_exception: number | null;
  avg_ms_per_exception: number | null;
  wall_ms: number;
  llm_tokens: number;
  llm_cost_usd: number;
  forecast_error_pct: number | null;
  exceptions_by_type: Record<string, number>;
  human_review_by_type: Record<string, number>;
  correct_by_type: Record<string, number>;
  rules_learned: number;
  rules_used: number;
}

/** Live counters carried on RunView.counts and run.progress events. */
export interface RunCounts {
  reconciled?: number;
  matched?: number;
  exceptions?: number;
  human?: number;
  precedent_hits?: number;
  [key: string]: number | undefined;
}

export interface RunView {
  run_id: string;
  period_id: string;
  status: RunStatus | string;
  brain: string;
  started_at: string | null;
  finished_at: string | null;
  counts: RunCounts;
  metrics: RunMetrics | null;
  error: string | null;
  current_step: string | null;
}

export interface AgentEvent {
  id: string;
  run_id: string | null;
  seq: number;
  ts: string;
  agent: AgentId | string;
  kind: EventKind | string;
  title: string;
  detail: string;
  data: JsonObject;
}

export interface PeriodStats {
  bank_transactions?: number;
  ap_invoices?: number;
  ar_invoices?: number;
  unreconciled?: number;
  purchase_orders?: number;
  [key: string]: unknown;
}

export interface PeriodView {
  id: string;
  name: string;
  start_date: string;
  end_date: string;
  /** open | running | pending_review | closed (free-form on the backend) */
  status: string;
  stats: PeriodStats;
  last_run_id: string | null;
}

// ---------------------------------------------------------------- API views

export interface ExceptionView {
  id: string;
  period_id: string;
  run_id: string | null;
  entity_type: "bank_transaction" | "ap_invoice" | "ar_invoice" | "purchase_order" | string;
  entity_id: string;
  counterparty_type: "vendor" | "customer" | "bank" | "payroll" | string | null;
  counterparty_id: string | null;
  counterparty_name: string | null;
  category: string;
  title: string;
  description: string;
  amount: number | null;
  expected_amount: number | null;
  difference: number | null;
  status: ExceptionStatus | string;
  confidence: number;
  tier: Tier | string | null;
  agent: AgentId | string;
  decision_id: string | null;
  tags: string[];
  created_at: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
  best_hypothesis: JsonObject | null;
  ground_truth_type: string | null;
}

export interface ExceptionDetail {
  exception: ExceptionView;
  hypotheses: Hypothesis[];
  decision: Decision | null;
  audit: AuditFinding[];
  corrections: HumanCorrection[];
  rules_used: RuleView[];
  /** The underlying bank txn / invoice row. */
  entity: JsonObject;
  /** Candidate invoices / txns. */
  related: JsonObject[];
  trace: AgentEvent[];
}

export type ResolveAction = "approve_hypothesis" | "teach" | "manual_match" | "dismiss";

export interface ResolveRequest {
  action: ResolveAction;
  hypothesis_id?: string | null;
  rule?: RuleSpec | null;
  explanation?: string;
  matched_ids?: string[];
  by?: string;
}

export interface ResolveResponse {
  exception_id: string;
  status: string;
  decision_id: string | null;
  correction_id: string | null;
  rule_id: string | null;
  /** Sibling exception ids auto-resolved by propagation. */
  propagated: string[];
  events: number;
  message: string;
}

export interface ForecastWeek {
  week_start: string;
  inflows: number;
  outflows: number;
  net: number;
  ending_cash: number;
  /** ar_collections, ap_payments, payroll, fees, ... */
  detail: Record<string, number>;
}

export interface ForecastAssumption {
  source: "rule" | "observation" | "default" | string;
  description: string;
  node_id?: string | null;
  value?: unknown;
  [key: string]: unknown;
}

export interface ForecastView {
  period_id: string;
  as_of: string;
  opening_cash: number;
  weeks: ForecastWeek[];
  assumptions: ForecastAssumption[];
  error_pct: number | null;
  actual_vs_forecast: JsonObject[];
  memory_adjustment_total: number;
}

export interface ChecklistItem {
  name: string;
  status: "done" | "warning" | "blocked";
  detail: string;
  count: number | null;
}

export interface ReportSection {
  title: string;
  body: string;
  /** Node ids to link into /memory. */
  evidence_ids: string[];
  data: JsonObject;
}

export interface CloseReport {
  period_id: string;
  run_id: string | null;
  status: string;
  summary: string;
  sections: ReportSection[];
  checklist: ChecklistItem[];
  open_items: JsonObject[];
  metrics: RunMetrics | null;
  generated_at: string;
}

export interface MetricsResponse {
  /** Latest run metrics per period, in period order. */
  periods: RunMetrics[];
  /** Period-over-period deltas; shape is backend-defined. */
  deltas: JsonObject;
  history: RunMetrics[];
}

/** GET /api/health */
export interface HealthResponse {
  status: "ok" | string;
  /** "openai" | "heuristic" (free-form) */
  brain: string;
  llm_available: boolean;
  company: string;
  version: string;
}

/** POST /api/runs */
export interface CreateRunResponse {
  run_id: string;
}

/** POST /api/reset */
export interface ResetResponse {
  status?: string;
  message?: string;
  [key: string]: unknown;
}

export type DataTable =
  | "bank_transactions"
  | "ap_invoices"
  | "ar_invoices"
  | "purchase_orders"
  | "payments"
  | "ledger_entries";
