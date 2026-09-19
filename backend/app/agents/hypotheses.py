"""Hypothesis → evidence → confidence engine (ARCHITECTURE §4).

For a bank transaction (or an AP invoice) the engine resolves the counterparty, runs the
hypothesis generators in the contractual order, tests each one against evidence, picks the
best passed hypothesis, tiers it by confidence and either executes it, parks it for the Audit
agent or raises it for a human. Rate-based adjustments (fees, discounts, FX) are never guessed:
they only pass when a learned Rule in memory explains them; otherwise the engine records an
*observation* (≤ 0.42) and asks a human.

Public entry points: `investigate_bank_txn`, `investigate_ap_invoice`, `execute_decision`,
`reinvestigate_exception`, `score`, `tier`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from app.agents.base import AgentContext, BaseAgent, jsonable
from app.agents.tools import GL_BANK_FEES, GL_CASH, GL_DISCOUNTS, GL_FX, GL_PAYROLL, GL_PAYROLL_TAX, GL_AP, GL_AR
from app.config import settings
from app.schemas import (
    Decision,
    DecisionStatus,
    Evidence,
    ExceptionRecord,
    ExceptionStatus,
    Hypothesis,
    HypothesisSpec,
    Tier,
)

log = logging.getLogger(__name__)

#: Confidence table (ARCHITECTURE §4) for non-rule hypotheses.
BASE_SCORES: dict[str, float] = {
    "exact_match": 0.98,
    "counterparty_exact": 0.97,
    "payroll": 0.97,
    "duplicate": 0.95,
    "bank_fee": 0.95,
    "combined_invoices": 0.92,
    "split_payment": 0.90,
    "timing_lag": 0.88,
    "partial_payment": 0.80,
    "llm_proposed": 0.80,
    "precedent_drift": 0.55,
    "observed_pattern": 0.42,
    "policy_no_po": 0.30,
    "policy_price_variance": 0.30,
    "unknown": 0.10,
}
RULE_CAP = 0.96
LLM_CAP = 0.80
OBSERVED_FX_SCORE = 0.35

#: Kinds that are "trivially clean": at HIGH confidence they execute without an Exception.
TRIVIAL_KINDS = frozenset({"exact_match", "counterparty_exact", "combined_invoices", "split_payment", "payroll", "bank_fee", "timing_lag"})
RULE_KIND: dict[str, str] = {
    "percentage_fee": "rule_percentage_fee",
    "fixed_fee": "rule_fixed_fee",
    "early_pay_discount": "rule_early_pay_discount",
    "fx_tolerance": "rule_fx_tolerance",
    "price_tolerance": "rule_price_tolerance",
}
ADJUSTMENT_ACCOUNT: dict[str, str] = {"fee": GL_BANK_FEES, "discount": GL_DISCOUNTS, "fx": GL_FX}
AMOUNT_TOL = 0.01
RULE_TOL = 0.02
DRIFT_PP = 0.01           # 1.0 percentage point
DRIFT_FIXED_USD = 25.0
MAX_OBSERVED_RATE = 0.15
MAX_OBSERVED_FIXED = 100.0
IN_TRANSIT_DAYS = 10


# ============================================================================ scoring
def rule_confidence(trust: dict[str, Any] | None) -> float:
    """Rule-based confidence: 0.70 + 0.12·human_verified + 0.03·min(confirmed,5) − 0.10·refuted, ≤ 0.96."""
    trust = trust or {}
    conf = (
        0.70
        + 0.12 * (1 if trust.get("human_verified") else 0)
        + 0.03 * min(int(trust.get("times_confirmed", 0) or 0), 5)
        - 0.10 * int(trust.get("times_refuted", 0) or 0)
    )
    return round(max(0.10, min(RULE_CAP, conf)), 4)


def score(kind: str, **ctx: Any) -> float:
    """Confidence for a hypothesis kind per ARCHITECTURE §4.

    Keyword context: `trust` (rule trust dict) for rule_* kinds, `counterparty_resolved`
    (False lowers counterparty_exact to 0.75), `fx_only` for observed_pattern on FX noise.
    """
    if kind.startswith("rule_"):
        return rule_confidence(ctx.get("trust"))
    if kind == "counterparty_exact" and ctx.get("counterparty_resolved") is False:
        return 0.75
    if kind == "observed_pattern" and ctx.get("fx_only"):
        return OBSERVED_FX_SCORE
    return BASE_SCORES.get(kind, BASE_SCORES["unknown"])


def tier(conf: float) -> Tier:
    """Map a confidence to HIGH / MEDIUM / LOW using the configured thresholds."""
    if conf >= settings.high_threshold:
        return Tier.HIGH
    if conf >= settings.medium_threshold:
        return Tier.MEDIUM
    return Tier.LOW


def hypothesis_tier(h: Hypothesis) -> Tier:
    """Tier of a hypothesis honouring a policy-forced tier (`params.force_tier`)."""
    forced = h.params.get("force_tier")
    if forced:
        return Tier(forced)
    return tier(h.confidence)


# ============================================================================ result object
@dataclass
class Investigation:
    """Everything the engine learned about one entity."""

    entity_type: str
    entity_id: str
    period_id: str
    entity: dict[str, Any] = field(default_factory=dict)
    counterparty: dict[str, Any] = field(default_factory=dict)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    best: Hypothesis | None = None
    decision: Decision | None = None
    exception: ExceptionRecord | None = None
    status: str = "open"          # reconciled | approved | clean | pending_audit | needs_human | held | skipped | already_reconciled | missing
    matched_ids: list[str] = field(default_factory=list)
    auto_resolved: bool = False
    executed: bool = False
    steps: int = 0
    ms: float = 0.0

    @property
    def tier(self) -> Tier | None:
        return hypothesis_tier(self.best) if self.best else None

    def to_dict(self) -> dict[str, Any]:
        """JSON-able summary for events / API."""
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "period_id": self.period_id,
            "counterparty": jsonable(self.counterparty),
            "status": self.status,
            "best": self.best.model_dump(mode="json") if self.best else None,
            "hypotheses": [_hyp_summary(h) for h in self.hypotheses],
            "decision_id": self.decision.id if self.decision else None,
            "exception_id": self.exception.id if self.exception else None,
            "matched_ids": list(self.matched_ids),
            "auto_resolved": self.auto_resolved,
            "executed": self.executed,
            "tier": self.tier.value if self.tier else None,
            "confidence": self.best.confidence if self.best else 0.0,
            "steps": self.steps,
            "ms": round(self.ms, 1),
        }


# ============================================================================ small helpers
def _money(x: float | None) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def _ev(kind: str, description: str, supports: bool = True, weight: float = 1.0, **data: Any) -> Evidence:
    return Evidence(kind=kind, description=description, supports=supports, weight=weight, data=jsonable(data))


def _hyp(kind: str, description: str, *, candidate_ids: list[str], evidence: list[Evidence], passed: bool, confidence: float | None = None, params: dict[str, Any] | None = None, rule_id: str | None = None, generated_by: str = "engine") -> Hypothesis:
    return Hypothesis(
        kind=kind,
        description=description,
        params=jsonable(params or {}),
        candidate_ids=list(candidate_ids),
        evidence=evidence,
        confidence=round(confidence if confidence is not None else (score(kind) if passed else 0.0), 4),
        rule_id=rule_id,
        tested=True,
        passed=passed,
        generated_by=generated_by,
    )


def _hyp_summary(h: Hypothesis) -> dict[str, Any]:
    return {
        "id": h.id,
        "kind": h.kind,
        "description": h.description,
        "confidence": h.confidence,
        "passed": h.passed,
        "candidate_ids": list(h.candidate_ids),
        "rule_id": h.rule_id,
        "generated_by": h.generated_by,
        "evidence": [{"kind": e.kind, "supports": e.supports, "description": e.description} for e in h.evidence],
    }


def _cand_summary(c: dict[str, Any]) -> dict[str, Any]:
    return {k: c.get(k) for k in ("id", "kind", "amount", "remaining", "expected_usd", "currency", "date", "number", "counterparty_id", "component") if k in c}


def _matched_ids(cands: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for c in cands:
        for i in [c["id"], *c.get("linked_ids", [])]:
            if i not in out:
                out.append(i)
    return out


def _norm_rate(value: Any) -> float:
    """Accept 0.03, 3 or "3%" style rates and return a fraction."""
    try:
        if isinstance(value, str):
            value = value.strip().rstrip("%")
        rate = float(value)
    except (TypeError, ValueError):
        return 0.0
    return rate / 100.0 if rate > 1.0 else rate


def _mem(ctx: AgentContext, method: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    """Call a MemoryService method defensively (memory outages never stop a close)."""
    mem = ctx.memory
    if mem is None:
        return default
    fn = getattr(mem, method, None)
    if fn is None:
        return default
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("memory.%s failed: %s", method, exc, exc_info=True)
        return default


def _add_edge(ctx: AgentContext, src: str, rel: str, dst: str, props: dict[str, Any] | None = None) -> None:
    store = getattr(ctx.memory, "store", None)
    if store is None or not hasattr(store, "add_edge"):
        return
    try:
        if hasattr(store, "has_edge") and store.has_edge(src, rel, dst):
            return
        store.add_edge(src, rel, dst, props or {})
    except Exception as exc:  # noqa: BLE001
        log.warning("graph edge %s-%s->%s failed: %s", src, rel, dst, exc)


def _rule_info(ctx: AgentContext, node: Any) -> dict[str, Any]:
    """Flatten a Rule GraphNode into a dict with id, params, trust and trust_score."""
    props = dict(getattr(node, "props", {}) or {})
    rule_id = getattr(node, "id", props.get("id"))
    trust = dict(props.get("trust") or {})
    info = {
        "id": rule_id,
        "pattern_type": props.get("pattern_type"),
        "scope_type": props.get("scope_type"),
        "scope_id": props.get("scope_id"),
        "params": dict(props.get("params") or {}),
        "description": props.get("description", ""),
        "version": props.get("version", 1),
        "trust": trust,
        "learned_in_period": props.get("learned_in_period"),
        "created_by": props.get("created_by"),
        "source_id": props.get("source_id"),
    }
    info["trust_score"] = _mem(ctx, "rule_trust", rule_id, default=None)
    if info["trust_score"] is None:
        info["trust_score"] = round(max(0.0, min(1.0, 0.5 + 0.25 * bool(trust.get("human_verified")) + 0.05 * int(trust.get("times_confirmed", 0) or 0) - 0.15 * int(trust.get("times_refuted", 0) or 0))), 3)
    return info


def _trust_text(rule: dict[str, Any]) -> str:
    t = rule.get("trust") or {}
    bits = ["human-verified" if t.get("human_verified") else "agent-learned"]
    if t.get("times_confirmed"):
        bits.append(f"confirmed {t['times_confirmed']}×")
    if t.get("times_refuted"):
        bits.append(f"refuted {t['times_refuted']}×")
    return ", ".join(bits)


def _load_rules(ctx: AgentContext, agent: BaseAgent, counterparty: dict[str, Any], pattern_types: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    """Active rules for the counterparty (and its channel customers) plus global ones."""
    scopes: list[tuple[str, str | None]] = []
    cp_type, cp_id = counterparty.get("type"), counterparty.get("id")
    if cp_type in ("vendor", "customer") and cp_id:
        scopes.append((cp_type, cp_id))
    for cust in counterparty.get("channel_customer_ids") or []:
        scopes.append(("customer", cust))
    scopes.append(("global", None))
    seen: set[str] = set()
    rules: list[dict[str, Any]] = []
    for scope_type, scope_id in scopes:
        nodes = _mem(ctx, "active_rules", scope_type=scope_type, scope_id=scope_id, default=[]) or []
        for node in nodes:
            info = _rule_info(ctx, node)
            if info["id"] in seen or not info["pattern_type"]:
                continue
            if pattern_types and info["pattern_type"] not in pattern_types:
                continue
            seen.add(info["id"])
            rules.append(info)
    agent.emit(
        "memory.read",
        f"Retrieved {len(rules)} active rule(s) for {counterparty.get('name') or cp_type or 'unknown counterparty'}",
        "; ".join(f"{r['id']} {r['pattern_type']} v{r['version']}" for r in rules)[:300],
        counterparty=counterparty.get("id"),
        rules=[{"id": r["id"], "pattern_type": r["pattern_type"], "params": r["params"], "trust": r["trust"], "version": r["version"]} for r in rules],
    )
    return rules


def _announce(ctx: AgentContext, agent: BaseAgent, h: Hypothesis) -> Hypothesis:
    """Emit hypothesis.generated + hypothesis.tested for a hypothesis and count it."""
    ctx.bump("hypotheses")
    agent.emit("hypothesis.generated", f"{h.kind}: {h.description[:120]}", h.description, hypothesis=_hyp_summary(h))
    verdict = "passed" if h.passed else "failed"
    agent.emit(
        "hypothesis.tested",
        f"{h.kind} {verdict} ({h.confidence:.0%})",
        "; ".join(e.description for e in h.evidence)[:300],
        hypothesis_id=h.id,
        kind=h.kind,
        confidence=h.confidence,
        passed=h.passed,
        rule_id=h.rule_id,
        evidence=[e.model_dump(mode="json") for e in h.evidence],
    )
    return h


# ============================================================================ bank-side facts
@dataclass
class BankFacts:
    """Everything the bank-side generators need, gathered once."""

    bt: dict[str, Any]
    observed: float                                   # absolute amount
    side: str                                         # in | out
    counterparty: dict[str, Any]
    refs: dict[str, list[str]]
    candidates: list[dict[str, Any]]                  # counterparty open items + referenced items
    referenced: list[dict[str, Any]]                  # subset of candidates mentioned in the memo
    siblings: list[dict[str, Any]]                    # other unreconciled txns, same counterparty
    prev_in_transit: list[dict[str, Any]]             # payments sent late in the previous period
    payroll: list[dict[str, Any]]
    rules: list[dict[str, Any]]
    period_id: str

    @property
    def by_id(self) -> dict[str, dict[str, Any]]:
        return {c["id"]: c for c in self.candidates}

    @property
    def cp_name(self) -> str:
        return self.counterparty.get("name") or self.counterparty.get("id") or "unknown counterparty"

    @property
    def resolved(self) -> bool:
        return self.counterparty.get("type") in ("vendor", "customer") and bool(self.counterparty.get("id"))

    def own_candidates(self) -> list[dict[str, Any]]:
        """Candidates that belong to the resolved counterparty (excludes foreign referenced items)."""
        if not self.resolved:
            return []
        cp_type, cp_id = self.counterparty["type"], self.counterparty["id"]
        channel = set(self.counterparty.get("channel_customer_ids") or [])
        return [
            c
            for c in self.candidates
            if (c.get("counterparty_type") == cp_type and c.get("counterparty_id") == cp_id)
            or (c.get("counterparty_type") == "customer" and c.get("counterparty_id") in channel)
        ]


def _memo_text(bt: dict[str, Any]) -> str:
    return f"{bt.get('description', '')} {bt.get('reference', '')}".strip()


def _load_referenced(agent: BaseAgent, refs: dict[str, list[str]], known: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Fetch referenced invoices/payments not already in the candidate pool."""
    out: list[dict[str, Any]] = []
    loaders = (("ar_invoice_ids", "get_ar_invoice", "ar_invoice"), ("ap_invoice_ids", "get_ap_invoice", "ap_invoice"), ("payment_ids", "get_payment", "payment"))
    for key, tool, kind in loaders:
        for rid in refs.get(key, []):
            if rid in known:
                out.append(known[rid])
                continue
            row = agent.try_tool(tool, {}, id=rid)
            if not row:
                continue
            paid = float(row.get("paid_amount") or 0.0)
            remaining = round(float(row["amount"]) - paid, 2)
            usd = remaining
            if kind == "ap_invoice" and row.get("currency", "USD") != "USD" and row.get("fx_rate"):
                usd = round(remaining * float(row["fx_rate"]), 2)
            cand = {
                "kind": kind,
                "id": rid,
                "amount": row["amount"],
                "remaining": remaining if kind != "payment" else row["amount"],
                "expected_usd": usd if kind != "payment" else round(float(row["amount"]), 2),
                "currency": row.get("currency", "USD"),
                "fx_rate": row.get("fx_rate"),
                "date": row.get("date"),
                "period_id": row.get("period_id"),
                "counterparty_type": "customer" if kind == "ar_invoice" else "vendor",
                "counterparty_id": row.get("customer_id") or row.get("vendor_id"),
                "number": row.get("vendor_invoice_number") or rid,
                "status": row.get("status"),
                "linked_ids": list(row.get("ap_invoice_ids") or []),
                "side": "in" if kind == "ar_invoice" else "out",
                "settled": kind == "payment" and bool(row.get("bank_txn_id")) or (kind != "payment" and remaining <= 0),
            }
            out.append(cand)
    return out


