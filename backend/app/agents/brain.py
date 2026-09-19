"""Brain: the pluggable reasoning layer (ARCHITECTURE §4).

* `HeuristicBrain` — deterministic, network-free. Proposes no extra hypotheses, writes
  templated narratives from evidence, parses teaching phrases with regexes and judges audit
  checks mechanically.
* `OpenAIBrain` — built on the OpenAI Agents SDK (`agents` package): one `Agent` per role with
  read-only function tools wrapping `tools.registry`, structured outputs via pydantic
  `output_type`, token/cost accounting into the run counters, and a fallback to
  `HeuristicBrain` on ANY exception (logged + `brain.fallback` event).

All planted cases resolve identically with either brain: the LLM only adds novel-hypothesis
proposals (which are still evidence-tested by the engine) and better prose.
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import typing
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.agents.base import Tool, jsonable
from app.agents.tools import registry
from app.config import settings
from app.db.session import new_session
from app.events import emit as _emit_event
from app.schemas import AuditCheck, AuditVerdict, HypothesisSpec, PatternType, RuleSpec, ScopeType

log = logging.getLogger(__name__)


# ============================================================================ protocol
@runtime_checkable
class Brain(Protocol):
    """Reasoning interface every agent talks to (see ARCHITECTURE §4)."""

    name: str

    async def propose_hypotheses(self, ctx: dict) -> list[HypothesisSpec]: ...

    async def explain_decision(self, ctx: dict) -> str: ...

    async def parse_correction(self, text: str, ctx: dict) -> RuleSpec | None: ...

    async def audit_challenge(self, ctx: dict) -> AuditVerdict: ...

    async def narrate_report(self, ctx: dict) -> str: ...


# ============================================================================ helpers
def _money(x: Any) -> str:
    try:
        return f"${float(x):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _trust_text(rule: dict[str, Any] | None) -> str:
    if not rule:
        return ""
    t = rule.get("trust") or {}
    bits = ["human-verified" if t.get("human_verified") else "agent-learned"]
    if t.get("times_confirmed"):
        bits.append(f"confirmed {t['times_confirmed']}×")
    if t.get("times_refuted"):
        bits.append(f"refuted {t['times_refuted']}×")
    return ", ".join(bits)


# ============================================================================ heuristic brain
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent|pct|per\s*cent)", re.I)
_DOLLAR_RE = re.compile(r"(?:\$|usd\s*)\s*(\d+(?:,\d{3})*(?:\.\d+)?)|(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:dollars?|usd|bucks)", re.I)
_DAYS_RE = re.compile(r"(\d+)\s*(?:business\s+|calendar\s+)?days?", re.I)
_DEDUCT_WORDS = ("deduct", "withhold", "net of", "less ", "minus", "takes", "keeps", "retain", "short", "nets", "subtract", "discount", "pays less", "pay less")
_ADD_WORDS = ("add", "surcharge", "extra", "on top", "plus", "markup", "mark-up", "charges more", "charge more", "premium", "over the invoice")
_FX_WORDS = ("fx", "exchange", "currency", "eur", "gbp", "forex", "conversion")
_DISCOUNT_WORDS = ("discount", "early pay", "early-pay", "prompt pay", "prompt-pay", "2/10")
_FEE_WORDS = ("fee", "surcharge", "commission", "processing", "charge", "processor", "cost")
_PRICE_WORDS = ("price", "po ", "purchase order", "unit cost", "quote")
_TIMING_WORDS = ("timing", "in transit", "clear", "lag", "settle")
_APPROVAL_WORDS = ("approve", "approval", "no po", "without po", "authorize", "sign-off", "sign off")
_GLOBAL_WORDS = ("all vendors", "all customers", "every vendor", "every customer", "globally", "any vendor", "any customer", "company-wide", "companywide")


class HeuristicBrain:
    """Deterministic brain: templated prose, regex teaching parser, mechanical audit."""

    name = "heuristic"

    async def propose_hypotheses(self, ctx: dict) -> list[HypothesisSpec]:
        """The heuristic engine never proposes beyond the built-in generators."""
        return []

    async def explain_decision(self, ctx: dict) -> str:
        """Precise narrative built from the winning hypothesis and its evidence."""
        return explain_from_evidence(ctx)

    async def parse_correction(self, text: str, ctx: dict) -> RuleSpec | None:
        """Regex / keyword parser for teaching phrases → RuleSpec (None when unparseable)."""
        return parse_rule_text(text, ctx)

    async def audit_challenge(self, ctx: dict) -> AuditVerdict:
        """Verdict from the re-performed checks plus rule-trust guard rails."""
        return audit_from_checks(ctx)

    async def narrate_report(self, ctx: dict) -> str:
        """Templated close narrative from metrics, sections and open items."""
        return narrate_from_metrics(ctx)


_KIND_PHRASES: dict[str, str] = {
    "exact_match": "settles {ids} referenced in the memo for exactly {gross}",
    "counterparty_exact": "settles {ids} — the only open item of {cp} at exactly {gross}",
    "combined_invoices": "settles {n} invoices of {cp} ({ids}) which sum to exactly {gross}",
    "split_payment": "is one of two transfers that together settle {ids} ({gross})",
    "partial_payment": "is a partial payment of {ids} ({gross}); the remainder stays open",
    "timing_lag": "is the bank clearing of {ids} initiated late in the previous period",
    "payroll": "is payroll run {ids} ({gross})",
    "bank_fee": "is a bank service fee with no counterparty",
    "duplicate": "duplicates {ids}: same vendor, same amount within 10 days, different invoice number — placed on hold",
    "rule_percentage_fee": "settles {ids} ({gross}) net of the {rate} fee learned as rule {rule_id} v{rule_version}",
    "rule_fixed_fee": "settles {ids} ({gross}) after the {amount} fixed fee learned as rule {rule_id} v{rule_version}",
    "rule_early_pay_discount": "settles {ids} ({gross}) less the {rate} early-payment discount learned as rule {rule_id} v{rule_version}",
    "rule_fx_tolerance": "settles {ids} ({gross}) within the {tolerance} FX tolerance learned as rule {rule_id} v{rule_version}",
    "rule_price_tolerance": "exceeds its PO within the price tolerance learned as rule {rule_id} v{rule_version}",
    "precedent_drift": "nearly fits rule {rule_id} v{rule_version}: {description}",
    "observed_pattern": "shows a pattern with no precedent: {description}",
    "policy_no_po": "has no purchase order above the policy threshold — human approval is mandatory",
    "policy_price_variance": "exceeds its purchase order beyond tolerance — human approval required",
    "llm_proposed": "was explained by an LLM proposal that passed evidence testing: {description}",
    "unknown": "could not be explained: {description}",
}


def explain_from_evidence(ctx: dict) -> str:
    """Build a decision narrative from an explain-context dict (see hypotheses._explain_ctx)."""
    entity = ctx.get("entity") or {}
    etype = ctx.get("entity_type") or "item"
    h = ctx.get("hypothesis") or {}
    kind = h.get("kind", "unknown")
    params = h.get("params") or {}
    cp = ctx.get("counterparty") or {}
    rule = ctx.get("rule") or params.get("rule") or {}
    matched = [m for m in (ctx.get("matched") or params.get("candidates") or []) if isinstance(m, dict)]
    ids = ", ".join(m.get("id", "?") for m in matched) or ", ".join(h.get("candidate_ids") or []) or "no open item"
    gross = _money(params.get("gross") if params.get("gross") is not None else ctx.get("expected"))
    fmt = {
        "ids": ids,
        "n": len(matched),
        "cp": cp.get("name") or cp.get("id") or "the counterparty",
        "gross": gross,
        "rate": _pct(params.get("rate")),
        "amount": _money(params.get("amount")),
        "tolerance": _pct(params.get("tolerance")),
        "rule_id": rule.get("id", "?"),
        "rule_version": rule.get("version", "?"),
        "description": h.get("description", ""),
    }
    if etype == "bank_transaction":
        what = f"Bank transaction {entity.get('id', '?')} ({_money(entity.get('amount'))} on {entity.get('date', '?')}, memo '{str(entity.get('description', ''))[:60]}')"
    elif etype == "ap_invoice":
        what = f"AP invoice {entity.get('id', '?')} ({_money(entity.get('amount'))} {entity.get('currency', 'USD')} from {fmt['cp']}, dated {entity.get('date', '?')})"
    else:
        what = f"{etype} {entity.get('id', '?')}"
    conclusion = _KIND_PHRASES.get(kind, "was assessed as {description}").format(**fmt)
    supporting = [e.get("description", "") for e in (ctx.get("evidence") or []) if e.get("supports")]
    against = [e.get("description", "") for e in (ctx.get("evidence") or []) if not e.get("supports")]
    ev = ""
    if supporting:
        ev = " Evidence: " + "; ".join(s for s in supporting[:4] if s) + "."
    if against:
        ev += " Against: " + "; ".join(a for a in against[:2] if a) + "."
    conf = float(ctx.get("confidence") or h.get("confidence") or 0.0)
    tier_ = str(ctx.get("tier") or "low").upper()
    outcome = {"HIGH": "executed autonomously", "MEDIUM": "queued for the Audit agent to re-perform before posting", "LOW": "routed to a human — no rate-based adjustment is ever guessed"}.get(tier_, "recorded")
    text = f"{what} {conclusion}.{ev} Confidence {conf:.0%} ({tier_}) → {outcome}."
    if rule:
        text += f" Precedent: rule {rule.get('id')} v{rule.get('version', '?')} ({_trust_text(rule)}), learned in {rule.get('learned_in_period') or 'an earlier period'}."
    diff = ctx.get("difference")
    if diff is not None and abs(float(diff)) >= 0.005 and kind not in ("rule_percentage_fee", "rule_fixed_fee", "rule_early_pay_discount", "rule_fx_tolerance"):
        text += f" Unexplained difference: {float(diff):+,.2f}."
    return text


def parse_rule_text(text: str, ctx: dict | None = None) -> RuleSpec | None:
    """Parse a human teaching phrase into a RuleSpec.

    Handles "Stripe deducts a 3% processing fee", "$25 wire fee", "2% early payment discount",
    "accept up to 1.5% FX variance", "2.5% surcharge", "7 day timing window", price tolerance
    and approval policies. Scope comes from ctx (counterparty_type / counterparty_id) unless
    the text says it applies to all vendors/customers.
    """
    ctx = ctx or {}
    raw = (text or "").strip()
    if not raw:
        return None
    low = " " + raw.lower() + " "
    pct_m = _PERCENT_RE.search(raw)
    dollar_m = _DOLLAR_RE.search(raw)
    days_m = _DAYS_RE.search(raw)
    rate = float(pct_m.group(1)) / 100.0 if pct_m else None
    amount = None
    if dollar_m:
        amount = float((dollar_m.group(1) or dollar_m.group(2)).replace(",", ""))
    has = lambda words: any(w in low for w in words)  # noqa: E731

    if has(_DEDUCT_WORDS):
        direction = "deduct"
    elif has(_ADD_WORDS):
        direction = "add"
    else:
        direction = None

    pattern: PatternType | None = None
    params: dict[str, Any] = {}
    if rate is not None and has(_FX_WORDS):
        pattern, params = PatternType.FX_TOLERANCE, {"tolerance_pct": rate}
    elif rate is not None and has(_DISCOUNT_WORDS):
        pattern, params = PatternType.EARLY_PAY_DISCOUNT, {"rate": rate, "direction": "deduct"}
    elif rate is not None and has(_PRICE_WORDS) and not has(_FEE_WORDS):
        pattern, params = PatternType.PRICE_TOLERANCE, {"tolerance_pct": rate}
    elif rate is not None:
        pattern = PatternType.PERCENTAGE_FEE
        params = {"rate": rate, "direction": direction or ("add" if "surcharge" in low else "deduct")}
    elif amount is not None:
        pattern = PatternType.FIXED_FEE
        params = {"amount": amount, "direction": direction or "either"}
    elif days_m and has(_TIMING_WORDS):
        pattern, params = PatternType.TIMING_WINDOW, {"days": int(days_m.group(1))}
    elif has(_APPROVAL_WORDS):
        pattern = PatternType.APPROVAL_POLICY
        params = {"requires_human": True}
        if amount is not None:
            params["threshold"] = amount
    if pattern is None:
        return None

    cp_type = ctx.get("counterparty_type") or (ctx.get("counterparty") or {}).get("type")
    cp_id = ctx.get("counterparty_id") or (ctx.get("counterparty") or {}).get("id")
    if has(_GLOBAL_WORDS) or cp_type not in ("vendor", "customer") or not cp_id:
        scope_type, scope_id = ScopeType.GLOBAL, None
    else:
        scope_type, scope_id = ScopeType(cp_type), cp_id
    description = raw[0].upper() + raw[1:]
    return RuleSpec(pattern_type=pattern, scope_type=scope_type, scope_id=scope_id, params=params, description=description[:200])


_CRITICAL_CHECKS = ("amount", "arithmetic", "ledger", "balanced", "candidate", "recomput")


def audit_from_checks(ctx: dict) -> AuditVerdict:
    """Mechanical audit verdict: all checks pass → approved (unless the rule is too weak);
    a critical (amount / ledger / candidate) check fails → rejected; otherwise escalated."""
    raw_checks = ctx.get("checks") or []
    checks = [c if isinstance(c, AuditCheck) else AuditCheck.model_validate(c) for c in raw_checks]
    rule = ctx.get("rule") or {}
    decision = ctx.get("decision") or {}
    confidence = float(decision.get("confidence") or ctx.get("confidence") or 0.0)
    passed = [c for c in checks if c.passed]
    failed = [c for c in checks if not c.passed]
    summary = f"{len(passed)}/{len(checks)} checks passed"
    if not checks:
        return AuditVerdict(verdict="escalated", reasoning="No re-performance checks were available; escalating to a human.", checks=checks)
    if failed:
        names = ", ".join(f"{c.name} ({c.detail})" if c.detail else c.name for c in failed)
        critical = any(any(k in c.name.lower() for k in _CRITICAL_CHECKS) for c in failed)
        if critical:
            return AuditVerdict(verdict="rejected", reasoning=f"{summary}; failed: {names}. A critical re-computation disagrees with the decision, so it is rejected.", checks=checks)
        return AuditVerdict(verdict="escalated", reasoning=f"{summary}; failed: {names}. Escalating to a human.", checks=checks)
    trust = rule.get("trust") or {}
    trust_score = rule.get("trust_score")
    if rule and trust_score is not None and float(trust_score) < 0.5 and not trust.get("human_verified"):
        return AuditVerdict(verdict="escalated", reasoning=f"{summary}, but rule {rule.get('id')} has trust {float(trust_score):.2f} and was never human-verified; escalating.", checks=checks)
    if rule and int(trust.get("times_refuted", 0) or 0) > int(trust.get("times_confirmed", 0) or 0):
        return AuditVerdict(verdict="escalated", reasoning=f"{summary}, but rule {rule.get('id')} has been refuted more often than confirmed; escalating.", checks=checks)
    precedent = f" Precedent rule {rule.get('id')} v{rule.get('version', '?')} ({_trust_text(rule)}) confirmed." if rule else ""
    return AuditVerdict(verdict="approved", reasoning=f"{summary}; re-performed evidence agrees with the decision at {confidence:.0%} confidence.{precedent}", checks=checks)


def narrate_from_metrics(ctx: dict) -> str:
    """Templated close narrative: what closed, what memory changed, what needs a human."""
    m = ctx.get("metrics") or {}
    period = ctx.get("period_name") or ctx.get("period_id") or m.get("period_id") or "the period"
    total = int(m.get("total_items") or 0)
    auto = int(m.get("auto_resolved") or 0)
    human = int(m.get("human_reviews") or 0)
    hits = int(m.get("precedent_hits") or 0)
    audits = int(m.get("audit_challenges") or 0)
    audit_ok = int(m.get("audit_passed") or 0)
    learned = int(m.get("rules_learned") or 0)
    acc = m.get("accuracy")
    parts = [
        f"Close of {period}: {total} items were investigated; {auto} were resolved autonomously and {human} were routed to a human."
    ]
    if hits:
        parts.append(f"{hits} decision(s) reused learned precedent from the Financial Memory Graph — the rules taught earlier are now doing the work.")
    if audits:
        parts.append(f"The Audit agent re-performed {audits} medium-confidence decision(s) and confirmed {audit_ok}.")
    if learned:
        parts.append(f"{learned} new rule(s) were learned this period.")
    if acc is not None:
        parts.append(f"Accuracy against the hidden ground truth on decided items: {float(acc):.0%}.")
    open_items = ctx.get("open_items") or []
    if open_items:
        heads = "; ".join(str(o.get("title") or o.get("id") or o) for o in open_items[:5])
        parts.append(f"Still open for the controller: {heads}.")
    for sec in ctx.get("sections") or []:
        if isinstance(sec, dict) and sec.get("body"):
            parts.append(f"{sec.get('title', '')}: {sec['body']}".strip())
    return " ".join(parts)


# ============================================================================ OpenAI brain
_PRICES_PER_M: dict[str, tuple[float, float]] = {  # USD per 1M input / output tokens
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "o4-mini": (1.10, 4.40),
}
_DEFAULT_PRICE = (0.50, 2.00)
_PROMPT_LIMIT = 14_000


class LLMHypothesis(BaseModel):
    """A hypothesis the investigator proposes (strict-schema friendly, no free-form dicts)."""

    kind: str = Field(description="exact_match | counterparty_exact | combined_invoices | split_payment | partial_payment | timing_lag | refund | reversal | custom")
    description: str
    candidate_ids: list[str] = Field(default_factory=list, description="invoice / payment ids this explains")
    rate: float | None = Field(default=None, description="fractional rate adjustment, e.g. 0.03")
    fixed_amount: float | None = Field(default=None, description="fixed USD adjustment")
    direction: str | None = Field(default=None, description="deduct | add")
    adjustment_kind: str | None = Field(default=None, description="fee | discount | fx")
    rule_id: str | None = None


class LLMHypothesisList(BaseModel):
    hypotheses: list[LLMHypothesis] = Field(default_factory=list)


class LLMRule(BaseModel):
    """Structured teaching parse (params flattened so the schema stays strict)."""

    parseable: bool
    pattern_type: str = Field(description="percentage_fee | fixed_fee | early_pay_discount | fx_tolerance | timing_window | price_tolerance | approval_policy | custom")
    scope_type: str = Field(description="vendor | customer | account | global")
    scope_id: str | None = None
    rate: float | None = None
    amount: float | None = None
    tolerance_pct: float | None = None
    days: int | None = None
    direction: str | None = None
    description: str


_ROLE_INSTRUCTIONS: dict[str, str] = {
    "investigator": (
        "You are the Reconciliation investigator in an AI Office of the CFO. You receive a bank transaction, "
        "its resolved counterparty, the open candidate items, learned rules and the hypotheses already tested. "
        "Propose up to 3 NOVEL, testable hypotheses the built-in generators missed (refunds, reversals, credit "
        "memos, multi-invoice nets, cross-period items). Use the read-only tools to look things up. Never "
        "propose a rate-based fee/discount/FX explanation unless a learned rule supports it. Name concrete "
        "candidate ids; the engine will verify the arithmetic."
    ),
    "explainer": (
        "You are the finance narrator. Write ONE crisp paragraph (max 120 words) explaining the decision for a "
        "controller: the transaction, what it matched, the decisive evidence with numbers, the precedent rule if "
        "any (id, version, trust), the confidence tier and what happens next. No speculation beyond the evidence."
    ),
    "teacher": (
        "You translate a controller's correction into a structured rule. Percentages become fractional rates, "
        "'$25' becomes amount 25, 'deducts/withholds/net of' → direction deduct, 'surcharge/adds' → direction add. "
        "FX/exchange/currency variance → fx_tolerance; discount → early_pay_discount; price/PO variance → "
        "price_tolerance. Scope defaults to the counterparty given in context unless the text says all vendors/"
        "customers. Set parseable=false when the text describes no rule."
    ),
    "auditor": (
        "You are the skeptical Audit agent. You get a decision, its hypothesis, evidence, the rule used and the "
        "re-performed checks. Verify the arithmetic with the tools when useful. Verdict: approved when every check "
        "passes and the precedent is trustworthy; rejected when a recomputation contradicts the decision; "
        "escalated otherwise. Give concise reasoning and echo the checks."
    ),
    "reporter": (
        "You write the period-close report narrative for a CFO: what closed, what the agents resolved, which "
        "precedents were reused, what the audit found, what still needs a human, and forecast notes. Plain, "
        "specific, numbers-first, no fluff, max 250 words."
    ),
}


class OpenAIBrain:
    """Brain backed by the OpenAI Agents SDK; degrades to `HeuristicBrain` on any exception."""

    name = "openai"

    def __init__(self, model: str | None = None, session_factory: Callable[[], Any] | None = None, fallback: HeuristicBrain | None = None) -> None:
        import agents as sdk  # OpenAI Agents SDK (top-level package `agents`)

        self._sdk = sdk
        self.model = model or settings.openai_model
        self._fallback = fallback or HeuristicBrain()
        self._session_factory = session_factory or new_session
        self._agents: dict[str, Any] = {}
        self._tools: list[Any] | None = None
        self.total_tokens = 0
        self.total_cost_usd = 0.0
        self.calls = 0
        self.fallbacks = 0
        if settings.openai_api_key:
            sdk.set_default_openai_key(settings.openai_api_key)
        sdk.set_tracing_disabled(True)

    # -- tools ----------------------------------------------------------------
    def _wrap_tool(self, tool: Tool) -> Any:
        sig = inspect.signature(tool.fn)
        params = [p for p in sig.parameters.values() if p.name != "session"]
        new_sig = sig.replace(parameters=params, return_annotation=str)
        hints = {k: v for k, v in typing.get_type_hints(tool.fn).items() if k not in ("session", "return")}
        factory = self._session_factory

        def wrapper(*args: Any, **kwargs: Any) -> str:
            bound = new_sig.bind(*args, **kwargs)
            bound.apply_defaults()
            if tool.requires_session:
                s = factory()
                try:
                    result = tool.fn(s, **bound.arguments)
                    s.commit()
                except Exception:
                    s.rollback()
                    raise
                finally:
                    s.close()
            else:
                result = tool.fn(**bound.arguments)
            return json.dumps(jsonable(result), default=str)[:8000]

        wrapper.__name__ = tool.name
        wrapper.__qualname__ = tool.name
        wrapper.__doc__ = tool.description or tool.fn.__doc__
        wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
        wrapper.__annotations__ = {**hints, "return": str}
        return self._sdk.function_tool(wrapper, name_override=tool.name, description_override=tool.description or tool.name, strict_mode=False)

    def tools(self) -> list[Any]:
        """Read-only registry tools as SDK function tools (built once)."""
        if self._tools is None:
            built = []
            for tool in registry.tools(include_mutating=False):
                try:
                    built.append(self._wrap_tool(tool))
                except Exception as exc:  # noqa: BLE001
                    log.warning("skipping tool %s for the LLM: %s", tool.name, exc)
            self._tools = built
        return self._tools

    def agent(self, role: str) -> Any:
        """The SDK Agent for a role (investigator, explainer, teacher, auditor, reporter)."""
        if role not in self._agents:
            output_type = {"investigator": LLMHypothesisList, "teacher": LLMRule, "auditor": AuditVerdict}.get(role)
            use_tools = role in ("investigator", "auditor")
            self._agents[role] = self._sdk.Agent(
                name=f"cfo-{role}",
                instructions=_ROLE_INSTRUCTIONS[role],
                model=self.model,
                tools=self.tools() if use_tools else [],
                output_type=output_type,
            )
        return self._agents[role]

    # -- accounting -------------------------------------------------------------
    def _account(self, result: Any, ctx: dict) -> None:
        usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        total = int(getattr(usage, "total_tokens", 0) or (in_tok + out_tok))
        price_in, price_out = _PRICES_PER_M.get(self.model, _DEFAULT_PRICE)
        cost = (in_tok * price_in + out_tok * price_out) / 1_000_000
        self.total_tokens += total
        self.total_cost_usd += cost
        self.calls += 1
        counters = ctx.get("counters")
        if isinstance(counters, dict):
            counters["llm_tokens"] = counters.get("llm_tokens", 0) + total
            counters["llm_cost_micro_usd"] = counters.get("llm_cost_micro_usd", 0) + int(round(cost * 1_000_000))
            counters["llm_calls"] = counters.get("llm_calls", 0) + 1

    @staticmethod
    def _prompt(ctx: dict, extra: str = "") -> str:
        payload = {k: v for k, v in ctx.items() if k not in ("counters", "run_id", "agent")}
        text = json.dumps(jsonable(payload), default=str, indent=1)
        if len(text) > _PROMPT_LIMIT:
            text = text[:_PROMPT_LIMIT] + "\n…(truncated)"
        return f"{extra}\n\nCONTEXT (JSON):\n{text}".strip()

    async def _run(self, role: str, prompt: str, ctx: dict, max_turns: int = 8) -> Any:
        result = await self._sdk.Runner.run(self.agent(role), prompt, max_turns=max_turns)
        self._account(result, ctx)
        return result.final_output

    async def _guard(self, op: str, ctx: dict, attempt: Callable[[], Awaitable[Any]], fallback: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await attempt()
        except Exception as exc:  # noqa: BLE001 - ANY failure degrades gracefully
            self.fallbacks += 1
            log.warning("OpenAIBrain.%s failed, falling back to heuristic: %s", op, exc, exc_info=True)
            counters = ctx.get("counters")
            if isinstance(counters, dict):
                counters["brain_fallbacks"] = counters.get("brain_fallbacks", 0) + 1
            try:
                _emit_event(ctx.get("run_id"), ctx.get("agent") or "system", "brain.fallback", f"LLM {op} failed → heuristic", str(exc)[:300], op=op, error=str(exc)[:500], model=self.model)
            except Exception:  # noqa: BLE001 - never let telemetry break the fallback
                log.debug("brain.fallback event failed", exc_info=True)
            return await fallback()

    # -- Brain protocol ---------------------------------------------------------
    async def propose_hypotheses(self, ctx: dict) -> list[HypothesisSpec]:
        """Ask the investigator agent for novel hypotheses (engine tests them afterwards)."""

        async def attempt() -> list[HypothesisSpec]:
            out = await self._run("investigator", self._prompt(ctx, "Propose novel hypotheses for this bank transaction."), ctx)
            specs: list[HypothesisSpec] = []
            for h in (out.hypotheses if isinstance(out, LLMHypothesisList) else [])[:4]:
                params: dict[str, Any] = {}
                if h.rate is not None:
                    params["rate"] = h.rate
                if h.fixed_amount is not None:
                    params["fixed_amount"] = h.fixed_amount
                if h.direction:
                    params["direction"] = h.direction
                if h.adjustment_kind:
                    params["adjustment_kind"] = h.adjustment_kind
                specs.append(HypothesisSpec(kind=h.kind.strip() or "llm_proposed", description=h.description.strip(), params=params, candidate_ids=[c.strip() for c in h.candidate_ids if c.strip()], rule_id=h.rule_id))
            return specs

        return await self._guard("propose_hypotheses", ctx, attempt, lambda: self._fallback.propose_hypotheses(ctx))

    async def explain_decision(self, ctx: dict) -> str:
        """Narrative from the explainer agent; heuristic template on failure."""

        async def attempt() -> str:
            out = await self._run("explainer", self._prompt(ctx, "Explain this decision."), ctx, max_turns=2)
            text = str(out).strip()
            if not text:
                raise ValueError("empty narrative")
            return text

        return await self._guard("explain_decision", ctx, attempt, lambda: self._fallback.explain_decision(ctx))

    async def parse_correction(self, text: str, ctx: dict) -> RuleSpec | None:
        """Structured teaching parse; the regex parser is the fallback (and the tie-breaker)."""

        async def attempt() -> RuleSpec | None:
            out = await self._run("teacher", self._prompt({**ctx, "text": text}, f"Parse this correction into a rule: {text!r}"), ctx, max_turns=2)
            if not isinstance(out, LLMRule) or not out.parseable:
                return await self._fallback.parse_correction(text, ctx)
            pattern = PatternType(out.pattern_type.strip().lower())
            scope = ScopeType(out.scope_type.strip().lower())
            params: dict[str, Any] = {}
            if out.rate is not None:
                params["rate"] = out.rate / 100.0 if out.rate > 1 else out.rate
            if out.amount is not None:
                params["amount"] = out.amount
            if out.tolerance_pct is not None:
                params["tolerance_pct"] = out.tolerance_pct / 100.0 if out.tolerance_pct > 1 else out.tolerance_pct
            if out.days is not None:
                params["days"] = out.days
            if out.direction:
                params["direction"] = out.direction.strip().lower()
            if not params and pattern not in (PatternType.APPROVAL_POLICY, PatternType.CUSTOM):
                raise ValueError("LLM rule lacks parameters")
            scope_id = out.scope_id if scope != ScopeType.GLOBAL else None
            return RuleSpec(pattern_type=pattern, scope_type=scope, scope_id=scope_id, params=params, description=(out.description or text).strip()[:200])

        return await self._guard("parse_correction", ctx, attempt, lambda: self._fallback.parse_correction(text, ctx))

    async def audit_challenge(self, ctx: dict) -> AuditVerdict:
        """Skeptic verdict from the auditor agent; mechanical verdict on failure."""

        async def attempt() -> AuditVerdict:
            out = await self._run("auditor", self._prompt(ctx, "Re-perform and judge this decision."), ctx)
            if not isinstance(out, AuditVerdict):
                raise ValueError("auditor returned no structured verdict")
            if not out.checks:
                out.checks = [c if isinstance(c, AuditCheck) else AuditCheck.model_validate(c) for c in (ctx.get("checks") or [])]
            return out

        return await self._guard("audit_challenge", ctx, attempt, lambda: self._fallback.audit_challenge(ctx))

    async def narrate_report(self, ctx: dict) -> str:
        """Close-report narrative from the reporter agent; template on failure."""

        async def attempt() -> str:
            out = await self._run("reporter", self._prompt(ctx, "Write the close report narrative."), ctx, max_turns=2)
            text = str(out).strip()
            if not text:
                raise ValueError("empty narrative")
            return text

        return await self._guard("narrate_report", ctx, attempt, lambda: self._fallback.narrate_report(ctx))


# ============================================================================ factory
def get_brain() -> Brain:
    """OpenAIBrain when an API key is configured (and the SDK loads); HeuristicBrain otherwise."""
    if settings.llm_available:
        try:
            return OpenAIBrain()
        except Exception as exc:  # noqa: BLE001
            log.warning("OpenAI brain unavailable (%s); using HeuristicBrain", exc)
    return HeuristicBrain()
