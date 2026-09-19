"""Finance tools shared by every agent (and, read-only, by the OpenAI brain).

Every tool is a plain function taking a SQLAlchemy `session` first (unless registered with
`requires_session=False`) and returning plain dicts / lists so results are JSON-able for the
event trace. Tools NEVER read `ground_truth`.

GL codes used for agent postings are module constants; missing accounts are created on the
fly (by name lookup first) so a chart of accounts without them never breaks a posting.
"""
from __future__ import annotations

import itertools
import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.base import ToolRegistry, jsonable
from app.config import settings
from app.db.models import (
    APInvoice,
    ARInvoice,
    BankTransaction,
    Customer,
    Employee,
    GLAccount,
    GoodsReceipt,
    LedgerEntry,
    PayrollRun,
    Payment,
    Period,
    PurchaseOrder,
    Vendor,
)
from app.schemas import new_id

registry = ToolRegistry()

# ---------------------------------------------------------------- GL account constants
GL_CASH = "1000"
GL_AR = "1100"
GL_AP = "2000"
GL_PAYROLL = "6100"
GL_PAYROLL_TAX = "6110"
GL_BANK_FEES = "6300"
GL_FX = "6500"
GL_DISCOUNTS = "6600"
GL_MISC = "6900"

GL_DEFAULTS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # code: (name, type, name keywords used to find an equivalent existing account)
    GL_CASH: ("Cash - Operating", "asset", ("cash", "operating")),
    GL_AR: ("Accounts Receivable", "asset", ("receivable",)),
    GL_AP: ("Accounts Payable", "liability", ("payable",)),
    GL_PAYROLL: ("Payroll Expense", "expense", ("payroll", "salar", "wage")),
    GL_PAYROLL_TAX: ("Payroll Taxes", "expense", ("payroll tax", "employer tax")),
    GL_BANK_FEES: ("Bank & Processing Fees", "expense", ("fee",)),
    GL_FX: ("FX Gain/Loss", "expense", ("fx", "exchange", "currency")),
    GL_DISCOUNTS: ("Discounts Allowed", "expense", ("discount",)),
    GL_MISC: ("Unexplained Cash Differences", "expense", ("unexplained", "suspense", "misc")),
}

OPEN_AP_STATUSES = ("open", "approved", "exception")
OPEN_AR_STATUSES = ("open", "partial", "exception")
IN_TRANSIT_STATUSES = ("sent", "scheduled")

PAYROLL_KEYWORDS = ("PAYROLL", "GUSTO", "RIPPLING", "PAYCHEX", "SALARY", "SALARIES", "EFTPS", "USATAXPYMT", "941", "TAX PMT", "TAXPYMT", "ADP")
BANK_FEE_KEYWORDS = ("SERVICE CHARGE", "SERVICE FEE", "MONTHLY FEE", "WIRE FEE", "BANK FEE", "ANALYSIS FEE", "MAINTENANCE FEE", "ACCOUNT FEE", "FEE")
FEE_TOKEN_RE = re.compile(r"\bFEE\b|\bCHARGE\b")

ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "ar_invoice_ids": re.compile(r"\bAR-[A-Z0-9]+(?:-[A-Z0-9]+)*"),
    "ap_invoice_ids": re.compile(r"\bINV-[A-Z0-9]+(?:-[A-Z0-9]+)*"),
    "payment_ids": re.compile(r"\bPAY-[A-Z0-9]+(?:-[A-Z0-9]+)*"),
    "po_ids": re.compile(r"\bPO-[A-Z0-9]+(?:-[A-Z0-9]+)*"),
    "bank_txn_ids": re.compile(r"\bBT-[A-Z0-9]+(?:-[A-Z0-9]+)*"),
    "payroll_ids": re.compile(r"\bPR-[A-Z0-9]+(?:-[A-Z0-9]+)*"),
}
_ID_MODELS = {
    "ar_invoice_ids": ARInvoice,
    "ap_invoice_ids": APInvoice,
    "payment_ids": Payment,
    "po_ids": PurchaseOrder,
    "bank_txn_ids": BankTransaction,
    "payroll_ids": PayrollRun,
}


# ---------------------------------------------------------------- helpers
def row_to_dict(row: Any) -> dict[str, Any]:
    """SQLAlchemy row → plain dict (dates → ISO strings)."""
    if row is None:
        return {}
    return {c.name: jsonable(getattr(row, c.name)) for c in row.__table__.columns}


def _norm(text: str | None) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()).strip()


def _contains_phrase(haystack_norm: str, needle_norm: str) -> bool:
    return bool(needle_norm) and f" {needle_norm} " in f" {haystack_norm} "


def _period_bounds(session: Session, period_id: str) -> tuple[date, date] | None:
    p = session.get(Period, period_id)
    if p is None:
        return None
    return p.start_date, p.end_date


def _usd(amount: float, currency: str | None, fx_rate: float | None) -> float:
    """Convert a native-currency amount into USD using the stored invoice-date rate."""
    if currency and currency.upper() != "USD" and fx_rate:
        return round(amount * fx_rate, 2)
    return round(amount, 2)


def _ap_candidate(inv: APInvoice) -> dict[str, Any]:
    remaining = round(inv.amount - (inv.paid_amount or 0.0), 2)
    return {
        "kind": "ap_invoice",
        "id": inv.id,
        "amount": inv.amount,
        "remaining": remaining,
        "expected_usd": _usd(remaining, inv.currency, inv.fx_rate),
        "currency": inv.currency,
        "fx_rate": inv.fx_rate,
        "date": inv.date.isoformat(),
        "due_date": inv.due_date.isoformat(),
        "period_id": inv.period_id,
        "counterparty_type": "vendor",
        "counterparty_id": inv.vendor_id,
        "number": inv.vendor_invoice_number,
        "status": inv.status,
        "po_id": inv.po_id,
        "linked_ids": [],
        "side": "out",
    }