def _gather_bank_facts(ctx: AgentContext, agent: BaseAgent, bt: dict[str, Any]) -> BankFacts:
    observed = round(abs(float(bt["amount"])), 2)
    side = "in" if float(bt["amount"]) >= 0 else "out"
    counterparty = agent.try_tool("resolve_counterparty", {"type": "unknown", "id": None, "name": None, "confidence": 0.0}, bank_txn=bt)
    refs = counterparty.get("references") or agent.try_tool("find_by_reference", {}, text=_memo_text(bt)) or {}
    candidates = agent.try_tool("counterparty_candidates", [], counterparty=counterparty, side=side, period_id=ctx.period_id) or []
    known = {c["id"]: c for c in candidates}
    referenced = _load_referenced(agent, refs, known)
    for c in referenced:
        if c["id"] not in known:
            candidates.append(c)
            known[c["id"]] = c
    siblings = agent.try_tool("list_sibling_txns", [], bank_txn_id=bt["id"]) if counterparty.get("id") else []
    prev = agent.try_tool("adjacent_period", None, period_id=ctx.period_id, delta=-1)
    prev_in_transit = agent.try_tool("payments_in_transit", [], period_id=prev, days=IN_TRANSIT_DAYS) if prev and side == "out" else []
    payroll: list[dict[str, Any]] = []
    memo = _memo_text(bt).upper()
    if side == "out" and (bt.get("type") == "payroll" or counterparty.get("type") == "payroll" or "PAYROLL" in memo):
        payroll = agent.try_tool("counterparty_candidates", [], counterparty={"type": "payroll", "id": None}, side="out", period_id=ctx.period_id) or []
    rules = _load_rules(ctx, agent, counterparty, ("percentage_fee", "fixed_fee", "early_pay_discount", "fx_tolerance"))
    return BankFacts(bt=bt, observed=observed, side=side, counterparty=counterparty, refs=refs, candidates=candidates, referenced=referenced, siblings=siblings or [], prev_in_transit=prev_in_transit or [], payroll=payroll, rules=rules, period_id=ctx.period_id)


# ============================================================================ bank-side generators
def _cp_evidence(f: BankFacts, cands: list[dict[str, Any]]) -> Evidence:
    own = {c["id"] for c in f.own_candidates()}
    same = all(c["id"] in own for c in cands) if cands else False
    if not f.resolved:
        return _ev("counterparty_match", f"counterparty could not be resolved from memo '{f.bt.get('description', '')[:60]}'", supports=False, weight=0.5)
    return _ev("counterparty_match", f"{f.cp_name} resolved via {f.counterparty.get('method')} ({f.counterparty.get('confidence', 0):.0%})" + ("" if same else " — candidate belongs to a different counterparty"), supports=same, method=f.counterparty.get("method"))


def gen_exact_match(f: BankFacts) -> list[Hypothesis]:
    """Memo references an invoice / payment whose (remaining) amount equals the transaction."""
    if not f.referenced:
        return []
    live = [c for c in f.referenced if not c.get("settled")]
    if not live:
        return []
    total = round(sum(c["expected_usd"] for c in live), 2)
    fits_all = abs(total - f.observed) <= AMOUNT_TOL
    singles = [c for c in live if abs(c["expected_usd"] - f.observed) <= AMOUNT_TOL]
    if fits_all and len(live) > 1:
        chosen = live
    elif singles:
        chosen = [singles[0]]
    else:
        chosen = [live[0]]
    expected = round(sum(c["expected_usd"] for c in chosen), 2)
    diff = round(f.observed - expected, 2)
    passed = abs(diff) <= AMOUNT_TOL
    evidence = [
        _ev("memo_reference", f"memo references {', '.join(c['id'] for c in chosen)}", refs=[c["id"] for c in chosen]),
        _ev("amount_match", f"observed {_money(f.observed)} vs expected {_money(expected)} (Δ {diff:+,.2f})", supports=passed, observed=f.observed, expected=expected, diff=diff),
        _cp_evidence(f, chosen),
    ]
    desc = f"{_money(f.observed)} settles {' + '.join(c['id'] for c in chosen)} referenced in the memo" if passed else f"memo references {chosen[0]['id']} ({_money(expected)}) but the amount differs by {diff:+,.2f}"
    return [_hyp("exact_match", desc, candidate_ids=_matched_ids(chosen), evidence=evidence, passed=passed, params={"expected": expected, "observed": f.observed, "difference": diff, "gross": expected, "candidates": [_cand_summary(c) for c in chosen]})]


def gen_counterparty_exact(f: BankFacts) -> list[Hypothesis]:
    """Same counterparty, equal amount, unique candidate (falls back to a unique global amount match)."""
    own = [c for c in f.own_candidates() if abs(c["expected_usd"] - f.observed) <= AMOUNT_TOL]
    if f.resolved:
        if not own:
            return []
        referenced_ids = {c["id"] for c in f.referenced}
        preferred = [c for c in own if c["id"] in referenced_ids] or own
        if len(preferred) == 1:
            c = preferred[0]
            evidence = [
                _cp_evidence(f, [c]),
                _ev("amount_match", f"{c['id']} open {_money(c['expected_usd'])} equals {_money(f.observed)}", observed=f.observed, expected=c["expected_usd"]),
                _ev("uniqueness", f"only open item of {f.cp_name} at this amount"),
            ]
            return [_hyp("counterparty_exact", f"{_money(f.observed)} matches {c['id']} ({c['kind']}) of {f.cp_name} exactly", candidate_ids=_matched_ids([c]), evidence=evidence, passed=True, params={"expected": c["expected_usd"], "observed": f.observed, "difference": 0.0, "gross": c["expected_usd"], "candidates": [_cand_summary(c)]})]
        evidence = [
            _cp_evidence(f, own),
            _ev("uniqueness", f"{len(own)} open items of {f.cp_name} share the amount {_money(f.observed)}: {', '.join(c['id'] for c in own)} — ambiguous without a memo reference", supports=False),
        ]
        return [_hyp("counterparty_exact", f"{len(own)} candidates of {f.cp_name} equal {_money(f.observed)} — ambiguous", candidate_ids=[c["id"] for c in own], evidence=evidence, passed=False, params={"ambiguous": True, "candidates": [_cand_summary(c) for c in own]})]
    return []


