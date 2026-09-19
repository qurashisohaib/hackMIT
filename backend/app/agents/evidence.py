from __future__ import annotations

from datetime import date
from math import isfinite

from app.agents.base import BaseAgent
from app.agents.records import memory_for
from app.config import settings
from app.schemas import AuditCheck, Hypothesis, RuleSpec


def check(name: str, passed: bool, detail: str) -> AuditCheck:
    return AuditCheck(name=name, passed=bool(passed), detail=detail)


def rule_fit(spec: RuleSpec, observed: float, gross: float) -> bool:
    if not isfinite(observed) or not isfinite(gross) or gross <= 0:
        return False
    params = spec.params
    direction = params.get("direction", "deduct" if spec.pattern_type.value == "early_pay_discount" else "either")
    if direction not in {"deduct", "add", "either"}:
        return False
    pattern = spec.pattern_type.value
    if pattern in {"percentage_fee", "early_pay_discount"}:
        rate = float(params.get("rate", 0))
        if not 0 < rate < 1:
            return False
        adjustment = gross * rate
    elif pattern == "fixed_fee":
        adjustment = float(params.get("amount", 0))
        if not isfinite(adjustment) or adjustment <= 0:
            return False
    elif pattern == "fx_tolerance":
        tolerance = float(params.get("tolerance_pct", 0))
        return 0 < tolerance < 1 and 0.01 < abs(observed - gross) <= gross * tolerance + 0.01
    else:
        return False
    amounts = []
    if direction in {"deduct", "either"}:
        amounts.append(round(gross - adjustment, 2))
    if direction in {"add", "either"}:
        amounts.append(round(gross + adjustment, 2))
    return any(abs(observed - amount) <= 0.03 for amount in amounts)


def bank_checks(agent: BaseAgent, entity: dict, hypothesis: Hypothesis, spec: RuleSpec | None = None) -> list[AuditCheck]:
    cp = agent.call_tool("resolve_counterparty", bank_txn=entity)
    ids = [cid for cid in hypothesis.candidate_ids if not cid.startswith("BT-")]
    roots: list[dict] = []
    linked: set[str] = set()
    for cid in ids:
        if cid.startswith("PAY-"):
            row = agent.call_tool("get_payment", id=cid)
            if not row:
                return [check("existence", False, f"Missing payment {cid}")]
            roots.append({**row, "kind": "payment"})
            linked.update(row["ap_invoice_ids"])
    for cid in ids:
        if cid in linked or cid.startswith("PAY-"):
            continue
        tool = "get_ar_invoice" if cid.startswith("AR-") else "get_ap_invoice"
        if cid.startswith("PR-"):
            payrolls = agent.call_tool("list_payroll_runs", period_id=entity["period_id"])
            row = next((p for p in payrolls if p["id"] == cid), {})
            if not row:
                return [check("existence", False, f"Missing payroll {cid}")]
            roots.append({**row, "kind": "payroll"})
        else:
            row = agent.call_tool(tool, id=cid)
            if not row:
                return [check("existence", False, f"Missing invoice {cid}")]
            roots.append({**row, "kind": "ar_invoice" if cid.startswith("AR-") else "ap_invoice"})
    observed = abs(float(entity["amount"]))
    if hypothesis.kind == "bank_fee":
        return [check("bank_fee", not ids and cp["type"] == "bank" and float(entity["amount"]) < 0, "Verify fee descriptor and cash outflow")]
    ownership = True
    available = True
    gross = 0.0
    foreign_currency = False
    for row in roots:
        kind = row["kind"]
        if kind == "payroll":
            component = hypothesis.params.get("component")
            amount = row["net"] if component == "net" else row["employer_taxes"] if component == "employer_taxes" else row["gross"]
            ownership &= cp["type"] == "payroll" and float(entity["amount"]) < 0
            gross += float(amount)
            continue
        if kind == "ar_invoice":
            ownership &= float(entity["amount"]) > 0 and (
                cp["type"] == "customer" and cp["id"] == row["customer_id"]
                or row["customer_id"] in cp.get("channel_customer_ids", [])
            )
        else:
            ownership &= float(entity["amount"]) < 0 and cp["type"] == "vendor" and cp["id"] == row["vendor_id"]
        available &= row["date"] <= entity["date"] and row["period_id"] <= entity["period_id"]
        amount = float(row["amount"])
        if kind == "payment":
            available &= row["bank_txn_id"] in (None, entity["id"], hypothesis.params.get("sibling_txn_id"))
            for inv_id in row["ap_invoice_ids"]:
                invoice = agent.call_tool("get_ap_invoice", id=inv_id)
                foreign_currency |= invoice.get("currency", "USD") != "USD"
        else:
            available &= row["status"] not in {"void", "held"}
            if not entity.get("reconciled"):
                amount -= float(row["paid_amount"])
            if row["currency"] != "USD":
                foreign_currency = True
                amount *= float(row.get("fx_rate") or 0)
        gross += amount
    checks = [
        check("counterparty", bool(roots) and ownership, "Reload candidate ownership and transaction direction"),
        check("availability", available and gross > 0, "Check dates, settlement state and eligible invoice balances"),
    ]
    if spec is None and hypothesis.rule_id and hypothesis.kind.startswith("rule_"):
        node = memory_for(agent.ctx).store.get_node(hypothesis.rule_id)
        if node is None:
            return checks + [check("rule", False, "Referenced rule does not exist")]
        spec = RuleSpec.model_validate(node.props)
        checks.append(check("rule_status", node.props.get("status") == "active", "Rule must be active"))
    if spec:
        scope_ok = spec.scope_type.value == "global" or spec.scope_type.value == cp["type"] and spec.scope_id == cp["id"]
        checks.append(check("rule_scope", scope_ok, "Rule scope agrees with financial counterparty"))
        if spec.pattern_type.value == "fx_tolerance":
            checks.append(check("currency", foreign_currency, "FX policy requires foreign currency evidence"))
        checks.append(check("rule_arithmetic", rule_fit(spec, observed, gross), f"Recomputed gross={gross:.2f}, bank={observed:.2f}"))
    elif hypothesis.kind == "split_payment":
        sibling_id = hypothesis.params.get("sibling_txn_id")
        sibling = agent.call_tool("get_bank_txn", id=sibling_id) if sibling_id else {}
        sibling_cp = agent.call_tool("resolve_counterparty", bank_txn=sibling) if sibling else {}
        valid = bool(sibling) and sibling_cp.get("id") == cp["id"] and float(sibling["amount"]) * float(entity["amount"]) > 0 and sibling["period_id"] == entity["period_id"]
        checks.append(check("split_sum", valid and abs(observed + abs(float(sibling.get("amount", 0))) - gross) <= 0.03, "Recompute both bank payments against invoice"))
    elif hypothesis.kind == "partial_payment":
        checks.append(check("partial_amount", 0 < observed < gross, "Partial receipt leaves an open balance"))
    else:
        checks.append(check("amount", abs(observed - gross) <= 0.03, f"Recomputed gross={gross:.2f}, bank={observed:.2f}"))
    return checks