def _ar_candidate(inv: ARInvoice) -> dict[str, Any]:
    remaining = round(inv.amount - (inv.paid_amount or 0.0), 2)
    return {
        "kind": "ar_invoice",
        "id": inv.id,
        "amount": inv.amount,
        "remaining": remaining,
        "expected_usd": round(remaining, 2),
        "currency": inv.currency,
        "fx_rate": None,
        "date": inv.date.isoformat(),
        "due_date": inv.due_date.isoformat(),
        "period_id": inv.period_id,
        "counterparty_type": "customer",
        "counterparty_id": inv.customer_id,
        "number": inv.id,
        "status": inv.status,
        "po_id": None,
        "linked_ids": [],
        "side": "in",
    }


def _payment_candidate(p: Payment) -> dict[str, Any]:
    return {
        "kind": "payment",
        "id": p.id,
        "amount": p.amount,
        "remaining": round(p.amount, 2),
        "expected_usd": round(p.amount, 2),
        "currency": "USD",
        "fx_rate": None,
        "date": p.date.isoformat(),
        "due_date": p.date.isoformat(),
        "period_id": p.period_id,
        "counterparty_type": "vendor",
        "counterparty_id": p.vendor_id,
        "number": p.id,
        "status": p.status,
        "po_id": None,
        "linked_ids": list(p.ap_invoice_ids or []),
        "method": p.method,
        "side": "out",
    }


def _payroll_candidates(pr: PayrollRun) -> list[dict[str, Any]]:
    out = []
    for component, amount in (("net", pr.net), ("employer_taxes", pr.employer_taxes), ("gross", pr.gross)):
        out.append(
            {
                "kind": "payroll",
                "id": pr.id,
                "component": component,
                "amount": amount,
                "remaining": round(amount, 2),
                "expected_usd": round(amount, 2),
                "currency": "USD",
                "date": pr.pay_date.isoformat(),
                "period_id": pr.period_id,
                "counterparty_type": "payroll",
                "counterparty_id": None,
                "linked_ids": [],
                "side": "out",
                "status": "cleared" if pr.bank_txn_id else "open",
            }
        )
    return out