def gen_unresolved_amount_match(f: BankFacts, agent: BaseAgent) -> list[Hypothesis]:
    """Counterparty unknown: a globally unique open item with this exact amount (MEDIUM)."""
    if f.resolved or f.counterparty.get("type") in ("payroll", "bank"):
        return []
    matches = agent.try_tool("find_amount_candidates", [], amount=f.observed, side=f.side, tolerance_abs=AMOUNT_TOL, period_id=f.period_id) or []
    if not matches:
        return []
    if len(matches) == 1:
        c = matches[0]
        evidence = [_cp_evidence(f, [c]), _ev("amount_match", f"{c['id']} is the only open item at {_money(f.observed)}", observed=f.observed, expected=c["expected_usd"])]
        return [_hyp("counterparty_exact", f"{_money(f.observed)} equals {c['id']} — counterparty unresolved, amount unique", candidate_ids=_matched_ids([c]), evidence=evidence, passed=True, confidence=score("counterparty_exact", counterparty_resolved=False), params={"expected": c["expected_usd"], "observed": f.observed, "difference": 0.0, "gross": c["expected_usd"], "candidates": [_cand_summary(c)]})]
    evidence = [_cp_evidence(f, matches), _ev("uniqueness", f"{len(matches)} open items across counterparties equal {_money(f.observed)} ({', '.join(c['id'] for c in matches)}) — amount collision", supports=False)]
    return [_hyp("counterparty_exact", f"amount collision: {len(matches)} open items equal {_money(f.observed)} and the counterparty is unresolved", candidate_ids=[c["id"] for c in matches], evidence=evidence, passed=False, params={"ambiguous": True, "candidates": [_cand_summary(c) for c in matches]})]


def gen_combined_invoices(f: BankFacts, agent: BaseAgent) -> list[Hypothesis]:
    """Subset-sum over the counterparty's open invoices (≤ 4 items)."""
    pool = [c for c in f.own_candidates() if c["kind"] in ("ar_invoice", "ap_invoice")]
    if len(pool) < 2:
        return []
    solutions = agent.try_tool("subset_sum", [], candidates=pool, target=f.observed, max_items=4, tol=AMOUNT_TOL) or []
    if not solutions:
        return []
    referenced_ids = {c["id"] for c in f.referenced}
    chosen: list[str] | None = None
    if len(solutions) == 1:
        chosen = solutions[0]
    else:
        covering = [s for s in solutions if referenced_ids and referenced_ids.issubset(set(s))]
        if len(covering) == 1:
            chosen = covering[0]
    if chosen is None:
        evidence = [_ev("subset_sum", f"{len(solutions)} different invoice combinations of {f.cp_name} sum to {_money(f.observed)} — ambiguous", supports=False, solutions=solutions[:5])]
        return [_hyp("combined_invoices", f"{len(solutions)} combinations of {f.cp_name} invoices sum to {_money(f.observed)} — ambiguous", candidate_ids=sorted({i for s in solutions for i in s}), evidence=evidence, passed=False, params={"ambiguous": True, "solutions": solutions[:5]})]
    cands = [f.by_id[i] for i in chosen]
    total = round(sum(c["expected_usd"] for c in cands), 2)
    evidence = [
        _cp_evidence(f, cands),
        _ev("subset_sum", f"{' + '.join(_money(c['expected_usd']) for c in cands)} = {_money(total)} (unique combination of {len(cands)} invoices)", observed=f.observed, expected=total, ids=chosen),
    ]
    return [_hyp("combined_invoices", f"{_money(f.observed)} settles {len(cands)} invoices of {f.cp_name}: {', '.join(chosen)}", candidate_ids=_matched_ids(cands), evidence=evidence, passed=True, params={"expected": total, "observed": f.observed, "difference": 0.0, "gross": total, "candidates": [_cand_summary(c) for c in cands]})]


def gen_split_payment(f: BankFacts) -> list[Hypothesis]:
    """This transaction plus one other unreconciled transaction of the same counterparty equals an invoice."""
    if not f.siblings:
        return []
    sign = 1 if f.side == "in" else -1
    others = [s for s in f.siblings if (float(s["amount"]) >= 0) == (sign > 0)]
    referenced_ids = {c["id"] for c in f.referenced}
    options: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for c in f.own_candidates():
        if c["kind"] not in ("ar_invoice", "ap_invoice") or c["expected_usd"] <= f.observed + AMOUNT_TOL:
            continue
        needed = round(c["expected_usd"] - f.observed, 2)
        for s in others:
            if abs(abs(float(s["amount"])) - needed) <= AMOUNT_TOL:
                options.append((c, s))
    if not options:
        return []
    options.sort(key=lambda cs: (cs[0]["id"] not in referenced_ids, cs[0].get("date") or "", cs[1].get("date") or ""))
    c, s = options[0]
    evidence = [
        _cp_evidence(f, [c]),
        _ev("split_sum", f"{_money(f.observed)} + {_money(abs(float(s['amount'])))} ({s['id']}, {s.get('date')}) = {_money(c['expected_usd'])} open on {c['id']}", observed=f.observed, sibling=s["id"], expected=c["expected_usd"]),
    ]
    if c["id"] in referenced_ids:
        evidence.append(_ev("memo_reference", f"memo references {c['id']}"))
    return [_hyp("split_payment", f"{c['id']} ({_money(c['expected_usd'])}) paid in two parts: this {_money(f.observed)} and {s['id']} {_money(abs(float(s['amount'])))}", candidate_ids=[*_matched_ids([c]), s["id"]], evidence=evidence, passed=True, params={"expected": c["expected_usd"], "observed": f.observed, "sibling_txn_id": s["id"], "sibling_amount": abs(float(s["amount"])), "gross": f.observed, "candidates": [_cand_summary(c)]})]


def _implied(agent: BaseAgent, observed: float, expected: float) -> dict[str, Any]:
    return agent.try_tool("implied_rate", {"rate": 0.0, "rate_abs": 0.0, "pct": 0.0, "direction": "none", "is_round": False, "fixed_diff": 0.0, "fixed_is_round": False}, observed=observed, expected=expected)


def gen_partial_payment(f: BankFacts, agent: BaseAgent) -> list[Hypothesis]:
    """Referenced (or unique) invoice paid short by a non-round amount (≥ 10% short)."""
    referenced_ids = {c["id"] for c in f.referenced}
    pool = [c for c in f.own_candidates() if c["kind"] in ("ar_invoice", "ap_invoice") and c["expected_usd"] > f.observed + AMOUNT_TOL]
    if not pool:
        return []
    preferred = [c for c in pool if c["id"] in referenced_ids] or (pool if len(pool) == 1 else [])
    if not preferred:
        return []
    c = preferred[0]
    imp = _implied(agent, f.observed, c["expected_usd"])
    ratio = f.observed / c["expected_usd"] if c["expected_usd"] else 0.0
    round_pattern = imp["is_round"] or imp["fixed_is_round"]
    passed = ratio <= 0.90 and not round_pattern
    evidence = [
        _cp_evidence(f, [c]),
        _ev("amount_match", f"{_money(f.observed)} is {ratio:.1%} of {c['id']} ({_money(c['expected_usd'])})", supports=ratio <= 0.90, observed=f.observed, expected=c["expected_usd"]),
        _ev("pattern", "difference is a round percentage / fixed amount — looks like an adjustment, not a partial payment" if round_pattern else "difference is not a round adjustment", supports=not round_pattern, implied=imp),
    ]
    if c["id"] in referenced_ids:
        evidence.append(_ev("memo_reference", f"memo references {c['id']}"))
    return [_hyp("partial_payment", f"partial payment of {c['id']}: {_money(f.observed)} against {_money(c['expected_usd'])} ({_money(round(c['expected_usd'] - f.observed, 2))} still open)", candidate_ids=_matched_ids([c]), evidence=evidence, passed=passed, params={"expected": c["expected_usd"], "observed": f.observed, "difference": round(f.observed - c["expected_usd"], 2), "gross": f.observed, "candidates": [_cand_summary(c)]})]


def gen_timing_lag(f: BankFacts) -> list[Hypothesis]:
    """Bank transaction settles a payment sent in the last days of the previous period."""
    if f.side != "out" or not f.prev_in_transit:
        return []
    cp_id = f.counterparty.get("id") if f.counterparty.get("type") == "vendor" else None
    matches = [p for p in f.prev_in_transit if abs(p["expected_usd"] - f.observed) <= AMOUNT_TOL and (cp_id is None or p["counterparty_id"] == cp_id)]
    if not matches:
        return []
    if len(matches) > 1 and cp_id is None:
        evidence = [_ev("timing", f"{len(matches)} in-transit payments from the previous period equal {_money(f.observed)} — counterparty unresolved", supports=False)]
        return [_hyp("timing_lag", "ambiguous in-transit payments from the previous period", candidate_ids=[p["id"] for p in matches], evidence=evidence, passed=False, params={"ambiguous": True})]
    p = matches[0]
    evidence = [
        _ev("timing", f"{p['id']} sent {p['date']} (previous period), cleared {f.bt.get('date')}", sent=p["date"], cleared=f.bt.get("date")),
        _ev("amount_match", f"payment {_money(p['expected_usd'])} equals {_money(f.observed)}", observed=f.observed, expected=p["expected_usd"]),
        _cp_evidence(f, [p]) if f.resolved else _ev("counterparty_match", "payment vendor inferred from the in-transit register", weight=0.5),
    ]
    return [_hyp("timing_lag", f"{p['id']} ({_money(p['expected_usd'])}) initiated {p['date']} cleared in {f.period_id} — timing lag", candidate_ids=_matched_ids([p]), evidence=evidence, passed=True, params={"expected": p["expected_usd"], "observed": f.observed, "difference": 0.0, "gross": p["expected_usd"], "candidates": [_cand_summary(p)]})]


def gen_payroll(f: BankFacts) -> list[Hypothesis]:
    """Outflow equals a payroll run's net pay or employer taxes."""
    if not f.payroll:
        return []
    matches = [c for c in f.payroll if abs(c["expected_usd"] - f.observed) <= AMOUNT_TOL and c["status"] != "cleared"]
    if not matches:
        return []
    c = sorted(matches, key=lambda x: (x["component"] != "net", x["component"] != "employer_taxes", x["date"]))[0]
    evidence = [
        _ev("amount_match", f"{c['id']} {c['component']} {_money(c['expected_usd'])} equals {_money(f.observed)}", observed=f.observed, expected=c["expected_usd"], component=c["component"]),
        _ev("counterparty_match", f"memo/type identifies payroll ({f.bt.get('type')})"),
    ]
    return [_hyp("payroll", f"{_money(f.observed)} is the {c['component'].replace('_', ' ')} of payroll run {c['id']} ({c['date']})", candidate_ids=[c["id"]], evidence=evidence, passed=True, params={"expected": c["expected_usd"], "observed": f.observed, "difference": 0.0, "gross": c["expected_usd"], "component": c["component"], "candidates": [_cand_summary(c)]})]