def ap_checks(agent: BaseAgent, entity: dict, hypothesis: Hypothesis, *, human_approval: bool = False) -> list[AuditCheck]:
    if human_approval and hypothesis.kind == "policy_no_po":
        vendor = agent.call_tool("get_vendor", vendor_id=entity["vendor_id"])
        amount = float(entity["amount"]) * (float(entity.get("fx_rate") or 1) if entity["currency"] != "USD" else 1)
        valid = bool(vendor) and not entity["po_id"] and amount > settings.no_po_threshold_usd and entity["status"] not in {"void", "held"}
        return [check("no_po_policy", valid, "Controller explicitly approves the identified no-PO invoice")]
    if hypothesis.kind == "duplicate":
        others = agent.call_tool("find_duplicates", ap_invoice_id=entity["id"])
        valid = any(other["id"] in hypothesis.candidate_ids and (other["date"], other["id"]) < (entity["date"], entity["id"]) for other in others)
        return [check("duplicate", valid, "Requery vendor, amount, date and original invoice")]
    if hypothesis.kind == "timing_lag":
        payment = agent.call_tool("get_payment", id=hypothesis.params["payment_id"])
        period = agent.call_tool("get_period", period_id=agent.ctx.period_id)
        age = (date.fromisoformat(period["end_date"]) - date.fromisoformat(payment["date"])).days
        amount = float(entity["amount"]) * (float(entity.get("fx_rate") or 1) if entity["currency"] != "USD" else 1)
        valid = entity["id"] in payment["ap_invoice_ids"] and payment["vendor_id"] == entity["vendor_id"] and payment["bank_txn_id"] is None and 0 <= age <= 10 and abs(float(payment["amount"]) - amount) <= 0.03 and not agent.call_tool("payment_has_bank_evidence", payment_id=payment["id"], period_id=agent.ctx.period_id)
        return [check("timing", valid, "Reload outstanding payment at period end")]
    match = agent.call_tool("three_way_match", ap_invoice_id=entity["id"])
    if human_approval and hypothesis.kind == "policy_price_variance":
        valid = match["has_po"] and match["receipts_ok"] and match["po"]["vendor_id"] == entity["vendor_id"] and match["po"]["currency"] == entity["currency"] and match["variance_pct"] > settings.price_tolerance_pct
        return [check("price_variance_policy", valid, "Controller explicitly approves verified PO price variance")]
    if hypothesis.kind == "rule_price_tolerance":
        node = memory_for(agent.ctx).store.get_node(hypothesis.rule_id or "")
        limit = float(node.props["params"].get("tolerance_pct", 0)) if node else 0
        valid = node is not None and node.props.get("status") == "active" and node.props.get("scope_id") in (None, entity["vendor_id"]) and match["has_po"] and match["receipts_ok"] and match["variance_pct"] <= limit
        return [check("price_policy", valid, "Re-perform PO, receiving and learned price tolerance")]
    return [check("three_way_match", match.get("ok", False), "Re-perform PO, receipt and invoice control")]
