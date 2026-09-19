"""Financial truth tables + memory graph tables (SQLAlchemy 2.0).

IDs are human-readable strings. Bank amounts are signed (negative = cash out).
`ground_truth` is hidden from agents; only the metrics layer reads it.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Period(Base):
    __tablename__ = "periods"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # "2026-01"
    name: Mapped[str] = mapped_column(String)  # "January 2026"
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, default="open")  # open|in_progress|pending_review|closed
    opening_cash: Mapped[float] = mapped_column(Float, default=0.0)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Vendor(Base):
    __tablename__ = "vendors"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # V001
    name: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)  # cloud, components, logistics, rent, ...
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    currency: Mapped[str] = mapped_column(String, default="USD")
    default_gl_account: Mapped[str] = mapped_column(String, default="6000")
    country: Mapped[str] = mapped_column(String, default="US")
    is_payment_processor: Mapped[bool] = mapped_column(Boolean, default=False)
    bank_descriptor: Mapped[str] = mapped_column(String, default="")  # how the bank memo names them


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # C001
    name: Mapped[str] = mapped_column(String)
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    currency: Mapped[str] = mapped_column(String, default="USD")
    channel: Mapped[str] = mapped_column(String, default="direct")  # direct|stripe
    bank_descriptor: Mapped[str] = mapped_column(String, default="")


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # E001
    name: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    approval_limit: Mapped[float] = mapped_column(Float, default=0.0)


class GLAccount(Base):
    __tablename__ = "gl_accounts"
    code: Mapped[str] = mapped_column(String, primary_key=True)  # "1000"
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)  # asset|liability|equity|revenue|expense


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # PO-2026-01-0007
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"))
    period_id: Mapped[str] = mapped_column(ForeignKey("periods.id"))
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String, default="USD")
    status: Mapped[str] = mapped_column(String, default="open")  # open|received|invoiced|closed
    description: Mapped[str] = mapped_column(Text, default="")
    requested_by: Mapped[str | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("employees.id"), nullable=True)


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # GR-2026-01-0007
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"))
    period_id: Mapped[str] = mapped_column(ForeignKey("periods.id"))
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)  # value received
    received_by: Mapped[str | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class APInvoice(Base):
    """Vendor bill."""

    __tablename__ = "ap_invoices"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # INV-2026-01-0031
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"))
    po_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)
    period_id: Mapped[str] = mapped_column(ForeignKey("periods.id"))
    vendor_invoice_number: Mapped[str] = mapped_column(String)  # vendor's own numbering
    date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String, default="USD")
    fx_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # to USD at invoice date
    status: Mapped[str] = mapped_column(String, default="open")  # open|approved|paid|held|void|exception
    gl_account: Mapped[str] = mapped_column(String, default="6000")
    description: Mapped[str] = mapped_column(Text, default="")
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    paid_amount: Mapped[float] = mapped_column(Float, default=0.0)
    matched_bank_txn_ids: Mapped[list] = mapped_column(JSON, default=list)


class ARInvoice(Base):
    """Customer invoice."""

    __tablename__ = "ar_invoices"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # AR-2026-01-0012
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    period_id: Mapped[str] = mapped_column(ForeignKey("periods.id"))
    date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String, default="USD")
    status: Mapped[str] = mapped_column(String, default="open")  # open|partial|paid|exception
    description: Mapped[str] = mapped_column(Text, default="")
    paid_amount: Mapped[float] = mapped_column(Float, default=0.0)
    matched_bank_txn_ids: Mapped[list] = mapped_column(JSON, default=list)


class Payment(Base):
    """AP disbursement instruction (what we *intended* to pay)."""

    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # PAY-2026-01-0020
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"))
    period_id: Mapped[str] = mapped_column(ForeignKey("periods.id"))
    ap_invoice_ids: Mapped[list] = mapped_column(JSON, default=list)
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String, default="ach")  # ach|wire|card|check
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="sent")  # scheduled|sent|cleared|failed
    bank_txn_id: Mapped[str | None] = mapped_column(String, nullable=True)


class BankTransaction(Base):
    __tablename__ = "bank_transactions"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # BT-2026-01-0143
    bank_account: Mapped[str] = mapped_column(String, default="OPERATING-4471")
    period_id: Mapped[str] = mapped_column(ForeignKey("periods.id"))
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)  # signed
    description: Mapped[str] = mapped_column(String)  # raw bank memo
    counterparty_hint: Mapped[str] = mapped_column(String, default="")  # normalized name if derivable
    type: Mapped[str] = mapped_column(String, default="ach")  # ach|wire|card|fee|deposit|payroll|transfer
    reference: Mapped[str] = mapped_column(String, default="")  # e.g. invoice number in memo
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False)
    reconciled_with: Mapped[list] = mapped_column(JSON, default=list)  # invoice/payment/payroll ids
    reconciliation_note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="unreconciled")  # unreconciled|reconciled|exception


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # JE-2026-01-0001
    period_id: Mapped[str] = mapped_column(ForeignKey("periods.id"))
    date: Mapped[date] = mapped_column(Date)
    account_code: Mapped[str] = mapped_column(ForeignKey("gl_accounts.code"))
    debit: Mapped[float] = mapped_column(Float, default=0.0)
    credit: Mapped[float] = mapped_column(Float, default=0.0)
    memo: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String, default="")  # ap_invoice|ar_invoice|bank|payroll|agent
    source_id: Mapped[str] = mapped_column(String, default="")
    created_by: Mapped[str] = mapped_column(String, default="system")  # system|agent:<name>|human


class PayrollRun(Base):
    __tablename__ = "payroll_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # PR-2026-01-1
    period_id: Mapped[str] = mapped_column(ForeignKey("periods.id"))
    pay_date: Mapped[date] = mapped_column(Date)
    gross: Mapped[float] = mapped_column(Float)
    net: Mapped[float] = mapped_column(Float)
    employer_taxes: Mapped[float] = mapped_column(Float)
    headcount: Mapped[int] = mapped_column(Integer)
    bank_txn_id: Mapped[str | None] = mapped_column(String, nullable=True)


class GroundTruth(Base):
    """Hidden labels for metrics. NEVER exposed via agent tools."""

    __tablename__ = "ground_truth"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period_id: Mapped[str] = mapped_column(ForeignKey("periods.id"))
    entity_type: Mapped[str] = mapped_column(String)  # bank_transaction|ap_invoice|ar_invoice
    entity_id: Mapped[str] = mapped_column(String, index=True)
    exception_type: Mapped[str] = mapped_column(String, default="clean")  # see ARCHITECTURE §2
    pattern: Mapped[str] = mapped_column(String, default="exact_match")  # expected hypothesis kind
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    matched_ids: Mapped[list] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    requires_human: Mapped[bool] = mapped_column(Boolean, default=False)  # policy items (no_po, unknown)
    group_id: Mapped[str | None] = mapped_column(String, nullable=True)  # links siblings (split/combined)


# ---------------------------------------------------------------------------
# Memory graph persistence (owned by app.memory.graph)
# ---------------------------------------------------------------------------
class GraphNodeRow(Base):
    __tablename__ = "graph_nodes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String, index=True)
    props: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GraphEdgeRow(Base):
    __tablename__ = "graph_edges"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    rel: Mapped[str] = mapped_column(String, index=True)
    src: Mapped[str] = mapped_column(String, index=True)
    dst: Mapped[str] = mapped_column(String, index=True)
    props: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


Index("ix_bank_period_status", BankTransaction.period_id, BankTransaction.status)
Index("ix_ap_period_status", APInvoice.period_id, APInvoice.status)
Index("ix_ar_period_status", ARInvoice.period_id, ARInvoice.status)
Index("ix_gt_period_entity", GroundTruth.period_id, GroundTruth.entity_type)