def gen_bank_fee(f: BankFacts) -> list[Hypothesis]:
    """Small outflow with a fee-like memo and no vendor / customer counterparty."""
    memo = _memo_text(f.bt).upper()
    fee_like = f.bt.get("type") == "fee" or f.counterparty.get("type") == "bank" or ("FEE" in memo or "SERVICE CHARGE" in memo)
    if f.side != "out" or not fee_like or f.resolved or f.observed > 250:
        return []
    evidence = [
        _ev("memo_pattern", f"memo '{f.bt.get('description', '')[:60]}' is fee-like and no vendor/customer matched"),
        _ev("amount_match", f"{_money(f.observed)} is within the bank-fee range (≤ $250)"),
    ]
    return [_hyp("bank_fee", f"{_money(f.observed)} bank service fee ({f.bt.get('description', '')[:40]})", candidate_ids=[], evidence=evidence, passed=True, params={"expected": 0.0, "observed": f.observed, "difference": f.observed, "gross": 0.0, "adjustment_kind": "fee"})]


def _rule_expectations(rule: dict[str, Any], base: float, side: str) -> list[dict[str, Any]]:
    """Expected observed amounts implied by a rule for an item worth `base` (USD)."""
    p = rule.get("params") or {}
    pt = rule.get("pattern_type")
    out: list[dict[str, Any]] = []
    if pt == "percentage_fee":
        rate = _norm_rate(p.get("rate", p.get("pct", 0)))
        direction = p.get("direction") or "either"
        if rate <= 0:
            return out
        if direction in ("deduct", "either"):
            out.append({"expected": round(base * (1 - rate), 2), "direction": "deduct", "rate": rate, "label": f"{rate:.1%} fee deducted", "adjustment_kind": "fee"})
        if direction in ("add", "either"):
            out.append({"expected": round(base * (1 + rate), 2), "direction": "add", "rate": rate, "label": f"{rate:.1%} surcharge added", "adjustment_kind": "fee"})
    elif pt == "early_pay_discount":
        rate = _norm_rate(p.get("rate", p.get("pct", 0)))
        if rate > 0:
            out.append({"expected": round(base * (1 - rate), 2), "direction": "deduct", "rate": rate, "label": f"{rate:.1%} early-payment discount", "adjustment_kind": "discount"})
    elif pt == "fixed_fee":
        amount = float(p.get("amount", p.get("fee", 0)) or 0)
        direction = p.get("direction") or "either"
        if amount <= 0:
            return out
        if direction in ("deduct", "either"):
            out.append({"expected": round(base - amount, 2), "direction": "deduct", "amount": amount, "label": f"{_money(amount)} fee deducted", "adjustment_kind": "fee"})
        if direction in ("add", "either"):
            out.append({"expected": round(base + amount, 2), "direction": "add", "amount": amount, "label": f"{_money(amount)} fee added", "adjustment_kind": "fee"})
    elif pt == "fx_tolerance":
        tol = _norm_rate(p.get("tolerance_pct", p.get("tolerance", p.get("rate", 0))))
        if tol > 0:
            out.append({"expected": base, "direction": "either", "tolerance": tol, "label": f"FX variance within ±{tol:.1%}", "adjustment_kind": "fx"})
    return out


def gen_memory_rules(f: BankFacts, agent: BaseAgent) -> list[Hypothesis]:
    """Each applicable learned Rule → parameterised hypothesis tested against the open items."""
    if not f.rules:
        return []
    pool = [c for c in f.candidates if c["kind"] in ("ar_invoice", "ap_invoice", "payment") and not c.get("settled")]
    referenced_ids = {c["id"] for c in f.referenced}
    out: list[Hypothesis] = []
    for rule in f.rules:
        kind = RULE_KIND.get(rule["pattern_type"] or "")
        if kind is None:
            continue
        fits: list[tuple[dict[str, Any], dict[str, Any], float]] = []
        for c in pool:
            base = c["expected_usd"]
            if base <= 0:
                continue
            for exp in _rule_expectations(rule, base, f.side):
                if rule["pattern_type"] == "fx_tolerance":
                    variance = abs(f.observed - base) / base
                    if variance <= exp["tolerance"] + 1e-9 and abs(f.observed - base) > AMOUNT_TOL:
                        fits.append((c, {**exp, "variance": round(variance, 5)}, abs(f.observed - base)))
                elif abs(f.observed - exp["expected"]) <= RULE_TOL:
                    fits.append((c, exp, abs(f.observed - exp["expected"])))
        # combined items under a percentage rule (e.g. one payout net of fee for two invoices)
        if not fits and rule["pattern_type"] in ("percentage_fee", "early_pay_discount") and len(pool) >= 2:
            for exp in _rule_expectations(rule, 1.0, f.side):
                factor = exp["expected"]
                if factor <= 0:
                    continue
                gross_target = round(f.observed / factor, 2)
                solutions = agent.try_tool("subset_sum", [], candidates=[c for c in pool if c["kind"] != "payment"], target=gross_target, max_items=4, tol=0.03) or []
                if len(solutions) == 1:
                    cands = [f.by_id[i] for i in solutions[0]]
                    base = round(sum(c["expected_usd"] for c in cands), 2)
                    combo = {"kind": "combo", "id": "+".join(solutions[0]), "expected_usd": base, "members": cands, "counterparty_type": cands[0]["counterparty_type"], "counterparty_id": cands[0]["counterparty_id"]}
                    fits.append((combo, {**exp, "expected": round(base * factor, 2)}, abs(f.observed - base * factor)))
        trust_txt = _trust_text(rule)
        rule_label = f"rule {rule['id']} v{rule['version']} ({trust_txt})"
        if not fits:
            evidence = [_ev("rule_fit", f"no open item of {f.cp_name} fits '{rule['description'] or rule['pattern_type']}' for {_money(f.observed)}", supports=False, rule_id=rule["id"], params=rule["params"])]
            out.append(_hyp(kind, f"{rule['description'] or rule['pattern_type']} does not explain {_money(f.observed)}", candidate_ids=[], evidence=evidence, passed=False, rule_id=rule["id"], generated_by="memory", params={"rule": {k: rule[k] for k in ("id", "pattern_type", "params", "version", "trust")}}))
            continue
        fits.sort(key=lambda t: (t[0]["id"] not in referenced_ids, t[2], t[0]["id"]))
        c, exp, gap = fits[0]
        distinct_items = {t[0]["id"] for t in fits}
        members = c.get("members") or [c]
        ambiguous = len(distinct_items) > 1 and c["id"] not in referenced_ids
        base = round(sum(m["expected_usd"] for m in members), 2)
        evidence = [
            _ev("rule_fit", f"{' + '.join(m['id'] for m in members)} {_money(base)} with {exp['label']} → expected {_money(exp['expected'])}, observed {_money(f.observed)} (Δ {f.observed - exp['expected']:+,.2f})", supports=not ambiguous, rule_id=rule["id"], expected=exp["expected"], observed=f.observed, base=base, params=rule["params"]),
            _cp_evidence(f, members),
            _ev("precedent", f"{rule_label}; trust {rule['trust_score']:.2f}, learned in {rule.get('learned_in_period') or 'n/a'}", rule_id=rule["id"], trust=rule["trust"], trust_score=rule["trust_score"], version=rule["version"]),
        ]
        if ambiguous:
            evidence.append(_ev("uniqueness", f"{len(distinct_items)} items fit the rule equally ({', '.join(sorted(distinct_items))}) — ambiguous", supports=False))
        conf = score(kind, trust=rule["trust"])
        desc = f"{_money(f.observed)} = {' + '.join(m['id'] for m in members)} {_money(base)} {exp['label']} per {rule_label}"
        out.append(
            _hyp(kind, desc, candidate_ids=_matched_ids(members), evidence=evidence, passed=not ambiguous, confidence=conf if not ambiguous else 0.0, rule_id=rule["id"], generated_by="memory",
                 params={"expected": exp["expected"], "observed": f.observed, "difference": round(f.observed - base, 2), "gross": base, "adjustment_kind": exp["adjustment_kind"], "direction": exp["direction"], "rate": exp.get("rate"), "amount": exp.get("amount"), "tolerance": exp.get("tolerance"), "variance": exp.get("variance"), "rule": {k: rule[k] for k in ("id", "pattern_type", "params", "version", "trust", "description", "learned_in_period")}, "candidates": [_cand_summary(m) for m in members]})
        )
    return out


def gen_precedent_drift(f: BankFacts, agent: BaseAgent) -> list[Hypothesis]:
    """A rule of the right pattern exists but the implied rate/fee is close, not equal → drift."""
    if not f.rules:
        return []
    pool = [c for c in f.own_candidates() if c["kind"] in ("ar_invoice", "ap_invoice", "payment") and not c.get("settled")]
    referenced_ids = {c["id"] for c in f.referenced}
    out: list[Hypothesis] = []
    for rule in f.rules:
        pt = rule["pattern_type"]
        p = rule.get("params") or {}
        best: tuple[float, dict[str, Any], dict[str, Any], str, str] | None = None  # (distance, cand, implied, description, adjustment_kind)
        for c in pool:
            base = c["expected_usd"]
            if base <= 0:
                continue
            imp = _implied(agent, f.observed, base)
            if pt in ("percentage_fee", "early_pay_discount"):
                known = _norm_rate(p.get("rate", p.get("pct", 0)))
                direction = p.get("direction") or ("deduct" if pt == "early_pay_discount" else "either")
                if known <= 0 or not imp["is_round"] or (direction != "either" and imp["direction"] != direction):
                    continue
                dist = abs(imp["rate_abs"] - known)
                if 0.0005 < dist <= DRIFT_PP + 1e-9:
                    desc = f"implied {imp['rate_abs']:.1%} vs known {known:.1%} (rule v{rule['version']}) — possible fee change"
                    adj = "discount" if pt == "early_pay_discount" else "fee"
                    if best is None or (c["id"] in referenced_ids, -dist) > (best[1]["id"] in referenced_ids, -best[0]):
                        best = (dist, c, imp, desc, adj)
            elif pt == "fixed_fee":
                known_amt = float(p.get("amount", p.get("fee", 0)) or 0)
                diff = abs(imp["fixed_diff"])
                if known_amt <= 0 or not imp["fixed_is_round"]:
                    continue
                dist = abs(diff - known_amt)
                if 0.005 < dist <= DRIFT_FIXED_USD:
                    desc = f"implied {_money(diff)} fixed fee vs known {_money(known_amt)} (rule v{rule['version']}) — possible fee change"
                    if best is None or dist < best[0]:
                        best = (dist, c, imp, desc, "fee")
            elif pt == "fx_tolerance":
                tol = _norm_rate(p.get("tolerance_pct", p.get("tolerance", p.get("rate", 0))))
                variance = abs(f.observed - base) / base
                if tol > 0 and tol < variance <= tol + DRIFT_PP:
                    dist = variance - tol
                    desc = f"implied {variance:.2%} FX variance vs tolerance {tol:.1%} (rule v{rule['version']}) — beyond the learned band"
                    if best is None or dist < best[0]:
                        best = (dist, c, imp, desc, "fx")
        if best is None:
            continue
        dist, c, imp, desc, adj = best
        evidence = [
            _ev("rule_fit", f"{c['id']} {_money(c['expected_usd'])} → observed {_money(f.observed)}: {desc}", supports=False, rule_id=rule["id"], implied=imp, params=p),
            _cp_evidence(f, [c]),
            _ev("precedent", f"nearest precedent: rule {rule['id']} v{rule['version']} ({_trust_text(rule)})", rule_id=rule["id"], version=rule["version"], trust=rule["trust"]),
        ]
        out.append(_hyp("precedent_drift", desc, candidate_ids=_matched_ids([c]), evidence=evidence, passed=True, rule_id=rule["id"], generated_by="memory",
                        params={"expected": c["expected_usd"], "observed": f.observed, "difference": round(f.observed - c["expected_usd"], 2), "gross": c["expected_usd"], "implied": imp, "known_rate": _norm_rate(p.get("rate", p.get("pct", 0))) if pt != "fixed_fee" else None, "known_amount": p.get("amount"), "adjustment_kind": adj, "rule": {k: rule[k] for k in ("id", "pattern_type", "params", "version", "trust", "description")}, "candidates": [_cand_summary(c)]}))
    return out


