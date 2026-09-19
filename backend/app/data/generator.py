"""Deterministic synthetic company: master data, three monthly periods, planted exceptions
and the hidden ground truth used by the metrics layer.

Everything is driven by one ``random.Random(seed)`` instance consumed in a fixed order, so the
same seed always yields byte-identical data. The generator never touches the database; it
returns a :class:`SyntheticDataset` of SQLAlchemy model instances that ``app.data.seed``
inserts. Bank-side ledger entries are intentionally *not* created (agents post them when
they reconcile); only invoice and payroll entries are posted by "system".

Planted exception types and per-period counts follow ARCHITECTURE.md §2 exactly and are
asserted by :meth:`SyntheticDataset.validate`.
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, get_args

from app.db.models import (
    APInvoice,
    ARInvoice,
    BankTransaction,
    Customer,
    Employee,
    GLAccount,
    GoodsReceipt,
    GroundTruth,
    LedgerEntry,
    Payment,
    PayrollRun,
    Period,
    PurchaseOrder,
    Vendor,
)
from app.schemas import EXCEPTION_TYPES, HypothesisKind

# --------------------------------------------------------------------------- constants
OPENING_CASH_JAN = 1_850_000.00
BANK_ACCOUNT = "OPERATING-4471"
MONTHLY_BANK_FEE = 35.00
WIRE_FEE = 25.00
STRIPE_FEE_RATE = 0.03
EARLY_PAY_DISCOUNT_RATE = 0.02
PRICE_VARIANCE_RATE = 0.08
FX_TOLERANCE_PCT = 0.015

# Periods the agents close, plus a tiny "future" April that only holds March's in-transit txns.
PERIOD_SPECS: tuple[tuple[str, str, date, date, str], ...] = (
    ("2026-01", "January 2026", date(2026, 1, 1), date(2026, 1, 31), "open"),
    ("2026-02", "February 2026", date(2026, 2, 1), date(2026, 2, 28), "open"),
    ("2026-03", "March 2026", date(2026, 3, 1), date(2026, 3, 31), "open"),
    ("2026-04", "April 2026", date(2026, 4, 1), date(2026, 4, 30), "future"),
)
CLOSE_PERIODS = ("2026-01", "2026-02", "2026-03")

# EUR→USD at invoice date, per period (Bosch invoices).
FX_RATES = {"2026-01": 1.0842, "2026-02": 1.0791, "2026-03": 1.0865}
# CloudSpan surcharge: 2.0% in Jan/Feb, drifts to 2.5% in March (both invoices).
VENDOR_FEE_RATES = {"2026-01": 0.02, "2026-02": 0.02, "2026-03": 0.025}

# Planted cases per close period (ARCHITECTURE.md §2). A "case" is one group_id.
EXPECTED_PLANTED: dict[str, int] = {
    "processor_fee": 3,
    "vendor_fee": 2,
    "early_pay_discount": 2,
    "wire_fee": 2,
    "fx_variance": 1,
    "split_payment": 2,
    "combined_payment": 2,
    "timing_lag": 2,
    "duplicate_invoice": 2,
    "no_po_invoice": 1,
    "price_variance": 1,
    "unknown_deposit": 1,
    "amount_collision": 1,
}
VALID_PATTERNS = frozenset(get_args(HypothesisKind))

# --------------------------------------------------------------------------- master data
GL_ACCOUNTS: tuple[tuple[str, str, str], ...] = (
    ("1000", "Cash", "asset"),
    ("1200", "Accounts Receivable", "asset"),
    ("1500", "Inventory", "asset"),
    ("2000", "Accounts Payable", "liability"),
    ("2100", "Accrued Liabilities", "liability"),
    ("4000", "Revenue", "revenue"),
    ("5000", "Cost of Goods Sold", "expense"),
    ("6000", "Operating Expenses", "expense"),
    ("6100", "Cloud & Software", "expense"),
    ("6200", "Payroll", "expense"),
    ("6300", "Bank & Processing Fees", "expense"),
    ("6400", "Rent", "expense"),
    ("6500", "FX Gain/Loss", "expense"),
    ("6600", "Discounts Given", "expense"),
)

EMPLOYEES: tuple[tuple[str, str, str, float], ...] = (
    ("E001", "Maya Chen", "CFO", 1e9),
    ("E002", "Daniel Okafor", "Controller", 50_000.0),
    ("E003", "Priya Raman", "AP Manager", 10_000.0),
    ("E004", "Luis Ortega", "Ops Lead", 5_000.0),
    ("E005", "Sarah Kim", "Procurement Analyst", 2_500.0),
    ("E006", "Tom Novak", "Warehouse Supervisor", 1_000.0),
)
PO_REQUESTERS = ("E005", "E004", "E006")

# Vendor catalogue. `memo` is how the bank prints the counterparty (always contains
# `descriptor`), `po` the PO amount range, `small` the sub-$5k non-PO bill range.
VENDORS: tuple[dict[str, Any], ...] = (
    dict(id="V001", name="CloudSpan Inc", category="cloud", gl="6100", descriptor="CLOUDSPAN",
         memo="CLOUDSPAN INC", vin="CS-", po=(8_000, 24_000),
         items=("Reserved GPU compute - training cluster", "Managed Kubernetes - production",
                "Object storage, egress & CDN", "Fleet telemetry ingestion tier")),
    dict(id="V002", name="Bosch Sensortec GmbH", category="components", gl="1500", currency="EUR",
         country="DE", descriptor="BOSCH SENSORTEC", memo="BOSCH SENSORTEC GMBH", vin="RE-",
         po=(12_000, 40_000),
         items=("BMI088 IMU modules (reel of 2,500)", "BMP390 pressure sensors (lot of 4,000)",
                "BHI360 smart sensor hubs (lot of 1,200)")),
    dict(id="V003", name="Shenzhen Nexa Electronics Co", category="components", gl="1500",
         country="CN", descriptor="NEXA ELECTRONICS", memo="SHENZHEN NEXA ELECTRONICS CO",
         vin="NX-", po=(9_000, 38_000),
         items=("Motor driver PCBAs (lot of 600)", "Wiring harness assemblies (lot of 900)",
                "Li-ion battery packs 48V (lot of 150)")),
    dict(id="V004", name="Tanaka Precision Ltd", category="components", gl="1500", country="JP",
         descriptor="TANAKA PRECISION", memo="TANAKA PRECISION LTD", vin="TP-",
         po=(7_000, 32_000),
         items=("Harmonic drive gearboxes (lot of 40)", "Precision cross-roller bearings (lot of 200)",
                "Servo encoder assemblies (lot of 120)")),
    dict(id="V-STRIPE", name="Stripe", category="payments", gl="6300", descriptor="STRIPE",
         memo="STRIPE", vin="ST-", is_processor=True, items=()),
    dict(id="V005", name="Pacific Freight Logistics", category="logistics", gl="6000",
         descriptor="PACIFIC FREIGHT", memo="PACIFIC FREIGHT LOGISTICS", vin="PFL-",
         po=(3_000, 15_000), small=(250, 1_900),
         items=("Ocean freight - 2x40ft container Shenzhen-Oakland", "Air freight - expedited servo shipment",
                "Domestic LTL - customer deliveries (month)"),
         small_items=("Weekly parcel manifest", "LTL shipment - customer delivery", "Customs brokerage fee")),
    dict(id="V006", name="Harbor Point Properties", category="rent", gl="6400",
         descriptor="HARBOR POINT", memo="HARBOR POINT PROPERTIES", vin="HPP-",
         po=(18_200, 19_400), small=(650, 1_200),
         items=("Monthly lease - Building 4 assembly floor & offices",),
         small_items=("CAM & utilities pass-through",)),
    dict(id="V007", name="Meridian Contract Engineering", category="contractors", gl="6000",
         descriptor="MERIDIAN CONTRACT", memo="MERIDIAN CONTRACT ENG", vin="MCE-",
         po=(6_000, 30_000), small=(1_800, 4_600),
         items=("Firmware contractor SOW - motion control phase 2", "Test fixture design & build",
                "Field service engineering - customer commissioning"),
         small_items=("Weekly T&M timesheet - embedded engineer", "Weekly T&M timesheet - test technician")),
    dict(id="V008", name="Lattice Workspace", category="saas", gl="6100",
         descriptor="LATTICE WORKSPACE", memo="LATTICE WORKSPACE", vin="LW-",
         po=(9_000, 15_000), small=(180, 2_900),
         items=("Annual enterprise workspace renewal (60 seats)",),
         small_items=("Additional seats - monthly", "Storage add-on - monthly", "SSO & audit log add-on")),
    dict(id="V009", name="Summit Insurance Group", category="insurance", gl="6000",
         descriptor="SUMMIT INSURANCE", memo="SUMMIT INSURANCE GRP", vin="SIG-",
         small=(2_100, 4_600), items=(),
         small_items=("General liability & property premium - monthly",)),
    dict(id="V010", name="Bay Area Power & Water", category="utilities", gl="6000",
         descriptor="BAY AREA PWR", memo="BAY AREA PWR & WATER", vin="BAPW-",
         small=(200, 3_800), items=(),
         small_items=("Electricity - Building 4", "Water & sewer - Building 4")),
    dict(id="V011", name="Keystone Machining Works", category="components", gl="1500",
         descriptor="KEYSTONE MACHINING", memo="KEYSTONE MACHINING WORKS", vin="KMW-",
         po=(4_000, 28_000),
         items=("CNC machined arm links (lot of 200)", "Anodized base plates (lot of 80)",
                "Gripper finger blanks (lot of 500)", "Aluminum joint housings (lot of 120)")),
    dict(id="V012", name="Northwind Packaging Supply", category="packaging", gl="1500",
         descriptor="NORTHWIND PKG", memo="NORTHWIND PKG SUPPLY", vin="NPS-",
         po=(1_500, 9_000), small=(300, 2_400),
         items=("Custom foam inserts - L-200 crates (lot of 150)", "Export-grade shipping crates (lot of 60)"),
         small_items=("Corrugated cartons & void fill", "Pallet wrap & strapping")),
    dict(id="V013", name="Orbit Marketing Partners", category="marketing", gl="6000",
         descriptor="ORBIT MARKETING", memo="ORBIT MARKETING PARTNERS", vin="OMP-",
         po=(5_000, 22_000), small=(800, 3_900),
         items=("Automate 2026 trade show booth build", "Product launch video production",
                "Demand-gen campaign - Q1"),
         small_items=("Paid search management - monthly", "Content retainer - monthly")),
    dict(id="V014", name="DigiKey Electronics", category="components", gl="1500",
         descriptor="DIGIKEY", memo="DIGIKEY ELECTRONICS", vin="DK-", card=True,
         po=(4_000, 14_000), small=(45, 1_900),
         items=("Connector & passive kit - production run",),
         small_items=("Prototype components order", "Connectors & cable assemblies",
                      "Microcontroller dev boards", "Passives restock")),
    dict(id="V015", name="Amazon Business", category="supplies", gl="6000",
         descriptor="AMZN BUSINESS", memo="AMZN BUSINESS", vin="AMZ-", card=True,
         small=(20, 950), items=(),
         small_items=("Lab consumables", "Office supplies", "ESD workstation supplies",
                      "Tooling & hand tools", "Break room supplies")),
)

# Vendor pools used by the per-period plan.
CLEAN_PO_VENDORS = ("V011", "V011", "V011", "V012", "V005", "V007", "V007", "V013", "V008", "V014")
EXCEPTION_DOMESTIC_VENDORS = ("V011", "V012", "V007", "V013", "V005", "V014")
NO_PO_VENDORS = ("V007", "V013", "V011")
NO_PO_ITEMS = (
    "Emergency rework of robotics cell - T&M, no PO raised",
    "Trade show booth expansion - verbal approval",
    "Rush machining of replacement arm links - no PO raised",
)
WIRE_VENDORS = ("V003", "V004")
SMALL_BILL_PLAN: tuple[tuple[str, int], ...] = (
    ("V010", 2), ("V008", 3), ("V005", 4), ("V007", 4), ("V012", 3), ("V013", 2),
    ("V009", 1), ("V006", 1), ("V014", 9), ("V015", 9),
)

# Customer catalogue: (id, name, terms, channel, descriptor, memo name, days-to-pay mean/spread,
# invoice amount range).
CUSTOMERS: tuple[dict[str, Any], ...] = (
    dict(id="C001", name="Apex Manufacturing", channel="direct", descriptor="APEX MFG",
         memo="APEX MFG", pay=(22, 4), amt=(8_000, 40_000)),
    dict(id="C002", name="Brightline Automation", channel="direct", descriptor="BRIGHTLINE AUTO",
         memo="BRIGHTLINE AUTOMATION", pay=(18, 4), amt=(6_000, 45_000)),
    dict(id="C003", name="Cascade Robotics Labs", channel="direct", descriptor="CASCADE ROBOTICS",
         memo="CASCADE ROBOTICS LABS", pay=(14, 3), amt=(5_000, 30_000)),
    dict(id="C004", name="Delta Fabrication Inc", channel="direct", descriptor="DELTA FAB",
         memo="DELTA FAB INC", pay=(25, 5), amt=(9_000, 48_000)),
    dict(id="C005", name="Evergreen Logistics", channel="direct", descriptor="EVERGREEN LOGISTICS",
         memo="EVERGREEN LOGISTICS", pay=(16, 3), amt=(6_000, 36_000)),
    dict(id="C006", name="Foundry Dynamics", channel="direct", descriptor="FOUNDRY DYNAMICS",
         memo="FOUNDRY DYNAMICS", pay=(20, 4), amt=(7_000, 42_000)),
    dict(id="C007", name="Nimbus Home Systems", channel="stripe", descriptor="STRIPE",
         memo="STRIPE", pay=(4, 1), amt=(2_000, 15_000)),
    dict(id="C008", name="Orchard Retail Co", channel="stripe", descriptor="STRIPE",
         memo="STRIPE", pay=(4, 1), amt=(2_000, 15_000)),
    dict(id="C009", name="Pioneer Drone Works", channel="stripe", descriptor="STRIPE",
         memo="STRIPE", pay=(4, 1), amt=(2_000, 15_000)),
)
DIRECT_CUSTOMERS = ("C001", "C002", "C003", "C004", "C005", "C006")
COMBINED_CUSTOMERS = ("C002", "C003", "C004", "C005", "C006")
STRIPE_CUSTOMERS = ("C007", "C008", "C009")
DIRECT_ITEMS = (
    "Lumen L-200 collaborative arm (qty 2)", "Lumen L-200 collaborative arm (qty 1)",
    "VisionKit V3 camera module (qty 6)", "Integration services - cell commissioning",
    "Annual support & software subscription", "Gripper G-40 end effector (qty 4)",
    "Mobile base M-1 (qty 1)", "Onsite training (2 days)", "Safety scanner package",
    "Spare parts kit - L-200", "Conveyor tracking license (qty 3)",
)
STRIPE_ITEMS = (
    "Lumen Home Assist unit (qty 1)", "Home Assist accessory bundle", "Extended warranty - 2 years",
    "Replacement battery pack (qty 3)", "Home Assist starter kit (qty 2)",
)
UNKNOWN_DEPOSITS = (
    ("ZELLE FROM J. WHITFIELD", 1_275.50),
    ("ZELLE FROM R. NAKAMURA", 2_140.00),
    ("ZELLE FROM J. WHITFIELD", 985.25),
)
PAYROLL_HEADCOUNT = 42
PAYROLL_GROSS_PER_RUN = 262_500.00
PAYROLL_NET_RATIO = 0.712
PAYROLL_EMPLOYER_TAX_RATIO = 0.0865


@dataclass(frozen=True)
class GeneratorConfig:
    """Volume knobs. Defaults follow ARCHITECTURE.md §2 scale for POs / AP / AR.

    Bank volume is bounded by invoice volume (every clean bank txn maps to exactly one
    payment or AR invoice), so raising the invoice counts is the way to raise bank volume.
    """

    n_clean_po_invoices: int = 22
    n_received_not_invoiced_pos: int = 4
    n_open_pos: int = 5
    n_clean_ar_invoices: int = 34
    ap_paid_fraction: float = 0.80
    small_bill_paid_fraction: float = 0.92
    ar_paid_fraction: float = 0.78
    memo_reference_rate: float = 0.60
    small_bills: tuple[tuple[str, int], ...] = SMALL_BILL_PLAN


# --------------------------------------------------------------------------- draft objects
class _Ref:
    """Base for anything whose ``id`` is resolved lazily (ids are assigned after date sorting)."""

    id: str


@dataclass
class _BankSpec(_Ref):
    """A bank transaction to materialise once every referenced id is known."""

    period_id: str
    date: date
    amount: float
    type: str
    counterparty_hint: str
    memo: Callable[[], tuple[str, str]]  # -> (description, reference)
    seq: int
    txn: BankTransaction | None = None

    @property
    def id(self) -> str:  # type: ignore[override]
        assert self.txn is not None and self.txn.id, "bank txn id requested before materialisation"
        return self.txn.id


@dataclass
class _TruthSpec:
    """Ground-truth row whose object references are resolved to ids at the end of the build."""

    entity: Any
    entity_type: str
    period_id: str
    exception_type: str = "clean"
    pattern: str = "exact_match"
    params: dict[str, Any] = field(default_factory=dict)
    matched: list[Any] = field(default_factory=list)
    explanation: Callable[[], str] | str = ""
    requires_human: bool = False
    group_id: str | None = None


def _resolve(value: Any) -> Any:
    """Replace model objects / bank specs with their ids inside nested params."""
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_resolve(v) for v in value]
    if isinstance(value, (_Ref, PurchaseOrder, APInvoice, ARInvoice, Payment, PayrollRun, BankTransaction)):
        return value.id
    return value


def _with_ap_reference(base: Callable[[], tuple[str, str]], ref: Callable[[], str]) -> Callable[[], tuple[str, str]]:
    """Wrap a memo builder so the bank line also prints a reference token."""

    def build() -> tuple[str, str]:
        text, _ = base()
        token = ref()
        return f"{text} REF {token}", token

    return build


def _money(value: float) -> float:
    return round(value + 1e-9, 2)


def _fmt(value: float) -> str:
    return f"{value:,.2f}"


# --------------------------------------------------------------------------- dataset
@dataclass
class SyntheticDataset:
    """All rows for one synthetic company, in insert order, plus hidden ground truth."""

    seed: int
    company_name: str
    config: GeneratorConfig
    periods: list[Period] = field(default_factory=list)
    gl_accounts: list[GLAccount] = field(default_factory=list)
    vendors: list[Vendor] = field(default_factory=list)
    customers: list[Customer] = field(default_factory=list)
    employees: list[Employee] = field(default_factory=list)
    purchase_orders: list[PurchaseOrder] = field(default_factory=list)
    goods_receipts: list[GoodsReceipt] = field(default_factory=list)
    ap_invoices: list[APInvoice] = field(default_factory=list)
    ar_invoices: list[ARInvoice] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    bank_transactions: list[BankTransaction] = field(default_factory=list)
    ledger_entries: list[LedgerEntry] = field(default_factory=list)
    payroll_runs: list[PayrollRun] = field(default_factory=list)
    ground_truth: list[GroundTruth] = field(default_factory=list)

    def tables(self) -> list[tuple[str, list[Any]]]:
        """Rows grouped by table, ordered so foreign keys are satisfied on insert."""
        return [
            ("periods", self.periods),
            ("gl_accounts", self.gl_accounts),
            ("vendors", self.vendors),
            ("customers", self.customers),
            ("employees", self.employees),
            ("purchase_orders", self.purchase_orders),
            ("goods_receipts", self.goods_receipts),
            ("ap_invoices", self.ap_invoices),
            ("ar_invoices", self.ar_invoices),
            ("payments", self.payments),
            ("bank_transactions", self.bank_transactions),
            ("ledger_entries", self.ledger_entries),
            ("payroll_runs", self.payroll_runs),
            ("ground_truth", self.ground_truth),
        ]

    # -- stats ------------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        """Per-period row counts, cash movement and planted-exception counts.

        ``exceptions[type]`` holds ``planted`` (distinct cases originating in the period),
        and raw ground-truth row counts by entity type (``bank``/``ap``/``ar``); timing-lag
        bank rows land in the *following* period, so their bank count shows there.
        """
        out: dict[str, Any] = {
            "seed": self.seed,
            "company": self.company_name,
            "master": {
                "vendors": len(self.vendors),
                "customers": len(self.customers),
                "employees": len(self.employees),
                "gl_accounts": len(self.gl_accounts),
            },
            "periods": {},
        }
        by_period_type: dict[tuple[str, str], Counter] = {}
        planted: dict[str, dict[str, set[str]]] = {}
        for gt in self.ground_truth:
            by_period_type.setdefault((gt.period_id, gt.exception_type), Counter())[gt.entity_type] += 1
            if gt.group_id:
                # group ids look like "PFEE-2026-01-3": the period in the middle is the origin period
                _, year, month, _ = gt.group_id.split("-")
                planted.setdefault(f"{year}-{month}", {}).setdefault(gt.exception_type, set()).add(gt.group_id)
        for p in self.periods:
            pid = p.id
            bank = [b for b in self.bank_transactions if b.period_id == pid]
            net = _money(sum(b.amount for b in bank))
            exc: dict[str, dict[str, int]] = {}
            for etype in EXCEPTION_TYPES:
                rows = by_period_type.get((pid, etype), Counter())
                groups = planted.get(pid, {}).get(etype, set())
                entry = {
                    "planted": len(groups) if etype != "clean" else 0,
                    "bank": rows.get("bank_transaction", 0),
                    "ap": rows.get("ap_invoice", 0),
                    "ar": rows.get("ar_invoice", 0),
                }
                if any(entry.values()):
                    exc[etype] = entry
            out["periods"][pid] = {
                "name": p.name,
                "status": p.status,
                "opening_cash": p.opening_cash,
                "bank_net": net,
                "closing_cash": _money(p.opening_cash + net),
                "purchase_orders": sum(1 for x in self.purchase_orders if x.period_id == pid),
                "goods_receipts": sum(1 for x in self.goods_receipts if x.period_id == pid),
                "ap_invoices": sum(1 for x in self.ap_invoices if x.period_id == pid),
                "ar_invoices": sum(1 for x in self.ar_invoices if x.period_id == pid),
                "payments": sum(1 for x in self.payments if x.period_id == pid),
                "bank_transactions": len(bank),
                "bank_deposits": sum(1 for b in bank if b.amount > 0),
                "bank_debits": sum(1 for b in bank if b.amount < 0),
                "payroll_runs": sum(1 for x in self.payroll_runs if x.period_id == pid),
                "ledger_entries": sum(1 for x in self.ledger_entries if x.period_id == pid),
                "ground_truth_rows": sum(1 for x in self.ground_truth if x.period_id == pid),
                "exceptions": exc,
            }
        return out

    # -- self-check ---------------------------------------------------------------------
    def validate(self) -> None:
        """Assert the structural invariants the rest of the system relies on."""
        truth_by_entity = Counter((gt.entity_type, gt.entity_id) for gt in self.ground_truth)
        for etype, rows in (
            ("bank_transaction", self.bank_transactions),
            ("ap_invoice", self.ap_invoices),
            ("ar_invoice", self.ar_invoices),
        ):
            for row in rows:
                n = truth_by_entity.get((etype, row.id), 0)
                if n != 1:
                    raise AssertionError(f"{etype} {row.id} has {n} ground_truth rows (expected 1)")
        if len(truth_by_entity) != len(self.ground_truth):
            raise AssertionError("duplicate ground_truth rows detected")
        keyed_rows = [r for name, rows in self.tables() if name not in ("ground_truth", "gl_accounts") for r in rows]
        ids = [r.id for r in keyed_rows]
        if len(ids) != len(set(ids)):
            raise AssertionError("duplicate primary keys generated")
        known_ids = set(ids) | {a.code for a in self.gl_accounts}
        for gt in self.ground_truth:
            if gt.exception_type not in EXCEPTION_TYPES:
                raise AssertionError(f"unknown exception_type {gt.exception_type}")
            if gt.pattern not in VALID_PATTERNS:
                raise AssertionError(f"unknown pattern {gt.pattern}")
            for mid in gt.matched_ids:
                if mid not in known_ids:
                    raise AssertionError(f"ground_truth {gt.entity_id} references unknown id {mid}")
        stats = self.stats()
        for pid in CLOSE_PERIODS:
            exc = stats["periods"][pid]["exceptions"]
            for etype, expected in EXPECTED_PLANTED.items():
                got = exc.get(etype, {}).get("planted", 0)
                if got != expected:
                    raise AssertionError(f"{pid}: planted {etype}={got}, expected {expected}")


# --------------------------------------------------------------------------- per-period scratch
@dataclass
class _PeriodCtx:
    period: Period
    next_period: Period
    pos: list[PurchaseOrder] = field(default_factory=list)
    grs: list[GoodsReceipt] = field(default_factory=list)
    aps: list[APInvoice] = field(default_factory=list)
    ars: list[ARInvoice] = field(default_factory=list)
    pays: list[Payment] = field(default_factory=list)
    bank: list[_BankSpec] = field(default_factory=list)
    carry_out: list[_BankSpec] = field(default_factory=list)
    payroll: list[PayrollRun] = field(default_factory=list)
    truths: list[_TruthSpec] = field(default_factory=list)
    group_counters: Counter = field(default_factory=Counter)

    @property
    def pid(self) -> str:
        return self.period.id

    @property
    def days(self) -> int:
        return (self.period.end_date - self.period.start_date).days + 1

    def day(self, offset: int) -> date:
        return self.period.start_date + timedelta(days=offset)

    def group(self, tag: str) -> str:
        self.group_counters[tag] += 1
        return f"{tag}-{self.pid}-{self.group_counters[tag]}"


class _Generator:
    """Stateful builder; one instance per :func:`generate_dataset` call."""

    def __init__(self, seed: int, company_name: str, config: GeneratorConfig) -> None:
        self.rng = random.Random(seed)
        self.config = config
        self.ds = SyntheticDataset(seed=seed, company_name=company_name, config=config)
        self.vendor_spec: dict[str, dict[str, Any]] = {v["id"]: v for v in VENDORS}
        self.customer_spec: dict[str, dict[str, Any]] = {c["id"]: c for c in CUSTOMERS}
        self.vendors: dict[str, Vendor] = {}
        self.customers: dict[str, Customer] = {}
        self.used_cents: set[int] = set()
        self.vin_counter: dict[str, int] = {}
        self.truth_specs: list[_TruthSpec] = []
        self._bank_seq = 0

    # ------------------------------------------------------------------ helpers
    def _unique_amount(self, lo: float, hi: float, step: int | None = None,
                       derived: tuple[Callable[[float], float], ...] = ()) -> float:
        """Draw an amount no other invoice/txn uses (also reserving every derived amount).

        ``step`` snaps to whole-dollar multiples: 1 keeps % fees exact in cents, 5 looks like
        a subscription or rent, 20 keeps a 2.5% surcharge exact. ``None`` draws any cents.
        """
        for _ in range(10_000):
            if step:
                value = float(self.rng.randint(int(lo) // step, int(hi) // step) * step)
            else:
                value = _money(self.rng.uniform(lo, hi))
            candidates = [value] + [_money(fn(value)) for fn in derived]
            cents = {int(round(c * 100)) for c in candidates}  # a 50/50 split legitimately repeats a value
            if not cents & self.used_cents:
                self.used_cents.update(cents)
                return value
        raise RuntimeError("could not draw a unique amount")

    def _reserve(self, value: float) -> float:
        value = _money(value)
        self.used_cents.add(int(round(value * 100)))
        return value

    def _vin(self, vendor_id: str) -> str:
        """Next vendor invoice number for a vendor (monotonic with realistic gaps)."""
        if vendor_id not in self.vin_counter:
            self.vin_counter[vendor_id] = self.rng.randint(10_000, 89_999)
        self.vin_counter[vendor_id] += self.rng.randint(1, 9)
        return f"{self.vendor_spec[vendor_id]['vin']}{self.vin_counter[vendor_id]}"

    @staticmethod
    def _approver(amount: float) -> str:
        if amount <= 5_000:
            return "E004"
        if amount <= 10_000:
            return "E003"
        if amount <= 50_000:
            return "E002"
        return "E001"

    def _with_reference(self) -> bool:
        return self.rng.random() < self.config.memo_reference_rate

    def _bank(self, ctx: _PeriodCtx, when: date, amount: float, type_: str, hint: str,
              memo: Callable[[], tuple[str, str]], period_id: str | None = None) -> _BankSpec:
        self._bank_seq += 1
        spec = _BankSpec(period_id=period_id or ctx.pid, date=when, amount=_money(amount), type=type_,
                         counterparty_hint=hint, memo=memo, seq=self._bank_seq)
        if spec.period_id == ctx.pid:
            ctx.bank.append(spec)
        else:
            ctx.carry_out.append(spec)
        return spec

    def _truth(self, ctx: _PeriodCtx, **kw: Any) -> _TruthSpec:
        spec = _TruthSpec(period_id=kw.pop("period_id", ctx.pid), **kw)
        self.truth_specs.append(spec)
        return spec

    # ------------------------------------------------------------------ memo builders
    def _ap_memo(self, vendor_id: str, method: str, ref: str | None) -> Callable[[], tuple[str, str]]:
        name = self.vendor_spec[vendor_id]["memo"]
        if method == "wire":
            base = f"WIRE OUT {name}"
        elif method == "card":
            base = self.rng.choice((f"CARD PURCHASE {name}", f"POS DEBIT {name}"))
        else:
            base = self.rng.choice((f"ACH DEBIT {name}", f"{name} ACH PMT", f"ONLINE PMT {name}"))

        def build() -> tuple[str, str]:
            return (f"{base} REF {ref}", ref) if ref else (base, "")

        return build

    def _ap_reference(self, invoice: APInvoice, payment: Payment, method: str) -> Callable[[], str]:
        """Pick which identifier the bank line prints: our payment id, our invoice id or the
        vendor's own number (card purchases always print the vendor's order number)."""
        pick = 1.0 if method == "card" else self.rng.random()

        def ref() -> str:
            if pick < 0.5:
                return payment.id
            if pick < 0.75:
                return invoice.id
            return invoice.vendor_invoice_number

        return ref

    def _deposit_memo(self, customer_id: str, refs: Callable[[], list[str]] | None,
                      wire: bool = False) -> Callable[[], tuple[str, str]]:
        name = self.customer_spec[customer_id]["memo"]
        variant = self.rng.random()

        def build() -> tuple[str, str]:
            ids = refs() if refs else []
            if wire:
                base = f"INCOMING WIRE {name}"
                return (f"{base} REF {' '.join(ids)}", ",".join(ids)) if ids else (base, "")
            if ids:
                return f"{name} ACH CREDIT INV {' '.join(ids)}", ",".join(ids)
            return (f"ACH CREDIT {name}" if variant < 0.5 else f"{name} ACH CREDIT"), ""

        return build

    # ------------------------------------------------------------------ master data
    def _build_master(self) -> None:
        for code, name, type_ in GL_ACCOUNTS:
            self.ds.gl_accounts.append(GLAccount(code=code, name=name, type=type_))
        for eid, name, role, limit in EMPLOYEES:
            self.ds.employees.append(Employee(id=eid, name=name, role=role, approval_limit=limit))
        for spec in VENDORS:
            v = Vendor(
                id=spec["id"], name=spec["name"], category=spec["category"],
                payment_terms_days=spec.get("terms", 30), currency=spec.get("currency", "USD"),
                default_gl_account=spec["gl"], country=spec.get("country", "US"),
                is_payment_processor=spec.get("is_processor", False), bank_descriptor=spec["descriptor"],
            )
            self.vendors[v.id] = v
            self.ds.vendors.append(v)
        for spec in CUSTOMERS:
            c = Customer(
                id=spec["id"], name=spec["name"], payment_terms_days=30, currency="USD",
                channel=spec["channel"], bank_descriptor=spec["descriptor"],
            )
            self.customers[c.id] = c
            self.ds.customers.append(c)

    def _build_periods(self) -> list[Period]:
        periods = [
            Period(id=pid, name=name, start_date=start, end_date=end, status=status, opening_cash=0.0,
                   closed_at=None, last_run_id=None)
            for pid, name, start, end, status in PERIOD_SPECS
        ]
        self.ds.periods.extend(periods)
        return periods

    # ------------------------------------------------------------------ row factories
    def _po(self, ctx: _PeriodCtx, vendor_id: str, when: date, amount: float, description: str,
            currency: str = "USD", status: str = "invoiced") -> PurchaseOrder:
        po = PurchaseOrder(
            id="", vendor_id=vendor_id, period_id=ctx.pid, date=when, amount=amount, currency=currency,
            status=status, description=description, requested_by=self.rng.choice(PO_REQUESTERS),
            approved_by=self._approver(amount),
        )
        ctx.pos.append(po)
        return po

    def _gr(self, ctx: _PeriodCtx, po: PurchaseOrder, when: date, notes: str = "Received in full") -> GoodsReceipt:
        gr = GoodsReceipt(id="", po_id="", period_id=ctx.pid, date=when, amount=po.amount,
                          received_by="E006", notes=notes)
        gr._po = po  # resolved to po_id once ids exist
        ctx.grs.append(gr)
        return gr

    def _ap(self, ctx: _PeriodCtx, vendor_id: str, when: date, amount: float, description: str,
            po: PurchaseOrder | None, currency: str = "USD", fx_rate: float | None = None,
            paid: bool = True, vin: str | None = None, held: bool = False) -> APInvoice:
        """Vendor bill. ``held`` bills (duplicate / no-PO / price variance) carry no approver."""
        spec = self.vendor_spec[vendor_id]
        inv = APInvoice(
            id="", vendor_id=vendor_id, po_id=None, period_id=ctx.pid,
            vendor_invoice_number=vin or self._vin(vendor_id), date=when,
            due_date=when + timedelta(days=spec.get("terms", 30)), amount=amount, currency=currency,
            fx_rate=fx_rate, status="paid" if paid else "open", gl_account=spec["gl"],
            description=description, approved_by=None if held else self._approver(amount * (fx_rate or 1.0)),
            paid_amount=amount if paid else 0.0, matched_bank_txn_ids=[],
        )
        inv._po = po
        ctx.aps.append(inv)
        return inv

    def _ar(self, ctx: _PeriodCtx, customer_id: str, when: date, amount: float, description: str) -> ARInvoice:
        inv = ARInvoice(
            id="", customer_id=customer_id, period_id=ctx.pid, date=when, due_date=when + timedelta(days=30),
            amount=amount, currency="USD", status="open", description=description, paid_amount=0.0,
            matched_bank_txn_ids=[],
        )
        ctx.ars.append(inv)
        return inv

    def _pay(self, ctx: _PeriodCtx, inv: APInvoice, when: date, amount_usd: float, method: str,
             status: str = "sent") -> Payment:
        pay = Payment(
            id="", vendor_id=inv.vendor_id, period_id=ctx.pid, ap_invoice_ids=[], date=when,
            amount=_money(amount_usd), method=method, approved_by=self._approver(amount_usd), status=status,
            bank_txn_id=None,
        )
        pay._invoices = [inv]
        ctx.pays.append(pay)
        return pay

    def _clean_chain(self, ctx: _PeriodCtx, vendor_id: str, po_offset: int, amount: float, description: str,
                     paid: bool) -> tuple[PurchaseOrder, APInvoice]:
        """PO -> receipt -> invoice for a domestic vendor; caller decides about payment."""
        po_date = ctx.day(po_offset)
        po = self._po(ctx, vendor_id, po_date, amount, description)
        gr_date = po_date + timedelta(days=self.rng.randint(2, 5))
        self._gr(ctx, po, gr_date)
        inv_date = gr_date + timedelta(days=self.rng.randint(1, 3))
        inv = self._ap(ctx, vendor_id, inv_date, amount, description, po, paid=paid)
        return po, inv

    def _clean_ap_settlement(self, ctx: _PeriodCtx, inv: APInvoice, pay_date: date, bt_date: date,
                             method: str = "ach") -> None:
        """Payment + bank debit + clean truth rows for a fully paid AP invoice."""
        pay = self._pay(ctx, inv, pay_date, inv.amount, method)
        with_ref = self._with_reference()
        base_memo = self._ap_memo(inv.vendor_id, method, None)
        memo = _with_ap_reference(base_memo, self._ap_reference(inv, pay, method)) if with_ref else base_memo
        vendor_name = self.vendors[inv.vendor_id].name
        bt = self._bank(ctx, bt_date, -inv.amount, method, vendor_name, memo)
        pattern = "exact_match" if with_ref else "counterparty_exact"
        self._truth(ctx, entity=bt, entity_type="bank_transaction", pattern=pattern,
                    params={"invoice_id": inv, "payment_id": pay, "memo_reference": with_ref},
                    matched=[pay],
                    explanation=f"{vendor_name} paid in full ({'memo cites reference' if with_ref else 'match by counterparty + amount'})")
        self._truth(ctx, entity=inv, entity_type="ap_invoice", pattern=pattern,
                    params={"payment_id": pay}, matched=[bt],
                    explanation=f"Paid by {pay.method.upper()} on {pay_date.isoformat()}")

    def _open_ap_truth(self, ctx: _PeriodCtx, inv: APInvoice) -> None:
        self._truth(ctx, entity=inv, entity_type="ap_invoice", pattern="exact_match", params={},
                    matched=[], explanation="Open at period end (not yet paid)")

    def _open_ar_truth(self, ctx: _PeriodCtx, inv: ARInvoice) -> None:
        self._truth(ctx, entity=inv, entity_type="ar_invoice", pattern="exact_match", params={},
                    matched=[], explanation="Open at period end (customer has not paid)")

    def _clean_deposit(self, ctx: _PeriodCtx, inv: ARInvoice, when: date) -> None:
        with_ref = self._with_reference()
        wire = inv.amount >= 30_000 and self.rng.random() < 0.5
        memo = self._deposit_memo(inv.customer_id, (lambda: [inv.id]) if with_ref else None, wire=wire)
        cust = self.customers[inv.customer_id]
        bt = self._bank(ctx, when, inv.amount, "wire" if wire else "deposit", cust.name, memo)
        pattern = "exact_match" if with_ref else "counterparty_exact"
        self._truth(ctx, entity=bt, entity_type="bank_transaction", pattern=pattern,
                    params={"invoice_id": inv, "memo_reference": with_ref}, matched=[inv],
                    explanation=lambda inv=inv, name=cust.name: f"{name} paid {inv.id} in full")
        self._truth(ctx, entity=inv, entity_type="ar_invoice", pattern=pattern, params={}, matched=[bt],
                    explanation=f"Collected on {when.isoformat()}")

    # ------------------------------------------------------------------ clean volume
    def _gen_clean_po_invoices(self, ctx: _PeriodCtx) -> None:
        cfg = self.config
        for i in range(cfg.n_clean_po_invoices + 1):
            if i == cfg.n_clean_po_invoices:
                vendor_id = "V006"  # one rent PO every month
            else:
                vendor_id = self.rng.choice(CLEAN_PO_VENDORS)
            spec = self.vendor_spec[vendor_id]
            amount = self._unique_amount(*spec["po"], step=5 if vendor_id in ("V006", "V008") else None)
            description = self.rng.choice(spec["items"])
            if vendor_id == "V006":
                description = f"{description} ({ctx.period.name})"
            paid = self.rng.random() < cfg.ap_paid_fraction
            if paid:
                # chain max = 5 + 3 + 14 + 2 = 24 days -> fits every month
                po_offset = self.rng.randint(0, max(0, ctx.days - 1 - 24))
                _, inv = self._clean_chain(ctx, vendor_id, po_offset, amount, description, paid=True)
                pay_date = inv.date + timedelta(days=self.rng.randint(3, 14))
                bt_date = pay_date + timedelta(days=self.rng.randint(0, 2))
                method = "card" if spec.get("card") else "ach"
                self._clean_ap_settlement(ctx, inv, pay_date, bt_date, method)
            else:
                po_offset = self.rng.randint(4, ctx.days - 10)
                _, inv = self._clean_chain(ctx, vendor_id, po_offset, amount, description, paid=False)
                self._open_ap_truth(ctx, inv)

    def _gen_other_pos(self, ctx: _PeriodCtx) -> None:
        """Received-not-invoiced (accrual candidates) and still-open POs."""
        for _ in range(self.config.n_received_not_invoiced_pos):
            vendor_id = self.rng.choice(CLEAN_PO_VENDORS)
            spec = self.vendor_spec[vendor_id]
            amount = self._unique_amount(*spec["po"])
            po_date = ctx.day(self.rng.randint(ctx.days - 14, ctx.days - 5))
            po = self._po(ctx, vendor_id, po_date, amount, self.rng.choice(spec["items"]), status="received")
            self._gr(ctx, po, po_date + timedelta(days=self.rng.randint(2, 4)), notes="Received; invoice pending")
        for _ in range(self.config.n_open_pos):
            vendor_id = self.rng.choice(CLEAN_PO_VENDORS)
            spec = self.vendor_spec[vendor_id]
            amount = self._unique_amount(*spec["po"])
            po_date = ctx.day(self.rng.randint(ctx.days - 10, ctx.days - 1))
            self._po(ctx, vendor_id, po_date, amount, self.rng.choice(spec["items"]), status="open")

    def _gen_small_bills(self, ctx: _PeriodCtx) -> None:
        """Sub-$5k non-PO bills (utilities, SaaS, freight, T&M, card purchases)."""
        for vendor_id, count in self.config.small_bills:
            spec = self.vendor_spec[vendor_id]
            for _ in range(count):
                amount = self._unique_amount(*spec["small"], step=5 if vendor_id in ("V008", "V009", "V006") else None)
                description = self.rng.choice(spec["small_items"])
                if vendor_id == "V010":
                    description = "Electricity - Building 4" if amount > 800 else "Water & sewer - Building 4"
                method = "card" if spec.get("card") else "ach"
                paid = self.rng.random() < self.config.small_bill_paid_fraction
                if paid:
                    lag = 0 if method == "card" else self.rng.randint(2, 10)
                    inv_date = ctx.day(self.rng.randint(0, ctx.days - 1 - lag - 1))
                    inv = self._ap(ctx, vendor_id, inv_date, amount, description, None, paid=True)
                    pay_date = inv_date + timedelta(days=lag)
                    bt_date = pay_date if method == "card" else pay_date + timedelta(days=self.rng.randint(0, 1))
                    self._clean_ap_settlement(ctx, inv, pay_date, bt_date, method)
                else:
                    inv_date = ctx.day(self.rng.randint(12, ctx.days - 1))
                    inv = self._ap(ctx, vendor_id, inv_date, amount, description, None, paid=False)
                    self._open_ap_truth(ctx, inv)

    def _gen_clean_ar(self, ctx: _PeriodCtx) -> None:
        for _ in range(self.config.n_clean_ar_invoices):
            customer_id = self.rng.choice(DIRECT_CUSTOMERS)
            cspec = self.customer_spec[customer_id]
            amount = self._unique_amount(*cspec["amt"])
            description = self.rng.choice(DIRECT_ITEMS)
            paid = self.rng.random() < self.config.ar_paid_fraction
            if paid:
                mean, spread = cspec["pay"]
                pay_days = max(4, min(ctx.days - 2, int(round(self.rng.gauss(mean, spread)))))
                if customer_id == "C001":
                    pay_days = max(pay_days, 15)  # Apex only takes its discount when paying early
                inv_date = ctx.day(self.rng.randint(0, ctx.days - 1 - pay_days))
                inv = self._ar(ctx, customer_id, inv_date, amount, description)
                self._clean_deposit(ctx, inv, inv_date + timedelta(days=pay_days))
            else:
                inv_date = ctx.day(self.rng.randint(8, ctx.days - 1))
                inv = self._ar(ctx, customer_id, inv_date, amount, description)
                self._open_ar_truth(ctx, inv)

    # ------------------------------------------------------------------ planted exceptions
    def _gen_processor_fee(self, ctx: _PeriodCtx) -> None:
        for customer_id in STRIPE_CUSTOMERS:
            cspec = self.customer_spec[customer_id]
            amount = self._unique_amount(*cspec["amt"], step=1,
                                         derived=(lambda a: a * (1 - STRIPE_FEE_RATE),))
            inv_date = ctx.day(self.rng.randint(0, 20))
            inv = self._ar(ctx, customer_id, inv_date, amount, self.rng.choice(STRIPE_ITEMS))
            payout = _money(amount * (1 - STRIPE_FEE_RATE))
            fee = _money(amount - payout)
            payout_ref = f"ST-{self.rng.randrange(16 ** 5):05X}"
            group = ctx.group("PFEE")

            def memo(inv=inv, payout_ref=payout_ref) -> tuple[str, str]:
                return f"STRIPE PAYOUT {payout_ref} REF {inv.id}", inv.id

            bt = self._bank(ctx, inv_date + timedelta(days=self.rng.randint(2, 6)), payout, "deposit", "Stripe", memo)
            params = {"rate": STRIPE_FEE_RATE, "direction": "deduct", "scope_type": "vendor", "scope_id": "V-STRIPE",
                      "gross": amount, "fee": fee, "customer_id": customer_id}
            self._truth(ctx, entity=bt, entity_type="bank_transaction", exception_type="processor_fee",
                        pattern="rule_percentage_fee", params=params, matched=[inv], group_id=group,
                        explanation=f"Stripe payout for {cspec['name']} net of 3.0% fee (gross {_fmt(amount)}, fee {_fmt(fee)})")
            self._truth(ctx, entity=inv, entity_type="ar_invoice", exception_type="processor_fee",
                        pattern="rule_percentage_fee", params=params, matched=[bt], group_id=group,
                        explanation="Settled via Stripe payout net of processing fee")

    def _gen_vendor_fee(self, ctx: _PeriodCtx) -> None:
        rate = VENDOR_FEE_RATES[ctx.pid]
        spec = self.vendor_spec["V001"]
        for _ in range(2):
            amount = self._unique_amount(*spec["po"], step=20, derived=(lambda a, r=rate: a * (1 + r),))
            po_offset = self.rng.randint(0, 6)
            _, inv = self._clean_chain(ctx, "V001", po_offset, amount, self.rng.choice(spec["items"]), paid=True)
            pay_date = inv.date + timedelta(days=self.rng.randint(3, 10))
            pay = self._pay(ctx, inv, pay_date, amount, "ach")
            debit = _money(amount * (1 + rate))
            memo = self._ap_memo("V001", "ach", None)

            def memo_ref(inv=inv, memo=memo) -> tuple[str, str]:
                text, _ = memo()
                return f"{text} REF {inv.vendor_invoice_number}", inv.vendor_invoice_number

            bt = self._bank(ctx, pay_date + timedelta(days=self.rng.randint(0, 2)), -debit, "ach", spec["name"], memo_ref)
            group = ctx.group("VFEE")
            params = {"rate": rate, "direction": "add", "scope_type": "vendor", "scope_id": "V001",
                      "invoice_amount": amount, "surcharge": _money(debit - amount), "invoice_id": inv, "payment_id": pay}
            self._truth(ctx, entity=bt, entity_type="bank_transaction", exception_type="vendor_fee",
                        pattern="rule_percentage_fee", params=params, matched=[pay], group_id=group,
                        explanation=f"CloudSpan auto-debit includes {rate * 100:.1f}% surcharge on {_fmt(amount)}")
            self._truth(ctx, entity=inv, entity_type="ap_invoice", exception_type="vendor_fee",
                        pattern="rule_percentage_fee", params=params, matched=[bt], group_id=group,
                        explanation=f"Paid with {rate * 100:.1f}% vendor surcharge")

    def _gen_wire_fee(self, ctx: _PeriodCtx) -> None:
        for vendor_id in WIRE_VENDORS:
            spec = self.vendor_spec[vendor_id]
            amount = self._unique_amount(*spec["po"], derived=(lambda a: a + WIRE_FEE,))
            _, inv = self._clean_chain(ctx, vendor_id, self.rng.randint(0, 6), amount, self.rng.choice(spec["items"]), paid=True)
            pay_date = inv.date + timedelta(days=self.rng.randint(3, 10))
            pay = self._pay(ctx, inv, pay_date, amount, "wire")
            with_ref = vendor_id == "V004"
            memo = self._ap_memo(vendor_id, "wire", None)

            def memo_fn(pay=pay, memo=memo, with_ref=with_ref) -> tuple[str, str]:
                text, _ = memo()
                return (f"{text} REF {pay.id}", pay.id) if with_ref else (text, "")

            bt = self._bank(ctx, pay_date + timedelta(days=self.rng.randint(0, 1)), -(amount + WIRE_FEE), "wire",
                            spec["name"], memo_fn)
            group = ctx.group("WFEE")
            params = {"amount": WIRE_FEE, "direction": "add", "scope_type": "global", "scope_id": None,
                      "invoice_amount": amount, "invoice_id": inv, "payment_id": pay, "memo_reference": with_ref}
            self._truth(ctx, entity=bt, entity_type="bank_transaction", exception_type="wire_fee",
                        pattern="rule_fixed_fee", params=params, matched=[pay], group_id=group,
                        explanation=f"International wire to {spec['name']} debited with $25.00 bank fee")
            self._truth(ctx, entity=inv, entity_type="ap_invoice", exception_type="wire_fee",
                        pattern="rule_fixed_fee", params=params, matched=[bt], group_id=group,
                        explanation="Paid by international wire (+$25.00 fee)")

    def _gen_fx_variance(self, ctx: _PeriodCtx) -> None:
        spec = self.vendor_spec["V002"]
        rate = FX_RATES[ctx.pid]
        variance = self.rng.choice((-1, 1)) * self.rng.uniform(0.005, 0.015)
        amount_eur = self._unique_amount(*spec["po"], step=1,
                                         derived=(lambda a: a * rate, lambda a: a * rate * (1 + variance)))
        invoice_usd = _money(amount_eur * rate)
        debit_usd = _money(amount_eur * rate * (1 + variance))
        po_date = ctx.day(self.rng.randint(0, 6))
        po = self._po(ctx, "V002", po_date, amount_eur, self.rng.choice(spec["items"]), currency="EUR")
        gr_date = po_date + timedelta(days=self.rng.randint(3, 6))
        self._gr(ctx, po, gr_date)
        inv = self._ap(ctx, "V002", gr_date + timedelta(days=self.rng.randint(1, 3)), amount_eur,
                       po.description, po, currency="EUR", fx_rate=rate, paid=True)
        pay_date = inv.date + timedelta(days=self.rng.randint(3, 9))
        pay = self._pay(ctx, inv, pay_date, invoice_usd, "wire")

        def memo(pay=pay) -> tuple[str, str]:
            return f"WIRE OUT BOSCH SENSORTEC GMBH EUR {_fmt(amount_eur)} REF {pay.id}", pay.id

        bt = self._bank(ctx, pay_date + timedelta(days=self.rng.randint(0, 2)), -debit_usd, "wire", spec["name"], memo)
        group = ctx.group("FX")
        params = {"tolerance_pct": FX_TOLERANCE_PCT, "scope_type": "vendor", "scope_id": "V002", "fx_rate": rate,
                  "amount_eur": amount_eur, "invoice_usd": invoice_usd, "settled_usd": debit_usd,
                  "variance_pct": round(variance, 5), "invoice_id": inv, "payment_id": pay}
        self._truth(ctx, entity=bt, entity_type="bank_transaction", exception_type="fx_variance",
                    pattern="rule_fx_tolerance", params=params, matched=[pay], group_id=group,
                    explanation=f"EUR {_fmt(amount_eur)} settled in USD at {variance * 100:+.2f}% vs booked rate {rate}")
        self._truth(ctx, entity=inv, entity_type="ap_invoice", exception_type="fx_variance",
                    pattern="rule_fx_tolerance", params=params, matched=[bt], group_id=group,
                    explanation="EUR invoice settled in USD with FX variance")

    def _gen_split_payment(self, ctx: _PeriodCtx) -> None:
        for _ in range(2):
            vendor_id = self.rng.choice(EXCEPTION_DOMESTIC_VENDORS)
            spec = self.vendor_spec[vendor_id]
            share = self.rng.choice((0.6, 0.5, 0.7))
            amount = self._unique_amount(max(spec["po"][0], 12_000), max(spec["po"][1], 20_000), step=1,
                                         derived=(lambda a, s=share: a * s, lambda a, s=share: a * (1 - s)))
            _, inv = self._clean_chain(ctx, vendor_id, self.rng.randint(0, 6), amount, self.rng.choice(spec["items"]), paid=True)
            pay_date = inv.date + timedelta(days=self.rng.randint(3, 9))
            pay = self._pay(ctx, inv, pay_date, amount, "ach")
            part1 = _money(amount * share)
            part2 = _money(amount - part1)
            group = ctx.group("SPLIT")
            parts: list[_BankSpec] = []
            for n, part in enumerate((part1, part2), start=1):
                def memo(inv=inv, n=n, name=spec["memo"]) -> tuple[str, str]:
                    return f"ACH DEBIT {name} REF {inv.id} PART {n}/2", inv.id

                parts.append(self._bank(ctx, pay_date + timedelta(days=(n - 1) * self.rng.randint(1, 3)), -part, "ach",
                                        spec["name"], memo))
            for n, (bt, part) in enumerate(zip(parts, (part1, part2)), start=1):
                sibling = parts[1] if n == 1 else parts[0]
                self._truth(ctx, entity=bt, entity_type="bank_transaction", exception_type="split_payment",
                            pattern="split_payment", matched=[inv], group_id=group,
                            params={"share": round(part / amount, 4), "part": n, "payment_id": pay, "sibling_txn_id": sibling,
                                    "invoice_amount": amount},
                            explanation=lambda inv=inv, n=n, part=part, amount=amount: (
                                f"Part {n}/2 of {inv.id} ({_fmt(part)} of {_fmt(amount)})"))
            self._truth(ctx, entity=inv, entity_type="ap_invoice", exception_type="split_payment", pattern="split_payment",
                        params={"payment_id": pay, "parts": [part1, part2]}, matched=parts, group_id=group,
                        explanation="Settled by two bank debits")

    def _gen_timing_lag(self, ctx: _PeriodCtx) -> None:
        for n in range(2):
            vendor_id = self.rng.choice(EXCEPTION_DOMESTIC_VENDORS)
            spec = self.vendor_spec[vendor_id]
            amount = self._unique_amount(*spec["po"])
            _, inv = self._clean_chain(ctx, vendor_id, self.rng.randint(4, 12), amount, self.rng.choice(spec["items"]), paid=True)
            pay_date = ctx.period.end_date - timedelta(days=self.rng.randint(0, 2))
            pay = self._pay(ctx, inv, pay_date, amount, "ach")
            clear_date = ctx.next_period.start_date + timedelta(days=self.rng.randint(0, 2))
            with_ref = n == 0
            base = self._ap_memo(vendor_id, "ach", None)

            def memo(pay=pay, base=base, with_ref=with_ref) -> tuple[str, str]:
                text, _ = base()
                return (f"{text} REF {pay.id}", pay.id) if with_ref else (text, "")

            bt = self._bank(ctx, clear_date, -amount, "ach", spec["name"], memo, period_id=ctx.next_period.id)
            group = ctx.group("LAG")
            self._truth(ctx, entity=inv, entity_type="ap_invoice", exception_type="timing_lag", pattern="timing_lag",
                        params={"payment_date": pay_date.isoformat(), "cleared_on": clear_date.isoformat(),
                                "cleared_period": ctx.next_period.id, "bank_txn_id": bt, "payment_id": pay},
                        matched=[pay], group_id=group,
                        explanation=f"Payment {pay_date.isoformat()} in transit at period end; clears {clear_date.isoformat()}")
            self._truth(ctx, period_id=ctx.next_period.id, entity=bt, entity_type="bank_transaction",
                        exception_type="timing_lag", pattern="timing_lag",
                        params={"origin_period": ctx.pid, "invoice_id": inv, "payment_date": pay_date.isoformat(),
                                "memo_reference": with_ref},
                        matched=[pay], group_id=group,
                        explanation=f"Clears payment {pay_date.isoformat()} issued in {ctx.period.name}")

    def _gen_duplicate_invoice(self, ctx: _PeriodCtx) -> None:
        for n in range(2):
            vendor_id = self.rng.choice(EXCEPTION_DOMESTIC_VENDORS)
            spec = self.vendor_spec[vendor_id]
            amount = self._unique_amount(*spec["po"])
            po, original = self._clean_chain(ctx, vendor_id, self.rng.randint(0, 6), amount, self.rng.choice(spec["items"]), paid=True)
            pay_date = original.date + timedelta(days=self.rng.randint(3, 10))
            self._clean_ap_settlement(ctx, original, pay_date, pay_date + timedelta(days=self.rng.randint(0, 2)))
            reissued = n == 1
            vin = self._vin(vendor_id) if reissued else f"{original.vendor_invoice_number}-R"
            dup = self._ap(ctx, vendor_id, original.date + timedelta(days=self.rng.randint(2, 6)), amount,
                           original.description, po, paid=False, vin=vin, held=True)
            self._truth(ctx, entity=dup, entity_type="ap_invoice", exception_type="duplicate_invoice", pattern="duplicate",
                        params={"original_id": original, "original_vendor_invoice_number": original.vendor_invoice_number,
                                "reissued": reissued}, matched=[original], group_id=ctx.group("DUP"),
                        explanation=f"Same vendor/amount/description as {'re-issued' if reissued else 'suffixed'} copy of the original bill")

    def _gen_no_po(self, ctx: _PeriodCtx) -> None:
        idx = CLOSE_PERIODS.index(ctx.pid)
        vendor_id = NO_PO_VENDORS[idx]
        amount = self._unique_amount(6_000, 19_000)
        inv = self._ap(ctx, vendor_id, ctx.day(self.rng.randint(6, ctx.days - 4)), amount, NO_PO_ITEMS[idx], None,
                       paid=False, held=True)
        self._truth(ctx, entity=inv, entity_type="ap_invoice", exception_type="no_po_invoice", pattern="policy_no_po",
                    params={"threshold_usd": 5_000.0}, matched=[], requires_human=True, group_id=ctx.group("NOPO"),
                    explanation=f"{_fmt(amount)} invoice without a purchase order exceeds the $5,000 no-PO threshold")

    def _gen_price_variance(self, ctx: _PeriodCtx) -> None:
        vendor_id = self.rng.choice(("V011", "V012", "V007"))
        spec = self.vendor_spec[vendor_id]
        po_amount = self._unique_amount(*spec["po"], step=1, derived=(lambda a: a * (1 + PRICE_VARIANCE_RATE),))
        po_date = ctx.day(self.rng.randint(0, 8))
        po = self._po(ctx, vendor_id, po_date, po_amount, self.rng.choice(spec["items"]))
        gr_date = po_date + timedelta(days=self.rng.randint(2, 5))
        self._gr(ctx, po, gr_date)
        inv_amount = _money(po_amount * (1 + PRICE_VARIANCE_RATE))
        inv = self._ap(ctx, vendor_id, gr_date + timedelta(days=self.rng.randint(1, 3)), inv_amount, po.description, po,
                       paid=False, held=True)
        self._truth(ctx, entity=inv, entity_type="ap_invoice", exception_type="price_variance", pattern="policy_price_variance",
                    params={"po_amount": po_amount, "invoice_amount": inv_amount, "variance_pct": PRICE_VARIANCE_RATE,
                            "tolerance_pct": 0.05}, matched=[po], requires_human=True, group_id=ctx.group("PVAR"),
                    explanation=f"Invoice {_fmt(inv_amount)} exceeds PO {_fmt(po_amount)} by 8.0% (> 5% tolerance)")

    def _gen_amount_collision(self, ctx: _PeriodCtx) -> None:
        pool = list(EXCEPTION_DOMESTIC_VENDORS)
        v1 = self.rng.choice(pool)
        v2 = self.rng.choice([v for v in pool if v != v1])
        amount = self._unique_amount(5_000, 12_000)
        group = ctx.group("COLL")
        pay_offset = self.rng.randint(0, 2)
        for vendor_id in (v1, v2):
            spec = self.vendor_spec[vendor_id]
            _, inv = self._clean_chain(ctx, vendor_id, self.rng.randint(0, 6), amount, self.rng.choice(spec["items"]), paid=True)
            pay_date = ctx.day(18 + pay_offset)
            pay = self._pay(ctx, inv, pay_date, amount, "ach")
            memo = self._ap_memo(vendor_id, "ach", None)
            bt = self._bank(ctx, pay_date + timedelta(days=self.rng.randint(0, 1)), -amount, "ach", spec["name"], memo)
            self._truth(ctx, entity=bt, entity_type="bank_transaction", exception_type="amount_collision",
                        pattern="counterparty_exact", params={"invoice_id": inv, "payment_id": pay, "collides_with": [v1, v2]},
                        matched=[pay], group_id=group,
                        explanation=f"Same amount as another vendor's invoice; memo names {spec['name']}")
            self._truth(ctx, entity=inv, entity_type="ap_invoice", exception_type="amount_collision",
                        pattern="counterparty_exact", params={"payment_id": pay}, matched=[bt], group_id=group,
                        explanation="Identical amount to another vendor's invoice this period")

    def _gen_early_pay_discount(self, ctx: _PeriodCtx) -> None:
        cspec = self.customer_spec["C001"]
        for n in range(2):
            amount = self._unique_amount(*cspec["amt"], step=1, derived=(lambda a: a * (1 - EARLY_PAY_DISCOUNT_RATE),))
            inv_date = ctx.day(self.rng.randint(0, 16))
            inv = self._ar(ctx, "C001", inv_date, amount, self.rng.choice(DIRECT_ITEMS))
            days = self.rng.randint(5, 9)
            received = _money(amount * (1 - EARLY_PAY_DISCOUNT_RATE))
            with_ref = n == 0
            memo = self._deposit_memo("C001", (lambda inv=inv: [inv.id]) if with_ref else None)
            bt = self._bank(ctx, inv_date + timedelta(days=days), received, "deposit", cspec["name"], memo)
            group = ctx.group("EPD")
            params = {"rate": EARLY_PAY_DISCOUNT_RATE, "scope_type": "customer", "scope_id": "C001", "days_to_pay": days,
                      "invoice_amount": amount, "discount": _money(amount - received), "memo_reference": with_ref}
            self._truth(ctx, entity=bt, entity_type="bank_transaction", exception_type="early_pay_discount",
                        pattern="rule_early_pay_discount", params=params, matched=[inv], group_id=group,
                        explanation=lambda inv=inv, days=days: f"Apex paid {inv.id} in {days} days less 2% early-payment discount")
            self._truth(ctx, entity=inv, entity_type="ar_invoice", exception_type="early_pay_discount",
                        pattern="rule_early_pay_discount", params=params, matched=[bt], group_id=group,
                        explanation="Collected net of 2% early-payment discount")

    def _gen_combined_payment(self, ctx: _PeriodCtx) -> None:
        customers = self.rng.sample(COMBINED_CUSTOMERS, 2)
        for n, customer_id in enumerate(customers):
            cspec = self.customer_spec[customer_id]
            invoices: list[ARInvoice] = []
            for _ in range(3):
                amount = self._unique_amount(cspec["amt"][0], cspec["amt"][1] * 0.6)
                invoices.append(self._ar(ctx, customer_id, ctx.day(self.rng.randint(0, 12)), amount, self.rng.choice(DIRECT_ITEMS)))
            total = self._reserve(sum(i.amount for i in invoices))
            with_ref = n == 0
            memo = self._deposit_memo(customer_id, (lambda invs=invoices: [i.id for i in invs]) if with_ref else None)
            when = max(i.date for i in invoices) + timedelta(days=self.rng.randint(6, 12))
            bt = self._bank(ctx, when, total, "deposit", cspec["name"], memo)
            group = ctx.group("COMB")
            self._truth(ctx, entity=bt, entity_type="bank_transaction", exception_type="combined_payment",
                        pattern="combined_invoices", params={"invoice_amounts": [i.amount for i in invoices], "memo_reference": with_ref},
                        matched=list(invoices), group_id=group,
                        explanation=f"{cspec['name']} paid three invoices with one deposit of {_fmt(total)}")
            for inv in invoices:
                self._truth(ctx, entity=inv, entity_type="ar_invoice", exception_type="combined_payment",
                            pattern="combined_invoices", params={"deposit_total": total}, matched=[bt], group_id=group,
                            explanation="Settled as part of a combined customer deposit")

    def _gen_unknown_deposit(self, ctx: _PeriodCtx) -> None:
        memo_text, amount = UNKNOWN_DEPOSITS[CLOSE_PERIODS.index(ctx.pid)]
        self._reserve(amount)
        bt = self._bank(ctx, ctx.day(self.rng.randint(5, 25)), amount, "deposit", "", lambda: (memo_text, ""))
        self._truth(ctx, entity=bt, entity_type="bank_transaction", exception_type="unknown_deposit", pattern="unknown",
                    params={"memo": memo_text}, matched=[], requires_human=True, group_id=ctx.group("UNK"),
                    explanation="Deposit from an unrecognised counterparty with no matching invoice")

    # ------------------------------------------------------------------ payroll & fees
    def _gen_payroll(self, ctx: _PeriodCtx) -> None:
        for n, pay_date in enumerate((ctx.day(14), ctx.period.end_date), start=1):
            gross = _money(PAYROLL_GROSS_PER_RUN * self.rng.uniform(0.985, 1.015))
            net = self._reserve(gross * PAYROLL_NET_RATIO)
            taxes = self._reserve(gross * PAYROLL_EMPLOYER_TAX_RATIO)
            run = PayrollRun(id=f"PR-{ctx.pid}-{n}", period_id=ctx.pid, pay_date=pay_date, gross=gross, net=net,
                             employer_taxes=taxes, headcount=PAYROLL_HEADCOUNT, bank_txn_id=None)
            ctx.payroll.append(run)
            for component, amount, text in (("net_pay", net, "PAYROLL GUSTO NET PAY"),
                                            ("employer_taxes", taxes, "GUSTO TAX EMPLOYER TAXES")):
                bt = self._bank(ctx, pay_date, -amount, "payroll", "Gusto Payroll", lambda text=text: (text, ""))
                self._truth(ctx, entity=bt, entity_type="bank_transaction", pattern="payroll",
                            params={"component": component, "payroll_run_id": run.id}, matched=[run],
                            explanation=f"Payroll run {run.id} {component.replace('_', ' ')}")

    def _gen_bank_fee(self, ctx: _PeriodCtx) -> None:
        bt = self._bank(ctx, ctx.period.end_date, -MONTHLY_BANK_FEE, "fee", "Bank", lambda: ("MONTHLY MAINTENANCE FEE", ""))
        self._truth(ctx, entity=bt, entity_type="bank_transaction", pattern="bank_fee",
                    params={"gl_account": "6300"}, matched=[], explanation="Recurring account maintenance fee")

    # ------------------------------------------------------------------ materialisation
    @staticmethod
    def _assign_ids(rows: list[Any], prefix: str, period_id: str, key: str = "date") -> None:
        rows.sort(key=lambda r: getattr(r, key))
        for n, row in enumerate(rows, start=1):
            row.id = f"{prefix}-{period_id}-{n:04d}"

    def _materialise_period(self, ctx: _PeriodCtx, carry_in: list[_BankSpec]) -> None:
        self._assign_ids(ctx.pos, "PO", ctx.pid)
        self._assign_ids(ctx.grs, "GR", ctx.pid)
        for gr in ctx.grs:
            gr.po_id = gr._po.id
        self._assign_ids(ctx.aps, "INV", ctx.pid)
        for inv in ctx.aps:
            inv.po_id = inv._po.id if inv._po is not None else None
        self._assign_ids(ctx.ars, "AR", ctx.pid)
        self._assign_ids(ctx.pays, "PAY", ctx.pid)
        for pay in ctx.pays:
            pay.ap_invoice_ids = [i.id for i in pay._invoices]

        specs = sorted(carry_in + ctx.bank, key=lambda s: (s.date, s.seq))
        for n, spec in enumerate(specs, start=1):
            description, reference = spec.memo()
            spec.txn = BankTransaction(
                id=f"BT-{ctx.pid}-{n:04d}", bank_account=BANK_ACCOUNT, period_id=ctx.pid, date=spec.date,
                amount=spec.amount, description=description, counterparty_hint=spec.counterparty_hint,
                type=spec.type, reference=reference, reconciled=False, reconciled_with=[],
                reconciliation_note="", status="unreconciled",
            )
            self.ds.bank_transactions.append(spec.txn)

        self.ds.purchase_orders.extend(ctx.pos)
        self.ds.goods_receipts.extend(ctx.grs)
        self.ds.ap_invoices.extend(ctx.aps)
        self.ds.ar_invoices.extend(ctx.ars)
        self.ds.payments.extend(ctx.pays)
        self.ds.payroll_runs.extend(ctx.payroll)
        self._post_ledger(ctx)

    def _post_ledger(self, ctx: _PeriodCtx) -> None:
        """System-posted entries for invoices and payroll (no cash entries — agents post those)."""
        entries: list[LedgerEntry] = []

        def post(when: date, account: str, debit: float, credit: float, memo: str, source_type: str, source_id: str) -> None:
            entries.append(LedgerEntry(id="", period_id=ctx.pid, date=when, account_code=account, debit=_money(debit),
                                       credit=_money(credit), memo=memo, source_type=source_type, source_id=source_id,
                                       created_by="system"))

        for inv in ctx.aps:
            usd = _money(inv.amount * (inv.fx_rate or 1.0))
            memo = f"{self.vendors[inv.vendor_id].name} {inv.vendor_invoice_number} - {inv.description}"
            post(inv.date, inv.gl_account, usd, 0.0, memo, "ap_invoice", inv.id)
            post(inv.date, "2000", 0.0, usd, memo, "ap_invoice", inv.id)
        for inv in ctx.ars:
            memo = f"{self.customers[inv.customer_id].name} - {inv.description}"
            post(inv.date, "1200", inv.amount, 0.0, memo, "ar_invoice", inv.id)
            post(inv.date, "4000", 0.0, inv.amount, memo, "ar_invoice", inv.id)
        for run in ctx.payroll:
            post(run.pay_date, "6200", run.gross, 0.0, f"{run.id} gross wages", "payroll", run.id)
            post(run.pay_date, "6200", run.employer_taxes, 0.0, f"{run.id} employer payroll taxes", "payroll", run.id)
            post(run.pay_date, "2100", 0.0, run.gross + run.employer_taxes, f"{run.id} payroll liabilities", "payroll", run.id)
        entries.sort(key=lambda e: e.date)
        for n, entry in enumerate(entries, start=1):
            entry.id = f"JE-{ctx.pid}-{n:04d}"
        self.ds.ledger_entries.extend(entries)

    def _materialise_truth(self) -> None:
        for spec in self.truth_specs:
            explanation = spec.explanation() if callable(spec.explanation) else spec.explanation
            self.ds.ground_truth.append(GroundTruth(
                period_id=spec.period_id, entity_type=spec.entity_type, entity_id=_resolve(spec.entity),
                exception_type=spec.exception_type, pattern=spec.pattern, params=_resolve(spec.params),
                matched_ids=_resolve(spec.matched), explanation=explanation, requires_human=spec.requires_human,
                group_id=spec.group_id,
            ))

    # ------------------------------------------------------------------ orchestration
    def _build_period(self, ctx: _PeriodCtx) -> None:
        self._gen_clean_po_invoices(ctx)
        self._gen_other_pos(ctx)
        self._gen_small_bills(ctx)
        self._gen_vendor_fee(ctx)
        self._gen_wire_fee(ctx)
        self._gen_fx_variance(ctx)
        self._gen_split_payment(ctx)
        self._gen_timing_lag(ctx)
        self._gen_duplicate_invoice(ctx)
        self._gen_no_po(ctx)
        self._gen_price_variance(ctx)
        self._gen_amount_collision(ctx)
        self._gen_clean_ar(ctx)
        self._gen_processor_fee(ctx)
        self._gen_early_pay_discount(ctx)
        self._gen_combined_payment(ctx)
        self._gen_unknown_deposit(ctx)
        self._gen_payroll(ctx)
        self._gen_bank_fee(ctx)

    def build(self) -> SyntheticDataset:
        """Generate master data, the three close periods and the April holding period."""
        self._build_master()
        periods = self._build_periods()
        cash = OPENING_CASH_JAN
        carry: list[_BankSpec] = []
        for idx, period in enumerate(periods[:-1]):
            period.opening_cash = _money(cash)
            ctx = _PeriodCtx(period=period, next_period=periods[idx + 1])
            self._build_period(ctx)
            self._materialise_period(ctx, carry)
            carry = ctx.carry_out
            cash += sum(b.amount for b in self.ds.bank_transactions if b.period_id == period.id)
        april = periods[-1]
        april.opening_cash = _money(cash)
        self._materialise_period(_PeriodCtx(period=april, next_period=april), carry)
        self._materialise_truth()
        self.ds.validate()
        return self.ds


def generate_dataset(seed: int, company_name: str, config: GeneratorConfig | None = None) -> SyntheticDataset:
    """Build the full deterministic dataset for ``company_name`` from ``seed``.

    Returns unsaved SQLAlchemy model instances (see :meth:`SyntheticDataset.tables`) plus
    the hidden ground truth. The result is validated before being returned.
    """
    return _Generator(seed=seed, company_name=company_name, config=config or GeneratorConfig()).build()