def _round_amounts_equal(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(round(a, 2) - round(b, 2)) <= tol + 1e-9


# ---------------------------------------------------------------- lookups
@registry.register
def get_period(session: Session, period_id: str) -> dict[str, Any]:
    """Fetch an accounting period by id.

    Args:
        period_id: period id such as "2026-01".
    """
    return row_to_dict(session.get(Period, period_id))


@registry.register(requires_session=False)
def adjacent_period(period_id: str, delta: int = -1) -> str | None:
    """Return the period id `delta` months away from `period_id` ("YYYY-MM"), or None if unparseable.

    Args:
        period_id: base period id.
        delta: +1 for the next month, -1 for the previous month.
    """
    m = re.fullmatch(r"(\d{4})-(\d{2})", period_id or "")
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    idx = year * 12 + (month - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


@registry.register
def list_periods(session: Session) -> list[dict[str, Any]]:
    """List all accounting periods ordered by id."""
    return [row_to_dict(p) for p in session.scalars(select(Period).order_by(Period.id))]


@registry.register
def get_vendor(session: Session, vendor_id: str) -> dict[str, Any]:
    """Fetch a vendor master record."""
    return row_to_dict(session.get(Vendor, vendor_id))


@registry.register
def get_customer(session: Session, customer_id: str) -> dict[str, Any]:
    """Fetch a customer master record."""
    return row_to_dict(session.get(Customer, customer_id))


@registry.register
def list_vendors(session: Session) -> list[dict[str, Any]]:
    """List all vendors."""
    return [row_to_dict(v) for v in session.scalars(select(Vendor).order_by(Vendor.id))]


@registry.register
def list_customers(session: Session) -> list[dict[str, Any]]:
    """List all customers."""
    return [row_to_dict(c) for c in session.scalars(select(Customer).order_by(Customer.id))]


@registry.register
def get_bank_txn(session: Session, id: str) -> dict[str, Any]:
    """Fetch a bank transaction by id (signed amount: negative = cash out)."""
    return row_to_dict(session.get(BankTransaction, id))


@registry.register
def list_bank_txns(session: Session, period_id: str, status: str | None = None) -> list[dict[str, Any]]:
    """List bank transactions in a period, optionally filtered by status.

    Args:
        period_id: period id.
        status: unreconciled | reconciled | exception (None = all).
    """
    stmt = select(BankTransaction).where(BankTransaction.period_id == period_id)
    if status:
        stmt = stmt.where(BankTransaction.status == status)
    stmt = stmt.order_by(BankTransaction.date, BankTransaction.id)
    return [row_to_dict(b) for b in session.scalars(stmt)]


@registry.register
def list_unreconciled_bank_txns(session: Session, period_id: str) -> list[dict[str, Any]]:
    """List bank transactions in a period that are not yet reconciled (date order)."""
    stmt = (
        select(BankTransaction)
        .where(BankTransaction.period_id == period_id, BankTransaction.reconciled.is_(False))
        .order_by(BankTransaction.date, BankTransaction.id)
    )
    return [row_to_dict(b) for b in session.scalars(stmt)]


@registry.register
def get_ap_invoice(session: Session, id: str) -> dict[str, Any]:
    """Fetch a vendor bill (AP invoice) by id."""
    return row_to_dict(session.get(APInvoice, id))


@registry.register
def get_ar_invoice(session: Session, id: str) -> dict[str, Any]:
    """Fetch a customer invoice (AR invoice) by id."""
    return row_to_dict(session.get(ARInvoice, id))


@registry.register
def list_ap_invoices(session: Session, period_id: str, status: str | None = None) -> list[dict[str, Any]]:
    """List AP invoices of a period, optionally filtered by status."""
    stmt = select(APInvoice).where(APInvoice.period_id == period_id)
    if status:
        stmt = stmt.where(APInvoice.status == status)
    return [row_to_dict(i) for i in session.scalars(stmt.order_by(APInvoice.date, APInvoice.id))]


@registry.register
def list_ar_invoices(session: Session, period_id: str, status: str | None = None) -> list[dict[str, Any]]:
    """List AR invoices of a period, optionally filtered by status."""
    stmt = select(ARInvoice).where(ARInvoice.period_id == period_id)
    if status:
        stmt = stmt.where(ARInvoice.status == status)
    return [row_to_dict(i) for i in session.scalars(stmt.order_by(ARInvoice.date, ARInvoice.id))]


@registry.register
def list_open_ap_invoices(
    session: Session,
    vendor_id: str | None = None,
    period_id: str | None = None,
    include_prior_periods: bool = True,
) -> list[dict[str, Any]]:
    """Open (unpaid, not held/void) AP invoices as match candidates.

    Args:
        vendor_id: restrict to one vendor.
        period_id: restrict to this period (and earlier ones when include_prior_periods).
        include_prior_periods: include invoices from periods before `period_id`.
    """
    stmt = select(APInvoice).where(APInvoice.status.in_(OPEN_AP_STATUSES))
    if vendor_id:
        stmt = stmt.where(APInvoice.vendor_id == vendor_id)
    if period_id:
        stmt = stmt.where(APInvoice.period_id <= period_id if include_prior_periods else APInvoice.period_id == period_id)
    rows = session.scalars(stmt.order_by(APInvoice.date, APInvoice.id)).all()
    return [_ap_candidate(i) for i in rows if round(i.amount - (i.paid_amount or 0.0), 2) > 0]


@registry.register
def list_open_ar_invoices(
    session: Session,
    customer_id: str | None = None,
    period_id: str | None = None,
    include_prior: bool = True,
) -> list[dict[str, Any]]:
    """Open (unpaid or partially paid) AR invoices as match candidates.

    Args:
        customer_id: restrict to one customer.
        period_id: restrict to this period (and earlier ones when include_prior).
        include_prior: include invoices from periods before `period_id`.
    """
    stmt = select(ARInvoice).where(ARInvoice.status.in_(OPEN_AR_STATUSES))
    if customer_id:
        stmt = stmt.where(ARInvoice.customer_id == customer_id)
    if period_id:
        stmt = stmt.where(ARInvoice.period_id <= period_id if include_prior else ARInvoice.period_id == period_id)
    rows = session.scalars(stmt.order_by(ARInvoice.date, ARInvoice.id)).all()
    return [_ar_candidate(i) for i in rows if round(i.amount - (i.paid_amount or 0.0), 2) > 0]


@registry.register
def get_payment(session: Session, id: str) -> dict[str, Any]:
    """Fetch an AP payment instruction by id."""
    return row_to_dict(session.get(Payment, id))


@registry.register
def list_payments(
    session: Session, vendor_id: str | None = None, period_id: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    """List AP payments filtered by vendor / period / status (scheduled|sent|cleared|failed)."""
    stmt = select(Payment)
    if vendor_id:
        stmt = stmt.where(Payment.vendor_id == vendor_id)
    if period_id:
        stmt = stmt.where(Payment.period_id == period_id)
    if status:
        stmt = stmt.where(Payment.status == status)
    return [row_to_dict(p) for p in session.scalars(stmt.order_by(Payment.date, Payment.id))]


@registry.register
def payments_in_transit(session: Session, period_id: str, days: int = 10) -> list[dict[str, Any]]:
    """Payments sent in the last `days` of a period that have no bank transaction yet.

    Args:
        period_id: period whose tail is inspected.
        days: window before period end.
    """
    bounds = _period_bounds(session, period_id)
    if bounds is None:
        return []
    start, end = bounds
    cutoff = max(start, end - timedelta(days=days))
    stmt = (
        select(Payment)
        .where(
            Payment.bank_txn_id.is_(None),
            Payment.status.in_(IN_TRANSIT_STATUSES),
            Payment.date >= cutoff,
            Payment.date <= end,
        )
        .order_by(Payment.date, Payment.id)
    )
    return [_payment_candidate(p) for p in session.scalars(stmt)]


@registry.register
def get_po(session: Session, id: str) -> dict[str, Any]:
    """Fetch a purchase order by id."""
    return row_to_dict(session.get(PurchaseOrder, id))


@registry.register
def get_receipts(session: Session, po_id: str) -> list[dict[str, Any]]:
    """Goods receipts recorded against a purchase order."""
    stmt = select(GoodsReceipt).where(GoodsReceipt.po_id == po_id).order_by(GoodsReceipt.date, GoodsReceipt.id)
    return [row_to_dict(r) for r in session.scalars(stmt)]


@registry.register
def list_payroll_runs(session: Session, period_id: str) -> list[dict[str, Any]]:
    """Payroll runs of a period (gross / net / employer taxes)."""
    stmt = select(PayrollRun).where(PayrollRun.period_id == period_id).order_by(PayrollRun.pay_date, PayrollRun.id)
    return [row_to_dict(p) for p in session.scalars(stmt)]


# ---------------------------------------------------------------- reference & counterparty resolution
@registry.register
def find_by_reference(session: Session, text: str) -> dict[str, list[str]]:
    """Extract ids mentioned in a memo (AR-…, INV-…, PAY-…, PO-…, BT-…, PR-…, vendor invoice numbers).

    Only ids that exist in the ledger are returned.

    Args:
        text: raw bank memo / reference text.
    """
    upper = (text or "").upper()
    out: dict[str, list[str]] = {k: [] for k in ID_PATTERNS}
    for key, pattern in ID_PATTERNS.items():
        model = _ID_MODELS[key]
        for raw in pattern.findall(upper):
            candidate = raw.rstrip("-")
            if candidate not in out[key] and session.get(model, candidate) is not None:
                out[key].append(candidate)
    tokens = {t for t in re.findall(r"[A-Z0-9][A-Z0-9\-/]{3,}", upper) if any(ch.isdigit() for ch in t)}
    out["vendor_invoice_numbers"] = []
    if tokens:
        stmt = select(APInvoice).where(func.upper(APInvoice.vendor_invoice_number).in_(sorted(tokens)))
        for inv in session.scalars(stmt):
            out["vendor_invoice_numbers"].append(inv.vendor_invoice_number)
            if inv.id not in out["ap_invoice_ids"]:
                out["ap_invoice_ids"].append(inv.id)
    out["all"] = [i for key in ID_PATTERNS for i in out[key]]
    return out


def _channel_customers(session: Session, vendor: Vendor) -> list[Customer]:
    """Customers whose receipts arrive through a payment-processor vendor (e.g. Stripe)."""
    if not vendor.is_payment_processor:
        return []
    customers = session.scalars(select(Customer).order_by(Customer.id)).all()
    vendor_tokens = {t for t in _norm(vendor.name).split() if len(t) >= 3} | {t for t in _norm(vendor.id).split() if len(t) >= 3}
    matched = [c for c in customers if c.channel and c.channel.upper() in vendor_tokens]
    if matched:
        return matched
    return [c for c in customers if (c.channel or "direct").lower() != "direct"]


def _resolve_counterparty_rows(session: Session, bt: dict[str, Any]) -> dict[str, Any]:
    text = _norm(f"{bt.get('description', '')} {bt.get('counterparty_hint', '')} {bt.get('reference', '')}")
    txn_type = (bt.get("type") or "").lower()
    result: dict[str, Any] = {
        "type": "unknown",
        "id": None,
        "name": None,
        "confidence": 0.0,
        "method": "none",
        "is_payment_processor": False,
        "channel_customer_ids": [],
        "channel_customer_names": [],
        "references": {},
    }

    best: tuple[float, int, str, Any] | None = None  # (confidence, match_len, type, row)

    def consider(conf: float, length: int, cp_type: str, row: Any, method: str) -> None:
        nonlocal best
        key = (conf, length)
        if best is None or key > (best[0], best[1]):
            best = (conf, length, cp_type, row)
            result["method"] = method

    for vendor in session.scalars(select(Vendor).order_by(Vendor.id)):
        desc = _norm(vendor.bank_descriptor)
        name = _norm(vendor.name)
        if _contains_phrase(text, desc):
            consider(0.95, len(desc), "vendor", vendor, "bank_descriptor")
        elif _contains_phrase(text, name):
            consider(0.85, len(name), "vendor", vendor, "name")
        else:
            first = name.split()[0] if name else ""
            if len(first) >= 4 and _contains_phrase(text, first):
                consider(0.70, len(first), "vendor", vendor, "name_token")
    for customer in session.scalars(select(Customer).order_by(Customer.id)):
        desc = _norm(customer.bank_descriptor)
        name = _norm(customer.name)
        if _contains_phrase(text, desc):
            consider(0.95, len(desc), "customer", customer, "bank_descriptor")
        elif _contains_phrase(text, name):
            consider(0.85, len(name), "customer", customer, "name")
        else:
            first = name.split()[0] if name else ""
            if len(first) >= 4 and _contains_phrase(text, first):
                consider(0.70, len(first), "customer", customer, "name_token")

    refs = find_by_reference(session, f"{bt.get('description', '')} {bt.get('reference', '')}")
    result["references"] = refs
    if best is None or best[0] < 0.9:
        # references are authoritative when the descriptor is ambiguous
        if refs["ar_invoice_ids"]:
            inv = session.get(ARInvoice, refs["ar_invoice_ids"][0])
            if inv is not None:
                consider(0.90, 99, "customer", session.get(Customer, inv.customer_id), "reference")
        elif refs["payment_ids"]:
            pay = session.get(Payment, refs["payment_ids"][0])
            if pay is not None:
                consider(0.90, 99, "vendor", session.get(Vendor, pay.vendor_id), "reference")
        elif refs["ap_invoice_ids"]:
            inv = session.get(APInvoice, refs["ap_invoice_ids"][0])
            if inv is not None:
                consider(0.90, 99, "vendor", session.get(Vendor, inv.vendor_id), "reference")

    if best is not None and best[3] is not None:
        conf, _, cp_type, row = best
        result.update({"type": cp_type, "id": row.id, "name": row.name, "confidence": conf})
        if cp_type == "vendor":
            result["is_payment_processor"] = bool(row.is_payment_processor)
            channel = _channel_customers(session, row)
            result["channel_customer_ids"] = [c.id for c in channel]
            result["channel_customer_names"] = [c.name for c in channel]
        return result

    if txn_type == "payroll" or any(k in text for k in PAYROLL_KEYWORDS):
        result.update({"type": "payroll", "id": None, "name": "Payroll", "confidence": 0.90, "method": "keyword"})
        return result
    if txn_type == "fee" or any(_contains_phrase(text, _norm(k)) for k in BANK_FEE_KEYWORDS):
        result.update({"type": "bank", "id": None, "name": "Bank", "confidence": 0.80, "method": "keyword"})
        return result
    hint = (bt.get("counterparty_hint") or "").strip()
    if hint:
        result["name"] = hint
    return result


@registry.register
def resolve_counterparty(session: Session, bank_txn: dict[str, Any] | str) -> dict[str, Any]:
    """Identify who a bank transaction is with from its memo/descriptor tokens.

    Returns {type: vendor|customer|bank|payroll|unknown, id, name, confidence, method,
    is_payment_processor, channel_customer_ids, references}. Stripe payouts resolve to the
    processor vendor (e.g. V-STRIPE) with the Stripe-channel customers listed alongside.

    Args:
        bank_txn: bank transaction dict (from get_bank_txn) or its id.
    """
    if isinstance(bank_txn, str):
        bank_txn = get_bank_txn(session, bank_txn)
    if not bank_txn:
        return {"type": "unknown", "id": None, "name": None, "confidence": 0.0, "method": "none", "references": {}}
    return _resolve_counterparty_rows(session, bank_txn)


@registry.register
def counterparty_candidates(
    session: Session, counterparty: dict[str, Any], side: str, period_id: str | None = None
) -> list[dict[str, Any]]:
    """Open items that a bank transaction with this counterparty could settle.

    Args:
        counterparty: result of resolve_counterparty.
        side: "in" (deposit) or "out" (disbursement).
        period_id: current period; earlier periods are included.
    """
    cp_type = counterparty.get("type")
    cp_id = counterparty.get("id")
    out: list[dict[str, Any]] = []
    if cp_type == "customer" and cp_id and side == "in":
        out.extend(list_open_ar_invoices(session, customer_id=cp_id, period_id=period_id))
    elif cp_type == "vendor" and cp_id:
        if side == "out":
            payments = [
                _payment_candidate(p)
                for p in session.scalars(
                    select(Payment)
                    .where(Payment.vendor_id == cp_id, Payment.bank_txn_id.is_(None), Payment.status.in_(IN_TRANSIT_STATUSES))
                    .order_by(Payment.date, Payment.id)
                )
            ]
            covered = {inv_id for p in payments for inv_id in p["linked_ids"]}
            out.extend(payments)
            out.extend(c for c in list_open_ap_invoices(session, vendor_id=cp_id, period_id=period_id) if c["id"] not in covered)
        else:
            for cust_id in counterparty.get("channel_customer_ids") or []:
                out.extend(list_open_ar_invoices(session, customer_id=cust_id, period_id=period_id))
            if not counterparty.get("is_payment_processor"):
                # vendor refund / credit: the open bill itself is the only plausible target
                out.extend(list_open_ap_invoices(session, vendor_id=cp_id, period_id=period_id))
    elif cp_type == "payroll":
        for pid in {period_id, adjacent_period(period_id or "", -1)}:
            if pid:
                for pr in session.scalars(select(PayrollRun).where(PayrollRun.period_id == pid, PayrollRun.bank_txn_id.is_(None))):
                    out.extend(_payroll_candidates(pr))
    return out


@registry.register
def list_sibling_txns(session: Session, bank_txn_id: str) -> list[dict[str, Any]]:
    """Other unreconciled bank transactions in the same period with the same counterparty."""
    bt = session.get(BankTransaction, bank_txn_id)
    if bt is None:
        return []
    me = _resolve_counterparty_rows(session, row_to_dict(bt))
    if me["type"] in ("unknown", "bank"):
        return []
    out = []
    stmt = (
        select(BankTransaction)
        .where(
            BankTransaction.period_id == bt.period_id,
            BankTransaction.reconciled.is_(False),
            BankTransaction.id != bank_txn_id,
        )
        .order_by(BankTransaction.date, BankTransaction.id)
    )
    for other in session.scalars(stmt):
        cp = _resolve_counterparty_rows(session, row_to_dict(other))
        if cp["type"] == me["type"] and cp["id"] == me["id"]:
            out.append(row_to_dict(other))
    return out


# ---------------------------------------------------------------- amount arithmetic
@registry.register
def find_amount_candidates(
    session: Session,
    amount: float,
    side: str,
    tolerance_abs: float = 0.01,
    period_id: str | None = None,
    counterparty: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Open invoices / payments whose USD value is within `tolerance_abs` of `amount`.

    Args:
        amount: observed absolute amount.
        side: "in" → AR invoices; "out" → AP invoices + in-transit payments.
        tolerance_abs: absolute tolerance in USD.
        period_id: current period (earlier periods are included).
        counterparty: optional resolve_counterparty result to restrict the search.
    """
    target = abs(float(amount))
    if counterparty and counterparty.get("id"):
        pool = counterparty_candidates(session, counterparty, side, period_id)
    elif side == "in":
        pool = list_open_ar_invoices(session, period_id=period_id)
    else:
        pool = [
            _payment_candidate(p)
            for p in session.scalars(
                select(Payment).where(Payment.bank_txn_id.is_(None), Payment.status.in_(IN_TRANSIT_STATUSES))
            )
        ]
        covered = {i for p in pool for i in p["linked_ids"]}
        pool.extend(c for c in list_open_ap_invoices(session, period_id=period_id) if c["id"] not in covered)
    return [c for c in pool if abs(c["expected_usd"] - target) <= tolerance_abs + 1e-9]


@registry.register(requires_session=False)
def subset_sum(
    candidates: list[dict[str, Any]], target: float, max_items: int = 4, tol: float = 0.01
) -> list[list[str]]:
    """Find combinations of candidate ids (2..max_items) whose `expected_usd` sums to `target`.

    Args:
        candidates: candidate dicts with `id` and `expected_usd` (or `amount`).
        target: absolute amount to reach.
        max_items: maximum number of items per combination.
        tol: absolute tolerance.
    """
    target = abs(float(target))
    items = [
        (c["id"], float(c.get("expected_usd", c.get("amount", 0.0))))
        for c in candidates
        if float(c.get("expected_usd", c.get("amount", 0.0))) <= target + tol
    ]
    items = sorted(items, key=lambda t: (-t[1], t[0]))[:28]  # bounded combinatorics
    solutions: list[list[str]] = []
    for k in range(2, min(max_items, len(items)) + 1):
        for combo in itertools.combinations(items, k):
            if abs(sum(v for _, v in combo) - target) <= tol + 1e-9:
                solutions.append([i for i, _ in combo])
    return solutions


@registry.register(requires_session=False)
def implied_rate(observed: float, expected: float) -> dict[str, Any]:
    """Describe the gap between an observed and an expected amount.

    Returns rate (signed fraction of expected), direction (add|deduct|none), is_round (rate is a
    multiple of 0.5% within 0.02pp), fixed_diff and fixed_is_round (multiple of $5).

    Args:
        observed: observed absolute amount.
        expected: expected absolute amount.
    """
    obs, exp = abs(float(observed)), abs(float(expected))
    if exp <= 0:
        return {"rate": 0.0, "direction": "none", "is_round": False, "fixed_diff": obs, "fixed_is_round": False, "pct": 0.0}
    diff = round(obs - exp, 2)
    rate = diff / exp
    pct = rate * 100.0
    nearest_half = round(pct * 2) / 2.0
    is_round = abs(diff) >= 0.5 and abs(pct - nearest_half) <= 0.02 and nearest_half != 0
    fixed_is_round = abs(diff) >= 1.0 and abs(abs(diff) - round(abs(diff) / 5.0) * 5.0) <= 0.011
    direction = "none" if abs(diff) < 0.005 else ("add" if diff > 0 else "deduct")
    return {
        "rate": round(rate, 6),
        "rate_abs": round(abs(rate), 6),
        "pct": round(pct, 3),
        "round_pct": nearest_half if is_round else None,
        "direction": direction,
        "is_round": bool(is_round),
        "fixed_diff": diff,
        "fixed_is_round": bool(fixed_is_round),
    }


# ---------------------------------------------------------------- AP controls
@registry.register
def three_way_match(session: Session, ap_invoice_id: str) -> dict[str, Any]:
    """PO ↔ goods receipt ↔ invoice comparison for an AP invoice.

    Returns {po, receipts, invoice, has_po, variance_pct, received_total, receipts_ok, ok}.
    """
    inv = session.get(APInvoice, ap_invoice_id)
    if inv is None:
        return {"invoice": {}, "po": {}, "receipts": [], "has_po": False, "variance_pct": None, "ok": False}
    po = session.get(PurchaseOrder, inv.po_id) if inv.po_id else None
    receipts = get_receipts(session, inv.po_id) if inv.po_id else []
    received_total = round(sum(r["amount"] for r in receipts), 2)
    variance_pct = None
    if po is not None and po.amount:
        variance_pct = round((inv.amount - po.amount) / po.amount, 4)
    tolerance = settings.price_tolerance_pct
    receipts_ok: bool | None = None
    if receipts:
        receipts_ok = received_total >= min(inv.amount, po.amount if po else inv.amount) * (1 - tolerance) - 0.01
    ok = po is not None and variance_pct is not None and variance_pct <= tolerance + 1e-9 and receipts_ok is not False
    return {
        "invoice": row_to_dict(inv),
        "po": row_to_dict(po),
        "receipts": receipts,
        "has_po": po is not None,
        "variance_pct": variance_pct,
        "variance_amount": round(inv.amount - po.amount, 2) if po else None,
        "received_total": received_total,
        "receipts_ok": receipts_ok,
        "tolerance_pct": tolerance,
        "ok": bool(ok),
    }


@registry.register
def find_duplicates(session: Session, ap_invoice_id: str, window_days: int = 10) -> list[dict[str, Any]]:
    """AP invoices from the same vendor with the same amount within `window_days`, different number."""
    inv = session.get(APInvoice, ap_invoice_id)
    if inv is None:
        return []
    lo, hi = inv.date - timedelta(days=window_days), inv.date + timedelta(days=window_days)
    stmt = (
        select(APInvoice)
        .where(
            APInvoice.vendor_id == inv.vendor_id,
            APInvoice.id != inv.id,
            APInvoice.status != "void",
            APInvoice.date >= lo,
            APInvoice.date <= hi,
        )
        .order_by(APInvoice.date, APInvoice.id)
    )
    return [
        row_to_dict(other)
        for other in session.scalars(stmt)
        if _round_amounts_equal(other.amount, inv.amount) and other.vendor_invoice_number != inv.vendor_invoice_number
    ]


# ---------------------------------------------------------------- summaries
@registry.register
def ar_aging(session: Session, period_id: str) -> dict[str, Any]:
    """AR aging buckets (current, 1-30, 31-60, 61-90, 90+) as of the period end."""
    bounds = _period_bounds(session, period_id)
    if bounds is None:
        return {}
    _, end = bounds
    buckets = {"current": 0.0, "1-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    by_customer: dict[str, float] = {}
    stmt = select(ARInvoice).where(ARInvoice.status.in_(OPEN_AR_STATUSES), ARInvoice.date <= end)
    total = 0.0
    count = 0
    for inv in session.scalars(stmt):
        remaining = round(inv.amount - (inv.paid_amount or 0.0), 2)
        if remaining <= 0:
            continue
        overdue = (end - inv.due_date).days
        key = "current" if overdue <= 0 else "1-30" if overdue <= 30 else "31-60" if overdue <= 60 else "61-90" if overdue <= 90 else "90+"
        buckets[key] = round(buckets[key] + remaining, 2)
        by_customer[inv.customer_id] = round(by_customer.get(inv.customer_id, 0.0) + remaining, 2)
        total = round(total + remaining, 2)
        count += 1
    return {"period_id": period_id, "as_of": end.isoformat(), "total": total, "count": count, "buckets": buckets, "by_customer": by_customer}


@registry.register
def ap_open_summary(session: Session, period_id: str) -> dict[str, Any]:
    """Open AP as of the period end: totals, by vendor, due within the period, held count."""
    bounds = _period_bounds(session, period_id)
    if bounds is None:
        return {}
    _, end = bounds
    stmt = select(APInvoice).where(APInvoice.period_id <= period_id)
    total = 0.0
    due_by_end = 0.0
    by_vendor: dict[str, float] = {}
    held = 0
    count = 0
    for inv in session.scalars(stmt):
        if inv.status == "held":
            held += 1
        if inv.status not in OPEN_AP_STATUSES:
            continue
        remaining = _usd(round(inv.amount - (inv.paid_amount or 0.0), 2), inv.currency, inv.fx_rate)
        if remaining <= 0:
            continue
        count += 1
        total = round(total + remaining, 2)
        by_vendor[inv.vendor_id] = round(by_vendor.get(inv.vendor_id, 0.0) + remaining, 2)
        if inv.due_date <= end:
            due_by_end = round(due_by_end + remaining, 2)
    return {"period_id": period_id, "total": total, "count": count, "due_by_period_end": due_by_end, "by_vendor": by_vendor, "held": held}


@registry.register
def period_cash_summary(session: Session, period_id: str) -> dict[str, Any]:
    """Opening cash, inflows, outflows, ending cash and reconciliation counts for a period."""
    period = session.get(Period, period_id)
    if period is None:
        return {}
    inflow = outflow = 0.0
    reconciled = unreconciled = exceptions = 0
    for bt in session.scalars(select(BankTransaction).where(BankTransaction.period_id == period_id)):
        if bt.amount >= 0:
            inflow = round(inflow + bt.amount, 2)
        else:
            outflow = round(outflow - bt.amount, 2)
        if bt.reconciled:
            reconciled += 1
        elif bt.status == "exception":
            exceptions += 1
        else:
            unreconciled += 1
    return {
        "period_id": period_id,
        "opening_cash": period.opening_cash,
        "inflows": inflow,
        "outflows": outflow,
        "ending_cash": round(period.opening_cash + inflow - outflow, 2),
        "reconciled": reconciled,
        "unreconciled": unreconciled,
        "exceptions": exceptions,
        "total": reconciled + unreconciled + exceptions,
    }


# ---------------------------------------------------------------- mutations
def _ensure_gl_account(session: Session, code: str) -> str:
    """Return a usable GL code: `code` if present, an equivalent by name, else create it."""
    if session.get(GLAccount, code) is not None:
        return code
    name, acct_type, keywords = GL_DEFAULTS.get(code, (f"Account {code}", "expense", ()))
    for acct in session.scalars(select(GLAccount).where(GLAccount.type == acct_type).order_by(GLAccount.code)):
        lowered = acct.name.lower()
        if any(k in lowered for k in keywords):
            return acct.code
    session.add(GLAccount(code=code, name=name, type=acct_type))
    session.flush()
    return code


def _post_ledger(session: Session, period_id: str, on: date, lines: list[dict[str, Any]], source_type: str, source_id: str, created_by: str) -> list[dict[str, Any]]:
    posted = []
    for line in lines:
        debit = round(float(line.get("debit", 0.0) or 0.0), 2)
        credit = round(float(line.get("credit", 0.0) or 0.0), 2)
        if debit == 0.0 and credit == 0.0:
            continue
        entry = LedgerEntry(
            id=new_id(f"JE-{period_id}"),
            period_id=period_id,
            date=on,
            account_code=_ensure_gl_account(session, str(line["account_code"])),
            debit=debit,
            credit=credit,
            memo=str(line.get("memo", ""))[:500],
            source_type=source_type,
            source_id=source_id,
            created_by=created_by,
        )
        session.add(entry)
        posted.append(row_to_dict(entry))
    session.flush()
    return posted


def _default_ledger(bt: BankTransaction, matched_rows: list[tuple[str, Any]], note: str) -> list[dict[str, Any]]:
    """Plain cash-vs-AR/AP/payroll entries when the caller supplied no adjustment lines."""
    amount = round(abs(bt.amount), 2)
    lines: list[dict[str, Any]] = []
    kinds = {kind for kind, _ in matched_rows}
    if bt.amount >= 0:
        lines.append({"account_code": GL_CASH, "debit": amount, "memo": note})
        counter = GL_AR if "ar_invoice" in kinds else GL_AP if kinds & {"ap_invoice", "payment"} else GL_MISC
        lines.append({"account_code": counter, "credit": amount, "memo": note})
    else:
        if "payroll" in kinds:
            lines.append({"account_code": GL_PAYROLL, "debit": amount, "memo": note})
        elif kinds & {"ap_invoice", "payment"}:
            lines.append({"account_code": GL_AP, "debit": amount, "memo": note})
        elif "ar_invoice" in kinds:
            lines.append({"account_code": GL_AR, "debit": amount, "memo": note})
        else:
            lines.append({"account_code": GL_BANK_FEES, "debit": amount, "memo": note})
        lines.append({"account_code": GL_CASH, "credit": amount, "memo": note})
    return lines


def _load_matched(session: Session, matched_ids: list[str]) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for mid in matched_ids:
        for kind, model in (("ar_invoice", ARInvoice), ("ap_invoice", APInvoice), ("payment", Payment), ("payroll", PayrollRun), ("bank_txn", BankTransaction)):
            row = session.get(model, mid)
            if row is not None:
                rows.append((kind, row))
                break
    return rows


@registry.register(mutating=True)
def mark_reconciled(
    session: Session,
    bank_txn_id: str,
    matched_ids: list[str],
    note: str,
    agent: str,
    ledger: list[dict[str, Any]] | None = None,
    allocations: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Reconcile a bank transaction against invoices / payments / payroll and post ledger entries.

    Updates the bank txn (reconciled, status, reconciled_with, note), the matched invoices'
    paid_amount / status / matched_bank_txn_ids, payments (cleared + bank_txn_id) and payroll
    runs (bank_txn_id). `ledger` lines are {account_code, debit, credit, memo}; when omitted a
    plain cash-vs-AR/AP entry is posted. Adjustment lines (fee / discount / FX) increase the
    amount allocated to the invoice beyond the cash moved.

    Args:
        bank_txn_id: the bank transaction to reconcile.
        matched_ids: invoice / payment / payroll ids it settles (may be empty for bank fees).
        note: reconciliation note stored on the transaction.
        agent: agent name (ledger rows are created_by "agent:<name>").
        ledger: explicit ledger lines; None → default cash vs AR/AP lines.
        allocations: optional explicit {invoice_id: amount} allocation override.
    """
    bt = session.get(BankTransaction, bank_txn_id)
    if bt is None:
        raise ValueError(f"unknown bank transaction {bank_txn_id}")
    matched_rows = _load_matched(session, matched_ids)
    cash = round(abs(bt.amount), 2)
    lines = ledger if ledger else _default_ledger(bt, matched_rows, note)
    adjustment = 0.0
    for line in lines:
        code = str(line.get("account_code"))
        if code not in (GL_CASH, GL_AR, GL_AP):
            adjustment += float(line.get("debit", 0.0) or 0.0) - float(line.get("credit", 0.0) or 0.0)
    # value settled on the invoice side: a fee on a deposit means the invoice is worth more than
    # the cash received; a fee on a disbursement means the invoice is worth less than the cash paid.
    gross = round(cash + adjustment, 2) if bt.amount >= 0 else round(cash - adjustment, 2)

    # invoices: allocate gross in date order (explicit allocations win)
    invoices = [(k, r) for k, r in matched_rows if k in ("ar_invoice", "ap_invoice")]
    payments = [r for k, r in matched_rows if k == "payment"]
    for pay in payments:
        for inv_id in pay.ap_invoice_ids or []:
            inv = session.get(APInvoice, inv_id)
            if inv is not None and all(r.id != inv.id for _, r in invoices):
                invoices.append(("ap_invoice", inv))
    invoices.sort(key=lambda kr: (kr[1].date, kr[1].id))
    remaining_cash = gross
    applied: dict[str, float] = {}
    for kind, inv in invoices:
        open_amount = round(inv.amount - (inv.paid_amount or 0.0), 2)
        if kind == "ap_invoice":
            open_amount = _usd(open_amount, inv.currency, inv.fx_rate)
        alloc = float(allocations[inv.id]) if allocations and inv.id in allocations else min(open_amount, remaining_cash)
        alloc = round(max(alloc, 0.0), 2)
        if kind == "ap_invoice" and inv.currency and inv.currency.upper() != "USD" and inv.fx_rate:
            native = round(alloc / inv.fx_rate, 2)
        else:
            native = alloc
        inv.paid_amount = round((inv.paid_amount or 0.0) + native, 2)
        ids = list(inv.matched_bank_txn_ids or [])
        if bt.id not in ids:
            ids.append(bt.id)
        inv.matched_bank_txn_ids = ids
        fully = inv.paid_amount >= inv.amount - 0.005
        inv.status = "paid" if fully else ("partial" if kind == "ar_invoice" else inv.status)
        applied[inv.id] = alloc
        remaining_cash = round(remaining_cash - alloc, 2)
    for pay in payments:
        pay.status = "cleared"
        pay.bank_txn_id = bt.id
    for kind, row in matched_rows:
        if kind == "payroll":
            row.bank_txn_id = bt.id

    posted = _post_ledger(session, bt.period_id, bt.date, lines, "bank", bt.id, f"agent:{agent}")
    bt.reconciled = True
    bt.status = "reconciled"
    bt.reconciled_with = list(dict.fromkeys(list(matched_ids) + [r.id for _, r in invoices]))
    bt.reconciliation_note = note[:1000]
    session.flush()
    return {
        "bank_txn": row_to_dict(bt),
        "matched_ids": bt.reconciled_with,
        "applied": applied,
        "gross": gross,
        "cash": cash,
        "adjustment": round(adjustment, 2),
        "ledger": posted,
    }


@registry.register(mutating=True)
def mark_exception(session: Session, bank_txn_id: str, note: str = "") -> dict[str, Any]:
    """Flag a bank transaction as an open exception (status 'exception', not reconciled)."""
    bt = session.get(BankTransaction, bank_txn_id)
    if bt is None:
        raise ValueError(f"unknown bank transaction {bank_txn_id}")
    if not bt.reconciled:
        bt.status = "exception"
        if note:
            bt.reconciliation_note = note[:1000]
    session.flush()
    return row_to_dict(bt)


@registry.register(mutating=True)
def mark_in_transit(session: Session, payment_id: str, note: str = "") -> dict[str, Any]:
    """Confirm a payment is in transit at period end (status 'sent', no bank txn yet)."""
    pay = session.get(Payment, payment_id)
    if pay is None:
        raise ValueError(f"unknown payment {payment_id}")
    if pay.bank_txn_id is None and pay.status != "cleared":
        pay.status = "sent"
    for inv_id in pay.ap_invoice_ids or []:
        inv = session.get(APInvoice, inv_id)
        if inv is not None and inv.status == "open":
            inv.status = "approved"
    session.flush()
    return {"payment": row_to_dict(pay), "note": note}


@registry.register(mutating=True)
def hold_invoice(session: Session, ap_invoice_id: str, reason: str) -> dict[str, Any]:
    """Put an AP invoice on hold (status 'held') with a reason appended to its description."""
    inv = session.get(APInvoice, ap_invoice_id)
    if inv is None:
        raise ValueError(f"unknown AP invoice {ap_invoice_id}")
    if inv.status not in ("paid", "void"):
        inv.status = "held"
    inv.description = f"{inv.description} [HOLD: {reason}]".strip()[:2000]
    session.flush()
    return row_to_dict(inv)


@registry.register(mutating=True)
def void_invoice(session: Session, ap_invoice_id: str, reason: str) -> dict[str, Any]:
    """Void an AP invoice (status 'void')."""
    inv = session.get(APInvoice, ap_invoice_id)
    if inv is None:
        raise ValueError(f"unknown AP invoice {ap_invoice_id}")
    if inv.status != "paid":
        inv.status = "void"
    inv.description = f"{inv.description} [VOID: {reason}]".strip()[:2000]
    session.flush()
    return row_to_dict(inv)


@registry.register(mutating=True)
def approve_invoice(session: Session, ap_invoice_id: str, by: str | None = None) -> dict[str, Any]:
    """Approve an open AP invoice for payment (status 'approved')."""
    inv = session.get(APInvoice, ap_invoice_id)
    if inv is None:
        raise ValueError(f"unknown AP invoice {ap_invoice_id}")
    if inv.status in ("open", "exception"):
        inv.status = "approved"
    if by and inv.approved_by is None and session.get(Employee, by) is not None:
        inv.approved_by = by
    session.flush()
    return row_to_dict(inv)