def _pattern_label(f: BankFacts, c: dict[str, Any], imp: dict[str, Any]) -> tuple[str, str]:
    """(human label, adjustment kind) for an observed round difference."""
    processor = f.counterparty.get("is_payment_processor")
    if imp["is_round"]:
        if f.side == "in" and imp["direction"] == "deduct":
            return ("processing fee", "fee") if processor else ("early-payment discount or processing fee", "discount")
        if f.side == "out" and imp["direction"] == "add":
            return ("vendor surcharge", "fee")
        if f.side == "out" and imp["direction"] == "deduct":
            return ("withheld fee or discount taken", "discount")
        return ("percentage adjustment", "fee")
    return ("fixed bank/wire fee", "fee")


def gen_observed_pattern(f: BankFacts, agent: BaseAgent) -> list[Hypothesis]:
    """Round % / round fixed difference (or small FX drift on a foreign-currency bill) → observation only."""
    pool = [c for c in f.candidates if c["kind"] in ("ar_invoice", "ap_invoice", "payment") and not c.get("settled")]
    referenced_ids = {c["id"] for c in f.referenced}
    own_ids = {c["id"] for c in f.own_candidates()}
    found: list[tuple[tuple[int, int, float], dict[str, Any], dict[str, Any], str]] = []
    for c in pool:
        base = c["expected_usd"]
        if base <= 0 or abs(f.observed - base) <= AMOUNT_TOL:
            continue
        imp = _implied(agent, f.observed, base)
        rate_ok = imp["is_round"] and imp["rate_abs"] <= MAX_OBSERVED_RATE
        fixed_ok = imp["fixed_is_round"] and abs(imp["fixed_diff"]) <= MAX_OBSERVED_FIXED
        fx_ok = (c.get("currency") or "USD") != "USD" and imp["rate_abs"] <= 0.05
        if rate_ok or fixed_ok:
            found.append(((c["id"] not in referenced_ids, c["id"] not in own_ids, imp["rate_abs"]), c, imp, "round"))
        elif fx_ok:
            found.append(((c["id"] not in referenced_ids, c["id"] not in own_ids, imp["rate_abs"] + 1.0), c, imp, "fx"))
    if not found:
        return []
    found.sort(key=lambda t: t[0])
    _, c, imp, mode = found[0]
    known_rules = [r for r in f.rules if r["pattern_type"] in ("percentage_fee", "early_pay_discount", "fixed_fee", "fx_tolerance")]
    if mode == "fx":
        label, adj = "FX movement on a foreign-currency bill", "fx"
        desc = f"difference is {imp['rate_abs']:.2%} on a {c.get('currency')} invoice — consistent with {label}, but no FX tolerance rule exists for {f.cp_name}"
        conf = score("observed_pattern", fx_only=True)
    else:
        label, adj = _pattern_label(f, c, imp)
        if imp["is_round"]:
            desc = f"difference is exactly {imp['rate_abs']:.1%} of the invoice — consistent with a {label}, but no verified precedent exists for {f.cp_name}"
        else:
            desc = f"difference is exactly {_money(abs(imp['fixed_diff']))} — consistent with a {label}, but no verified precedent exists for {f.cp_name}"
        conf = score("observed_pattern")
    if known_rules:
        desc += f" (known rule {known_rules[0]['id']} v{known_rules[0]['version']} does not fit)"
    evidence = [
        _ev("pattern", desc, implied=imp, base=c["expected_usd"], observed=f.observed),
        _cp_evidence(f, [c]),
        _ev("precedent", "no learned rule explains this adjustment — rate-based adjustments are never guessed", supports=False, weight=1.5),
    ]
    if c["id"] in referenced_ids:
        evidence.append(_ev("memo_reference", f"memo references {c['id']}"))
    return [_hyp("observed_pattern", desc, candidate_ids=_matched_ids([c]), evidence=evidence, passed=True, confidence=conf,
                 params={"expected": c["expected_usd"], "observed": f.observed, "difference": round(f.observed - c["expected_usd"], 2), "gross": c["expected_usd"], "implied": imp, "adjustment_kind": adj, "pattern_label": label, "suggested_pattern": ("fx_tolerance" if mode == "fx" else "fixed_fee" if not imp["is_round"] else "early_pay_discount" if adj == "discount" else "percentage_fee"), "suggested_params": ({"tolerance_pct": round(imp["rate_abs"], 4)} if mode == "fx" else {"amount": abs(imp["fixed_diff"])} if not imp["is_round"] else {"rate": round(imp["rate_abs"], 4), "direction": imp["direction"]}), "candidates": [_cand_summary(c)]})]


def gen_unknown(f: BankFacts) -> Hypothesis:
    """Fallback when nothing explains the transaction."""
    if f.side == "in" and not f.own_candidates():
        desc = f"deposit of {_money(f.observed)} from {f.cp_name} with no open invoice — possible overpayment / unknown deposit"
    elif f.side == "in":
        desc = f"deposit of {_money(f.observed)} from {f.cp_name} matches no open invoice or combination"
    else:
        desc = f"payment of {_money(f.observed)} to {f.cp_name} matches no open bill, payment or payroll run"
    evidence = [_ev("no_match", desc, supports=False), _cp_evidence(f, [])]
    return _hyp("unknown", desc, candidate_ids=[c["id"] for c in f.own_candidates()][:6], evidence=evidence, passed=True, confidence=score("unknown"), params={"observed": f.observed, "candidates": [_cand_summary(c) for c in f.own_candidates()][:6]})


# ---------------------------------------------------------------- LLM-proposed specs
def _test_spec(f: BankFacts, agent: BaseAgent, spec: HypothesisSpec, tested: list[Hypothesis]) -> Hypothesis:
    """Evidence-test a Brain-proposed HypothesisSpec with the same arithmetic as the built-ins."""
    known = f.by_id
    refs: dict[str, list[str]] = {"ar_invoice_ids": [], "ap_invoice_ids": [], "payment_ids": []}
    for cid in spec.candidate_ids:
        if cid in known:
            continue
        key = "ar_invoice_ids" if cid.startswith("AR-") else "payment_ids" if cid.startswith("PAY-") else "ap_invoice_ids"
        refs[key].append(cid)
    extra = _load_referenced(agent, refs, known) if any(refs.values()) else []
    pool = {**known, **{c["id"]: c for c in extra}}
    cands = [pool[cid] for cid in spec.candidate_ids if cid in pool]
    base = round(sum(c["expected_usd"] for c in cands), 2)
    p = dict(spec.params or {})
    rate = _norm_rate(p.get("rate", 0)) if p.get("rate") is not None else 0.0
    fixed = float(p.get("fixed_amount", p.get("amount", 0)) or 0)
    direction = p.get("direction") or ("deduct" if f.side == "in" else "add")
    expected = base
    if rate:
        expected = round(base * (1 - rate), 2) if direction == "deduct" else round(base * (1 + rate), 2)
    if fixed:
        expected = round(expected - fixed, 2) if direction == "deduct" else round(expected + fixed, 2)
    passed = bool(cands) and abs(expected - f.observed) <= RULE_TOL
    evidence = [
        _ev("amount_match", f"proposed {spec.kind}: expected {_money(expected)} vs observed {_money(f.observed)} (Δ {f.observed - expected:+,.2f})", supports=passed, expected=expected, observed=f.observed, base=base),
        _cp_evidence(f, cands) if cands else _ev("counterparty_match", "no verifiable candidates named", supports=False),
        _ev("precedent", "LLM proposal — rate/fee adjustments without a learned rule stay below the auto-execute bar", supports=bool(spec.rule_id), weight=0.5),
    ]
    conf = 0.0
    if passed:
        conf = min(LLM_CAP, score(spec.kind) if spec.kind in BASE_SCORES else LLM_CAP)
        for h in tested:
            if h.passed and h.kind == spec.kind and set(h.candidate_ids) == set(_matched_ids(cands)):
                conf = h.confidence
                break
        if spec.kind.startswith("rule_") and spec.rule_id:
            conf = min(conf, LLM_CAP)
        if (rate or fixed) and not spec.rule_id:
            conf = min(conf, score("observed_pattern"))
    kind = spec.kind if spec.kind in BASE_SCORES or spec.kind in RULE_KIND.values() else "llm_proposed"
    return _hyp(kind, spec.description, candidate_ids=_matched_ids(cands), evidence=evidence, passed=passed, confidence=conf, rule_id=spec.rule_id, generated_by="llm",
                params={**p, "proposed_kind": spec.kind, "expected": expected, "observed": f.observed, "difference": round(f.observed - base, 2) if cands else None, "gross": base, "adjustment_kind": p.get("adjustment_kind", "fee"), "candidates": [_cand_summary(c) for c in cands]})


async def _llm_hypotheses(ctx: AgentContext, agent: BaseAgent, f: BankFacts, tested: list[Hypothesis]) -> list[Hypothesis]:
    if ctx.brain is None:
        return []
    brain_ctx = {
        "entity_type": "bank_transaction",
        "entity": f.bt,
        "observed": f.observed,
        "side": f.side,
        "counterparty": f.counterparty,
        "candidates": [_cand_summary(c) for c in f.candidates][:15],
        "siblings": [{"id": s["id"], "amount": s["amount"], "date": s.get("date"), "description": s.get("description")} for s in f.siblings][:8],
        "rules": [{k: r[k] for k in ("id", "pattern_type", "params", "description", "version", "trust_score")} for r in f.rules],
        "hypotheses": [_hyp_summary(h) for h in tested],
        "period_id": ctx.period_id,
        "run_id": ctx.run_id,
        "agent": agent.name,
        "counters": ctx.counters,
    }
    try:
        specs = await ctx.brain.propose_hypotheses(brain_ctx)
    except Exception as exc:  # noqa: BLE001
        log.warning("brain.propose_hypotheses failed: %s", exc)
        return []
    out: list[Hypothesis] = []
    for spec in specs or []:
        if not isinstance(spec, HypothesisSpec):
            try:
                spec = HypothesisSpec.model_validate(spec)
            except Exception:  # noqa: BLE001
                continue
        out.append(_announce(ctx, agent, _test_spec(f, agent, spec, tested)))
    return out


# ============================================================================ bank investigation
def _pick_best(hyps: list[Hypothesis]) -> Hypothesis | None:
    passed = [h for h in hyps if h.passed]
    if not passed:
        return None
    return sorted(passed, key=lambda h: -h.confidence)[0]  # stable → generator order breaks ties


def _bank_category(f: BankFacts, best: Hypothesis) -> str:
    if best.kind == "unknown":
        return "unknown_deposit" if f.side == "in" and not f.own_candidates() else ("unmatched_txn" if not f.own_candidates() else "unexplained_difference")
    if best.kind in ("partial_payment",):
        return "partial_payment"
    return "unexplained_difference"


async def investigate_bank_txn(ctx: AgentContext, agent: BaseAgent, bank_txn_id: str, exception_id: str | None = None, resolved_by: str | None = None) -> Investigation:
    """Run the full hypothesis loop for one bank transaction and act on the outcome.

    Args:
        ctx: run context (memory, brain, counters).
        agent: the calling agent (its tool calls are traced).
        bank_txn_id: bank transaction id.
        exception_id: existing Exception to update (re-investigation / propagation).
        resolved_by: who to credit on resolution (defaults to "agent:<name>").
    """
    t0 = perf_counter()
    steps0 = agent.steps
    inv = Investigation(entity_type="bank_transaction", entity_id=bank_txn_id, period_id=ctx.period_id)
    bt = agent.try_tool("get_bank_txn", {}, id=bank_txn_id)
    if not bt:
        inv.status = "missing"
        inv.hypotheses = [_announce(ctx, agent, _hyp("unknown", f"bank transaction {bank_txn_id} not found", candidate_ids=[], evidence=[_ev("no_match", "entity missing", supports=False)], passed=True, confidence=score("unknown")))]
        inv.best = inv.hypotheses[0]
        inv.steps, inv.ms = agent.steps - steps0, (perf_counter() - t0) * 1000
        return inv
    inv.entity = bt
    if bt.get("reconciled"):
        inv.status = "already_reconciled"
        inv.matched_ids = list(bt.get("reconciled_with") or [])
        if exception_id:
            _close_exception(ctx, exception_id, resolved_by or f"agent:{agent.name}")
            inv.status = "reconciled"
        inv.steps, inv.ms = agent.steps - steps0, (perf_counter() - t0) * 1000
        return inv

    ctx.bump("items")
    agent.emit("agent.step", f"Investigating {bt['id']} {_money(float(bt['amount']))}", str(bt.get("description", ""))[:200], bank_txn=bt)
    f = _gather_bank_facts(ctx, agent, bt)
    inv.counterparty = f.counterparty

    hyps: list[Hypothesis] = []

    def run(gen_out: list[Hypothesis]) -> None:
        for h in gen_out:
            hyps.append(_announce(ctx, agent, h))

    def safe(fn: Any, *args: Any) -> list[Hypothesis]:
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            log.warning("generator %s failed on %s: %s", getattr(fn, "__name__", fn), bt["id"], exc, exc_info=True)
            return []

    run(safe(gen_exact_match, f))
    run(safe(gen_counterparty_exact, f))
    run(safe(gen_unresolved_amount_match, f, agent))
    run(safe(gen_combined_invoices, f, agent))
    run(safe(gen_split_payment, f))
    run(safe(gen_partial_payment, f, agent))
    run(safe(gen_timing_lag, f))
    run(safe(gen_payroll, f))
    run(safe(gen_bank_fee, f))
    run(safe(gen_memory_rules, f, agent))
    run(safe(gen_precedent_drift, f, agent))
    run(safe(gen_observed_pattern, f, agent))
    if not any(h.passed and h.kind in TRIVIAL_KINDS and tier(h.confidence) == Tier.HIGH for h in hyps):
        hyps.extend(await _llm_hypotheses(ctx, agent, f, hyps))
    if not any(h.passed for h in hyps):
        run([gen_unknown(f)])
    inv.hypotheses = hyps
    inv.best = _pick_best(hyps)

    await _conclude(ctx, agent, inv, f, exception_id, resolved_by)
    inv.steps, inv.ms = agent.steps - steps0, (perf_counter() - t0) * 1000
    await ctx.pace()
    return inv


# ============================================================================ conclusion (shared)
def _close_exception(ctx: AgentContext, exception_id: str, resolved_by: str) -> None:
    store = getattr(ctx.memory, "store", None)
    if store is None:
        return
    try:
        store.update_node(exception_id, {"status": ExceptionStatus.RESOLVED.value, "resolved_by": resolved_by, "resolved_at": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:  # noqa: BLE001
        log.warning("could not close exception %s: %s", exception_id, exc)


def _explain_ctx(ctx: AgentContext, agent: BaseAgent, inv: Investigation, best: Hypothesis, decision: Decision | None, counterparty: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": inv.entity_type,
        "entity": inv.entity,
        "counterparty": counterparty,
        "hypothesis": best.model_dump(mode="json"),
        "evidence": [e.model_dump(mode="json") for e in best.evidence],
        "confidence": best.confidence,
        "tier": hypothesis_tier(best).value,
        "rule": best.params.get("rule"),
        "matched": best.params.get("candidates", []),
        "observed": best.params.get("observed"),
        "expected": best.params.get("expected"),
        "difference": best.params.get("difference"),
        "action": decision.action if decision else None,
        "status": decision.status.value if decision else None,
        "alternatives": [_hyp_summary(h) for h in inv.hypotheses if h.id != best.id][:6],
        "period_id": ctx.period_id,
        "run_id": ctx.run_id,
        "agent": agent.name,
        "counters": ctx.counters,
    }


async def _explain(ctx: AgentContext, agent: BaseAgent, inv: Investigation, best: Hypothesis, decision: Decision | None, counterparty: dict[str, Any]) -> str:
    if ctx.brain is None:
        return best.description
    try:
        return await ctx.brain.explain_decision(_explain_ctx(ctx, agent, inv, best, decision, counterparty))
    except Exception as exc:  # noqa: BLE001
        log.warning("brain.explain_decision failed: %s", exc)
        return best.description


def _action_for(best: Hypothesis) -> str:
    if best.kind == "duplicate":
        return "hold"
    if best.kind in ("policy_no_po", "policy_price_variance", "unknown"):
        return "escalate"
    if best.kind == "rule_price_tolerance":
        return "approve"
    if best.kind == "timing_lag" and best.params.get("entity_type") == "ap_invoice":
        return "accept_in_transit"
    return "reconcile"


def _precedent_hit(ctx: AgentContext, agent: BaseAgent, best: Hypothesis, decision: Decision) -> None:
    rule = best.params.get("rule") or {}
    ctx.bump("precedent_hits")
    agent.emit(
        "memory.precedent_hit",
        f"Precedent {best.rule_id} v{rule.get('version', '?')} applied",
        f"{rule.get('description') or rule.get('pattern_type')} — {_trust_text(rule)}; learned in {rule.get('learned_in_period') or 'n/a'}",
        rule_id=best.rule_id,
        decision_id=decision.id,
        pattern_type=rule.get("pattern_type"),
        params=rule.get("params"),
        trust=rule.get("trust"),
        version=rule.get("version"),
        learned_in_period=rule.get("learned_in_period"),
        provenance={"rule_id": best.rule_id, "learned_in_period": rule.get("learned_in_period"), "created_by": rule.get("created_by"), "trust": rule.get("trust")},
    )


async def _conclude(ctx: AgentContext, agent: BaseAgent, inv: Investigation, f: BankFacts | None, exception_id: str | None, resolved_by: str | None, *, ap_info: dict[str, Any] | None = None) -> None:
    """Tier the best hypothesis and execute / park / escalate accordingly."""
    best = inv.best
    if best is None:
        inv.status = "clean"
        return
    best_tier = hypothesis_tier(best)
    counterparty = inv.counterparty
    is_bank = inv.entity_type == "bank_transaction"
    action = _action_for(best)
    by = resolved_by or f"agent:{agent.name}"

    # ---- trivially clean → execute, no Exception
    if best.kind in TRIVIAL_KINDS and best_tier == Tier.HIGH and exception_id is None:
        decision = Decision(exception_id=f"auto:{inv.entity_id}", hypothesis_id=best.id, action=action, matched_ids=[i for i in best.candidate_ids if not i.startswith("BT-")], confidence=best.confidence, tier=best_tier, status=DecisionStatus.PROPOSED, explanation=best.description, agent=agent.name, period_id=ctx.period_id, run_id=ctx.run_id)
        ok = await execute_decision(ctx, agent, decision, best, inv.entity)
        inv.decision = decision
        inv.executed = ok
        inv.auto_resolved = ok
        inv.matched_ids = list(decision.matched_ids)
        inv.status = ("reconciled" if is_bank else "approved") if ok else "needs_human"
        if ok:
            ctx.bump("auto_resolved")
        return

    # ---- exception path
    observed = best.params.get("observed", abs(float(inv.entity.get("amount", 0) or 0)))
    expected = best.params.get("expected") if best.kind not in TRIVIAL_KINDS else best.params.get("gross")
    if best.kind in ("rule_percentage_fee", "rule_fixed_fee", "rule_early_pay_discount", "rule_fx_tolerance", "precedent_drift", "observed_pattern", "partial_payment", "llm_proposed"):
        expected = best.params.get("gross")
    difference = round(float(observed) - float(expected), 2) if expected is not None and observed is not None else None
    category = (ap_info or {}).get("category") or (_bank_category(f, best) if f is not None else "unexplained")
    title = (ap_info or {}).get("title") or f"{'Deposit' if f and f.side == 'in' else 'Payment'} {_money(float(observed))} from {counterparty.get('name') or 'unknown'}" + (f" vs {_money(float(expected))} (Δ {difference:+,.2f})" if expected and difference is not None else "")
    status = {Tier.HIGH: ExceptionStatus.OPEN, Tier.MEDIUM: ExceptionStatus.PENDING_AUDIT, Tier.LOW: ExceptionStatus.NEEDS_HUMAN}[best_tier]
    exc = ExceptionRecord(
        **({"id": exception_id} if exception_id else {}),
        period_id=ctx.period_id,
        run_id=ctx.run_id,
        entity_type=inv.entity_type,
        entity_id=inv.entity_id,
        counterparty_type=counterparty.get("type") if counterparty.get("type") in ("vendor", "customer", "bank", "payroll") else None,
        counterparty_id=counterparty.get("id"),
        counterparty_name=counterparty.get("name"),
        category=category,
        title=title[:200],
        description=best.description,
        amount=float(observed) if observed is not None else None,
        expected_amount=float(expected) if expected is not None else None,
        difference=difference,
        status=status,
        confidence=best.confidence,
        tier=best_tier,
        agent=agent.name,
        hypothesis_ids=[h.id for h in inv.hypotheses],
        tags=[best.kind, *(["precedent"] if best.rule_id else []), *(["policy"] if best.kind.startswith("policy_") else [])],
    )
    inv.exception = exc
    is_new = exception_id is None
    if is_new:
        ctx.bump("exceptions")
    _mem(ctx, "record_exception", exc)
    for h in inv.hypotheses:
        _mem(ctx, "record_hypothesis", exc.id, h)
    agent.emit("memory.write", f"Recorded exception {exc.id} with {len(inv.hypotheses)} hypotheses", exc.title, exception_id=exc.id, hypothesis_ids=exc.hypothesis_ids)
    if best.kind == "observed_pattern" and counterparty.get("id"):
        imp = best.params.get("implied") or {}
        _mem(ctx, "record_observation", counterparty["type"], counterparty["id"], "implied_adjustment", {"rate": imp.get("rate"), "fixed_diff": imp.get("fixed_diff"), "suggested_pattern": best.params.get("suggested_pattern"), "entity_id": inv.entity_id}, ctx.period_id)
    if is_new:
        agent.emit("exception.raised", f"{category}: {exc.title}", exc.description, exception=exc.model_dump(mode="json"), hypotheses=[_hyp_summary(h) for h in inv.hypotheses], best=_hyp_summary(best), tier=best_tier.value, confidence=best.confidence)
    if is_bank and best_tier != Tier.HIGH:
        agent.try_tool("mark_exception", None, bank_txn_id=inv.entity_id, note=f"{exc.id}: {best.description}"[:300])

    if best_tier == Tier.LOW:
        inv.status = "needs_human"
        ctx.bump("human")
        agent.emit("human.review_requested", f"Human review: {exc.title}", best.description, exception_id=exc.id, category=category, confidence=best.confidence, best=_hyp_summary(best), suggested_pattern=best.params.get("suggested_pattern"), suggested_params=best.params.get("suggested_params"))
        return

    decision = Decision(
        exception_id=exc.id,
        hypothesis_id=best.id,
        action=action,
        matched_ids=[i for i in best.candidate_ids if not i.startswith("BT-")],
        confidence=best.confidence,
        tier=best_tier,
        status=DecisionStatus.PROPOSED,
        agent=agent.name,
        rule_id=best.rule_id if best.kind.startswith("rule_") else None,
        steps=agent.steps,
        period_id=ctx.period_id,
        run_id=ctx.run_id,
    )
    decision.explanation = await _explain(ctx, agent, inv, best, decision, counterparty)
    inv.decision = decision
    inv.matched_ids = list(decision.matched_ids)
    if best_tier == Tier.MEDIUM:
        decision.status = DecisionStatus.PENDING_AUDIT
        _mem(ctx, "record_decision", exc.id, decision)
        if decision.rule_id:
            _precedent_hit(ctx, agent, best, decision)
        ctx.bump("pending_audit")
        inv.status = "pending_audit"
        agent.emit("decision.made", f"{action} {inv.entity_id} — pending audit ({best.confidence:.0%})", decision.explanation, decision=decision.model_dump(mode="json"), hypothesis=_hyp_summary(best), evidence=[e.model_dump(mode="json") for e in best.evidence], exception_id=exc.id)
        return

    # HIGH
    agent.emit("decision.made", f"{action} {inv.entity_id} — executing ({best.confidence:.0%})", decision.explanation, decision=decision.model_dump(mode="json"), hypothesis=_hyp_summary(best), evidence=[e.model_dump(mode="json") for e in best.evidence], exception_id=exc.id)
    ok = await execute_decision(ctx, agent, decision, best, inv.entity)
    inv.executed = ok
    if ok:
        _mem(ctx, "record_decision", exc.id, decision)
        if decision.rule_id:
            _precedent_hit(ctx, agent, best, decision)
        if by != f"agent:{agent.name}":
            _close_exception(ctx, exc.id, by)
        exc.status = ExceptionStatus.RESOLVED
        exc.resolved_by = by
        exc.resolved_at = decision.executed_at
        inv.status = "held" if action == "hold" else ("reconciled" if is_bank else "approved")
        inv.auto_resolved = True
        ctx.bump("auto_resolved")
        agent.emit("exception.resolved", f"{exc.id} resolved by {by}", decision.explanation, exception_id=exc.id, decision_id=decision.id, resolved_by=by, status=inv.status)
    else:
        decision.status = DecisionStatus.ESCALATED
        _mem(ctx, "record_decision", exc.id, decision)
        exc.status = ExceptionStatus.NEEDS_HUMAN
        inv.status = "needs_human"
        ctx.bump("human")
        agent.emit("human.review_requested", f"Execution failed for {inv.entity_id}", decision.explanation, exception_id=exc.id, decision_id=decision.id)


# ============================================================================ execution
def _ledger_lines(h: Hypothesis, bt: dict[str, Any], gross: float, memo: str) -> list[dict[str, Any]]:
    """Balanced journal lines for a bank match: cash vs AR/AP plus fee / discount / FX adjustment."""
    cash = round(abs(float(bt["amount"])), 2)
    inflow = float(bt["amount"]) >= 0
    kinds = {c.get("kind") for c in h.params.get("candidates", [])}
    lines: list[dict[str, Any]] = []
    if h.kind == "bank_fee":
        return [{"account_code": GL_BANK_FEES, "debit": cash, "memo": memo}, {"account_code": GL_CASH, "credit": cash, "memo": memo}]
    if h.kind == "payroll":
        account = GL_PAYROLL_TAX if h.params.get("component") == "employer_taxes" else GL_PAYROLL
        return [{"account_code": account, "debit": cash, "memo": memo}, {"account_code": GL_CASH, "credit": cash, "memo": memo}]
    counter = GL_AR if "ar_invoice" in kinds or (inflow and not kinds & {"ap_invoice", "payment"}) else GL_AP
    adjustment = round((gross - cash) if inflow else (cash - gross), 2)  # > 0 = expense
    if inflow:
        lines.append({"account_code": GL_CASH, "debit": cash, "memo": memo})
    else:
        lines.append({"account_code": counter, "debit": gross, "memo": memo})
    if abs(adjustment) >= 0.005:
        account = ADJUSTMENT_ACCOUNT.get(str(h.params.get("adjustment_kind", "fee")), GL_BANK_FEES)
        if adjustment > 0:
            lines.append({"account_code": account, "debit": adjustment, "memo": f"{memo} — adjustment"})
        else:
            lines.append({"account_code": account, "credit": -adjustment, "memo": f"{memo} — adjustment"})
    if inflow:
        lines.append({"account_code": counter, "credit": gross, "memo": memo})
    else:
        lines.append({"account_code": GL_CASH, "credit": cash, "memo": memo})
    return lines


def _mirror_match(ctx: AgentContext, bt_id: str, matched_ids: list[str], rel: str, decision: Decision, period_id: str) -> None:
    _mem(ctx, "ensure_entity", "Transaction", bt_id, {"period_id": period_id})
    for mid in matched_ids:
        label = "Payment" if mid.startswith(("PAY-", "PR-")) else "Invoice"
        _mem(ctx, "ensure_entity", label, mid, {"period_id": period_id})
        _add_edge(ctx, bt_id, rel, mid, {"decision_id": decision.id, "kind": decision.action})


async def execute_decision(ctx: AgentContext, agent: BaseAgent, decision: Decision, hypothesis: Hypothesis, entity: dict[str, Any]) -> bool:
    """Carry out a decision: reconcile / hold / approve / accept-in-transit, post ledger lines,
    mirror MATCHES edges in memory, mark the decision executed and emit `decision.executed`.

    Returns True on success; on failure the decision is left un-executed and False is returned.
    """
    is_bank = "bank_account" in entity or entity.get("id", "").startswith("BT-")
    memo = f"{hypothesis.kind}: {hypothesis.description}"[:200]
    results: list[Any] = []
    try:
        if decision.action == "reconcile" and is_bank:
            bt_id = entity["id"]
            matched = [i for i in decision.matched_ids if not i.startswith("BT-")]
            gross = float(hypothesis.params.get("gross") or 0.0) or abs(float(entity["amount"]))
            if hypothesis.kind in ("partial_payment", "split_payment"):
                gross = abs(float(entity["amount"]))
            lines = _ledger_lines(hypothesis, entity, gross, memo)
            res = agent.call_tool("mark_reconciled", bank_txn_id=bt_id, matched_ids=matched, note=memo, agent=agent.name, ledger=lines)
            results.append(res)
            ctx.bump("reconciled")
            ctx.bump("matched", len([i for i in matched if not i.startswith("PR-")]))
            rel = "PARTIALLY_MATCHES" if hypothesis.kind in ("partial_payment", "split_payment") else "MATCHES"
            _mirror_match(ctx, bt_id, matched, rel, decision, ctx.period_id)
            sibling = hypothesis.params.get("sibling_txn_id")
            if hypothesis.kind == "split_payment" and sibling:
                sib = agent.try_tool("get_bank_txn", {}, id=sibling)
                if sib and not sib.get("reconciled"):
                    sib_lines = _ledger_lines(hypothesis, sib, abs(float(sib["amount"])), memo)
                    results.append(agent.call_tool("mark_reconciled", bank_txn_id=sibling, matched_ids=matched, note=f"{memo} (second part)", agent=agent.name, ledger=sib_lines))
                    ctx.bump("reconciled")
                    _mirror_match(ctx, sibling, matched, "PARTIALLY_MATCHES", decision, ctx.period_id)
        elif decision.action == "hold":
            results.append(agent.call_tool("hold_invoice", ap_invoice_id=entity["id"], reason=hypothesis.description[:200]))
            ctx.bump("held")
            for other in decision.matched_ids:
                _mem(ctx, "ensure_entity", "Invoice", other, {"period_id": ctx.period_id})
                _add_edge(ctx, entity["id"], "DUPLICATE_OF", other, {"decision_id": decision.id})
        elif decision.action == "void":
            results.append(agent.call_tool("void_invoice", ap_invoice_id=entity["id"], reason=hypothesis.description[:200]))
        elif decision.action == "approve":
            results.append(agent.call_tool("approve_invoice", ap_invoice_id=entity["id"]))
            if hypothesis.params.get("po_id"):
                _add_edge(ctx, entity["id"], "APPROVED_BY", decision.id, {"po_id": hypothesis.params["po_id"]})
        elif decision.action == "accept_in_transit":
            for pid in [i for i in decision.matched_ids if i.startswith("PAY-")]:
                results.append(agent.call_tool("mark_in_transit", payment_id=pid, note=hypothesis.description[:200]))
                _mem(ctx, "ensure_entity", "Payment", pid, {"period_id": ctx.period_id})
                _add_edge(ctx, entity["id"], "MATCHES", pid, {"decision_id": decision.id, "kind": "timing_lag"})
        elif decision.action in ("escalate", "dismiss"):
            pass
        else:
            raise ValueError(f"unsupported action {decision.action}")
    except Exception as exc:  # noqa: BLE001
        log.warning("execute_decision failed for %s: %s", entity.get("id"), exc, exc_info=True)
        agent.emit("decision.executed", f"Execution failed: {decision.action} {entity.get('id')}", str(exc)[:300], decision_id=decision.id, ok=False, error=str(exc))
        return False
    decision.status = DecisionStatus.EXECUTED
    decision.executed_at = datetime.now(timezone.utc)
    agent.emit(
        "decision.executed",
        f"{decision.action} {entity.get('id')} ✓ ({hypothesis.kind}, {decision.confidence:.0%})",
        decision.explanation or hypothesis.description,
        decision_id=decision.id,
        decision=decision.model_dump(mode="json"),
        hypothesis=_hyp_summary(hypothesis),
        matched_ids=decision.matched_ids,
        rule_id=decision.rule_id,
        results=[jsonable(r) for r in results][:3],
        ok=True,
    )
    return True


# ============================================================================ AP invoice investigation
def _invoice_usd(inv: dict[str, Any]) -> float:
    amount = float(inv.get("amount") or 0.0)
    if (inv.get("currency") or "USD") != "USD" and inv.get("fx_rate"):
        return round(amount * float(inv["fx_rate"]), 2)
    return round(amount, 2)


async def investigate_ap_invoice(ctx: AgentContext, agent: BaseAgent, ap_invoice_id: str, exception_id: str | None = None, resolved_by: str | None = None) -> Investigation:
    """AP-side controls for one vendor bill: duplicate, no-PO policy, price variance, timing lag.

    Clean bills (3-way match within tolerance or below the PO threshold) are approved directly.
    """
    t0 = perf_counter()
    steps0 = agent.steps
    inv = Investigation(entity_type="ap_invoice", entity_id=ap_invoice_id, period_id=ctx.period_id)
    row = agent.try_tool("get_ap_invoice", {}, id=ap_invoice_id)
    if not row:
        inv.status = "missing"
        inv.hypotheses = [_announce(ctx, agent, _hyp("unknown", f"AP invoice {ap_invoice_id} not found", candidate_ids=[], evidence=[_ev("no_match", "entity missing", supports=False)], passed=True, confidence=score("unknown")))]
        inv.best = inv.hypotheses[0]
        inv.steps, inv.ms = agent.steps - steps0, (perf_counter() - t0) * 1000
        return inv
    inv.entity = row
    if row.get("status") in ("paid", "void", "held"):
        inv.status = "skipped"
        if exception_id and row.get("status") != "held":
            _close_exception(ctx, exception_id, resolved_by or f"agent:{agent.name}")
        inv.steps, inv.ms = agent.steps - steps0, (perf_counter() - t0) * 1000
        return inv

    ctx.bump("items")
    vendor = agent.try_tool("get_vendor", {}, vendor_id=row["vendor_id"]) or {}
    counterparty = {"type": "vendor", "id": row["vendor_id"], "name": vendor.get("name") or row["vendor_id"], "confidence": 1.0, "method": "invoice", "is_payment_processor": bool(vendor.get("is_payment_processor"))}
    inv.counterparty = counterparty
    usd = _invoice_usd(row)
    agent.emit("agent.step", f"Checking {row['id']} {_money(usd)} from {counterparty['name']}", str(row.get("description", ""))[:200], ap_invoice=row)
    hyps: list[Hypothesis] = []

    # duplicate
    dups = agent.try_tool("find_duplicates", [], ap_invoice_id=row["id"]) or []
    if dups:
        original = sorted([*dups, row], key=lambda r: (r["date"], r["id"]))[0]
        others_held = any(d.get("status") in ("held", "void") for d in dups)
        i_am_copy = original["id"] != row["id"] and not others_held
        other = dups[0] if original["id"] == row["id"] else original
        evidence = [
            _ev("duplicate_scan", f"{other['id']} (vendor no. {other.get('vendor_invoice_number')}) from {counterparty['name']} has the same amount {_money(float(other['amount']))} within 10 days ({other['date']} vs {row['date']})", supports=True, other=other["id"]),
            _ev("ordering", "this bill is the later copy" if i_am_copy else ("the later copy is already held/void" if others_held else "this bill is the original; the later copy will be held"), supports=i_am_copy),
        ]
        desc = f"{row['id']} duplicates {other['id']} (same vendor, amount {_money(float(row['amount']))}, different invoice number {row.get('vendor_invoice_number')} vs {other.get('vendor_invoice_number')})"
        hyps.append(_announce(ctx, agent, _hyp("duplicate", desc, candidate_ids=[other["id"]], evidence=evidence, passed=i_am_copy, params={"observed": usd, "expected": usd, "difference": 0.0, "duplicate_of": other["id"], "entity_type": "ap_invoice"})))

    # policy: no PO above threshold
    if not row.get("po_id") and usd > settings.no_po_threshold_usd:
        evidence = [_ev("policy", f"{_money(usd)} exceeds the {_money(settings.no_po_threshold_usd)} no-PO threshold and no purchase order is attached", supports=True, threshold=settings.no_po_threshold_usd)]
        hyps.append(_announce(ctx, agent, _hyp("policy_no_po", f"{row['id']} {_money(usd)} has no PO — policy requires human approval", candidate_ids=[], evidence=evidence, passed=True, confidence=score("policy_no_po"), params={"observed": usd, "expected": None, "force_tier": Tier.LOW.value, "entity_type": "ap_invoice", "threshold": settings.no_po_threshold_usd})))

    # 3-way match / price variance
    twm = agent.try_tool("three_way_match", {}, ap_invoice_id=row["id"]) or {}
    variance = twm.get("variance_pct")
    if twm.get("has_po") and variance is not None and variance > settings.price_tolerance_pct + 1e-9:
        po_amount = float(twm["po"]["amount"])
        rules = _load_rules(ctx, agent, counterparty, ("price_tolerance",))
        covering = [r for r in rules if variance <= _norm_rate((r["params"] or {}).get("tolerance_pct", (r["params"] or {}).get("tolerance", 0))) + 1e-9]
        base_evidence = [
            _ev("three_way_match", f"invoice {_money(float(row['amount']))} vs PO {twm['po']['id']} {_money(po_amount)}: +{variance:.1%} (tolerance {settings.price_tolerance_pct:.0%})", supports=False, variance_pct=variance, po_id=twm["po"]["id"], received_total=twm.get("received_total")),
        ]
        if covering:
            r = covering[0]
            evidence = [*base_evidence, _ev("precedent", f"rule {r['id']} v{r['version']} allows up to {_norm_rate(r['params'].get('tolerance_pct', 0)):.1%} for {counterparty['name']} ({_trust_text(r)})", rule_id=r["id"], trust=r["trust"])]
            hyps.append(_announce(ctx, agent, _hyp("rule_price_tolerance", f"+{variance:.1%} over PO is within the learned {counterparty['name']} tolerance (rule {r['id']} v{r['version']})", candidate_ids=[twm["po"]["id"]], evidence=evidence, passed=True, confidence=score("rule_price_tolerance", trust=r["trust"]), rule_id=r["id"], generated_by="memory", params={"observed": usd, "expected": po_amount, "difference": round(usd - po_amount, 2), "po_id": twm["po"]["id"], "variance_pct": variance, "entity_type": "ap_invoice", "rule": {k: r[k] for k in ("id", "pattern_type", "params", "version", "trust", "description", "learned_in_period")}})))
        else:
            evidence = [*base_evidence, _ev("precedent", f"no price-tolerance rule for {counterparty['name']} covers +{variance:.1%}", supports=False)]
            hyps.append(_announce(ctx, agent, _hyp("policy_price_variance", f"{row['id']} exceeds PO {twm['po']['id']} by {variance:.1%} — needs human approval", candidate_ids=[twm["po"]["id"]], evidence=evidence, passed=True, confidence=score("policy_price_variance"), params={"observed": usd, "expected": po_amount, "difference": round(usd - po_amount, 2), "po_id": twm["po"]["id"], "variance_pct": variance, "force_tier": Tier.LOW.value, "entity_type": "ap_invoice"})))
    elif twm.get("has_po"):
        hyps.append(_announce(ctx, agent, _hyp("policy_price_variance", f"{row['id']} matches PO {twm['po']['id']} within tolerance", candidate_ids=[twm["po"]["id"]], evidence=[_ev("three_way_match", f"variance {variance:+.1%} within {settings.price_tolerance_pct:.0%}; received {_money(float(twm.get('received_total') or 0))}", supports=True, variance_pct=variance)], passed=False, params={"po_id": twm["po"]["id"], "variance_pct": variance, "entity_type": "ap_invoice"})))

    # timing lag: payment in transit at period end
    period = agent.try_tool("get_period", {}, period_id=ctx.period_id) or {}
    payments = [p for p in (agent.try_tool("list_payments", [], vendor_id=row["vendor_id"]) or []) if row["id"] in (p.get("ap_invoice_ids") or [])]
    in_transit = [p for p in payments if p.get("bank_txn_id") is None and p.get("status") in ("sent", "scheduled")]
    if in_transit and period.get("end_date"):
        p = in_transit[0]
        end = datetime.fromisoformat(period["end_date"]).date()
        sent = datetime.fromisoformat(p["date"]).date()
        late = 0 <= (end - sent).days <= IN_TRANSIT_DAYS
        evidence = [
            _ev("timing", f"payment {p['id']} {_money(float(p['amount']))} sent {p['date']} via {p.get('method')} — {(end - sent).days} day(s) before period end, not yet on the bank statement", supports=late, sent=p["date"], period_end=period["end_date"]),
            _ev("amount_match", f"payment {_money(float(p['amount']))} vs invoice {_money(usd)}", supports=abs(float(p["amount"]) - usd) <= RULE_TOL + 0.03 * usd, expected=usd, observed=p["amount"]),
        ]
        hyps.append(_announce(ctx, agent, _hyp("timing_lag", f"{row['id']} paid by {p['id']} on {p['date']} — clears next period (timing lag)", candidate_ids=[p["id"]], evidence=evidence, passed=late, params={"observed": float(p["amount"]), "expected": usd, "difference": round(float(p["amount"]) - usd, 2), "gross": usd, "payment_id": p["id"], "entity_type": "ap_invoice"})))

    inv.hypotheses = hyps
    inv.best = _pick_best(hyps)
    if inv.best is None:
        inv.status = "clean"
        if row.get("status") == "open":
            agent.try_tool("approve_invoice", None, ap_invoice_id=row["id"])
            inv.status = "approved"
        if exception_id:
            _close_exception(ctx, exception_id, resolved_by or f"agent:{agent.name}")
        inv.steps, inv.ms = agent.steps - steps0, (perf_counter() - t0) * 1000
        return inv

    best = inv.best
    category = {"duplicate": "duplicate", "policy_no_po": "policy", "policy_price_variance": "price_variance", "rule_price_tolerance": "price_variance", "timing_lag": "timing"}.get(best.kind, "unexplained")
    ap_info = {"category": category, "title": f"{row['id']} {_money(usd)} from {counterparty['name']}: {best.kind.replace('_', ' ')}"}
    await _conclude(ctx, agent, inv, None, exception_id, resolved_by, ap_info=ap_info)
    inv.steps, inv.ms = agent.steps - steps0, (perf_counter() - t0) * 1000
    await ctx.pace()
    return inv


# ============================================================================ re-investigation
async def reinvestigate_exception(ctx: AgentContext, agent: BaseAgent, exception_id: str, resolved_by: str = "propagation") -> Investigation:
    """Reload an open exception's entity and re-run the loop with the CURRENT memory.

    Used by teach-once propagation and audit escalation: a rule learned a minute ago resolves
    sibling exceptions here. Returns the new Investigation (status tells what happened).
    """
    detail = _mem(ctx, "exception_detail", exception_id, default=None)
    view = (detail or {}).get("exception") if isinstance(detail, dict) and "exception" in (detail or {}) else detail
    if not view:
        inv = Investigation(entity_type="unknown", entity_id=exception_id, period_id=ctx.period_id, status="missing")
        inv.hypotheses = [_hyp("unknown", f"exception {exception_id} not found in memory", candidate_ids=[], evidence=[_ev("no_match", "exception missing", supports=False)], passed=True, confidence=score("unknown"))]
        inv.best = inv.hypotheses[0]
        return inv
    entity_type = view.get("entity_type")
    entity_id = view.get("entity_id")
    period_id = view.get("period_id") or ctx.period_id
    if period_id != ctx.period_id:
        ctx = AgentContext(run_id=ctx.run_id, period_id=period_id, memory=ctx.memory, brain=ctx.brain, session_factory=ctx.session_factory, counters=ctx.counters, step_delay_ms=ctx.step_delay_ms, extra=ctx.extra)
    agent.emit("propagation", f"Re-investigating {exception_id} ({entity_id}) with current memory", view.get("title", ""), exception_id=exception_id, entity_id=entity_id)
    if entity_type == "bank_transaction":
        inv = await investigate_bank_txn(ctx, agent, entity_id, exception_id=exception_id, resolved_by=resolved_by)
    elif entity_type == "ap_invoice":
        inv = await investigate_ap_invoice(ctx, agent, entity_id, exception_id=exception_id, resolved_by=resolved_by)
    else:
        inv = Investigation(entity_type=entity_type or "unknown", entity_id=entity_id or exception_id, period_id=period_id, status="skipped")
    if inv.auto_resolved:
        ctx.bump("propagated")
    return inv
