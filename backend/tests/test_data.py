from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import inspect, select

from app.config import settings
from app.data.generator import GeneratorConfig, SyntheticDataset, generate_dataset
from app.data.seed import seed_database
from app.db import session as db_session
from app.db.models import BankTransaction, Base, Period


PERIODS = ("2026-01", "2026-02", "2026-03")
EXPECTED_CASES = {
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


@pytest.fixture(scope="module")
def dataset() -> SyntheticDataset:
    return generate_dataset(seed=42, company_name="Acceptance Company")


@pytest.fixture
def isolated_seed_db(tmp_path: Path) -> Iterator[Path]:
    original_path = settings.db_path
    db_session.reset_engine()
    path = tmp_path / "seed-acceptance.sqlite"
    settings.db_path = str(path)
    try:
        yield path
    finally:
        db_session.reset_engine()
        settings.db_path = original_path


def _dataset_rows(dataset: SyntheticDataset) -> dict[str, list[dict[str, object]]]:
    return {
        name: [
            {
                column.key: inspect(row).dict.get(column.key)
                for column in inspect(row).mapper.column_attrs
            }
            for row in rows
        ]
        for name, rows in dataset.tables()
    }


def _database_rows() -> dict[str, list[dict[str, object]]]:
    with db_session.get_session() as session:
        return {
            name: [
                dict(row)
                for row in session.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings()
            ]
            for name, table in Base.metadata.tables.items()
            if name not in ("graph_nodes", "graph_edges")
        }


@pytest.mark.parametrize("seed", [0, 42, 2026])
def test_same_seed_reproduces_every_column(seed: int) -> None:
    first = generate_dataset(seed, "Acceptance Company")
    second = generate_dataset(seed, "Acceptance Company")

    assert _dataset_rows(first) == _dataset_rows(second)


def test_different_seed_changes_financial_data(dataset: SyntheticDataset) -> None:
    other = generate_dataset(43, "Acceptance Company")

    assert [
        (row.date, row.amount, row.description) for row in dataset.bank_transactions
    ] != [(row.date, row.amount, row.description) for row in other.bank_transactions]


def test_installments_preserve_cash_and_invoice_obligations(dataset: SyntheticDataset) -> None:
    baseline = generate_dataset(42, "Acceptance Company", GeneratorConfig(target_bank_transactions=0))
    for period in PERIODS:
        before = sum(row.amount for row in baseline.bank_transactions if row.period_id == period)
        after = sum(row.amount for row in dataset.bank_transactions if row.period_id == period)
        assert after == pytest.approx(before, abs=0.01)
    expected: dict[str, float] = defaultdict(float)
    actual: dict[str, float] = defaultdict(float)
    for payment in baseline.payments:
        assert len(payment.ap_invoice_ids) == 1
        expected[payment.ap_invoice_ids[0]] += payment.amount
    for payment in dataset.payments:
        actual[payment.ap_invoice_ids[0]] += payment.amount
    assert dict(actual) == pytest.approx(dict(expected), abs=0.01)


@pytest.mark.parametrize("period_id", PERIODS)
def test_planted_counts_match_architecture(
    dataset: SyntheticDataset, period_id: str
) -> None:
    groups: dict[str, set[str]] = defaultdict(set)
    for truth in dataset.ground_truth:
        if truth.group_id and f"-{period_id}-" in truth.group_id:
            groups[truth.exception_type].add(truth.group_id)

    assert {kind: len(ids) for kind, ids in groups.items()} == EXPECTED_CASES


@pytest.mark.parametrize("period_id", PERIODS)
def test_bank_volume_matches_architecture(
    dataset: SyntheticDataset, period_id: str
) -> None:
    count = sum(row.period_id == period_id for row in dataset.bank_transactions)

    assert 180 <= count <= 220, (
        f"{period_id} has {count} bank transactions; ARCHITECTURE §2 requires ~180–220"
    )


@pytest.mark.parametrize("period_id", PERIODS)
def test_invoice_purchase_order_and_payroll_scale(
    dataset: SyntheticDataset, period_id: str
) -> None:
    assert 50 <= sum(row.period_id == period_id for row in dataset.ap_invoices) <= 80
    assert 35 <= sum(row.period_id == period_id for row in dataset.ar_invoices) <= 50
    assert (
        40 <= sum(row.period_id == period_id for row in dataset.purchase_orders) <= 50
    )
    assert sum(row.period_id == period_id for row in dataset.payroll_runs) == 2


def test_truth_covers_each_financial_item_once(dataset: SyntheticDataset) -> None:
    expected = {
        *(("bank_transaction", row.id) for row in dataset.bank_transactions),
        *(("ap_invoice", row.id) for row in dataset.ap_invoices),
        *(("ar_invoice", row.id) for row in dataset.ar_invoices),
    }
    actual = Counter(
        (truth.entity_type, truth.entity_id) for truth in dataset.ground_truth
    )

    assert actual == Counter(dict.fromkeys(expected, 1))
    assert {truth.exception_type for truth in dataset.ground_truth} == {
        *EXPECTED_CASES,
        "clean",
    }


def test_ids_and_truth_matches_reference_real_entities(
    dataset: SyntheticDataset,
) -> None:
    identities = [
        inspect(row).dict["id"]
        for name, rows in dataset.tables()
        if name not in ("ground_truth", "gl_accounts")
        for row in rows
    ]
    assert len(identities) == len(set(identities))
    known = set(identities) | {account.code for account in dataset.gl_accounts}

    for truth in dataset.ground_truth:
        assert set(truth.matched_ids) <= known, truth.entity_id


@pytest.mark.parametrize("period_id", PERIODS)
def test_ledger_is_balanced_per_source(
    dataset: SyntheticDataset, period_id: str
) -> None:
    balances: dict[tuple[str, str], float] = defaultdict(float)
    for entry in dataset.ledger_entries:
        if entry.period_id == period_id:
            assert entry.debit >= 0 and entry.credit >= 0
            assert not (entry.debit and entry.credit)
            assert entry.created_by == "system"
            balances[(entry.source_type, entry.source_id)] += entry.debit - entry.credit

    assert balances
    for source, balance in balances.items():
        assert balance == pytest.approx(0, abs=0.005), source


def test_bank_dates_and_opening_cash_roll_forward(dataset: SyntheticDataset) -> None:
    periods = sorted(dataset.periods, key=lambda row: row.start_date)
    assert [period.id for period in periods] == [*PERIODS, "2026-04"]
    assert periods[-1].status == "future"

    for period in periods:
        transactions = [
            row for row in dataset.bank_transactions if row.period_id == period.id
        ]
        assert all(
            period.start_date <= row.date <= period.end_date for row in transactions
        )
        assert all(row.amount != 0 and not row.reconciled for row in transactions)
    for current, following in zip(periods, periods[1:]):
        cash_movement = sum(
            row.amount
            for row in dataset.bank_transactions
            if row.period_id == current.id
        )
        assert following.opening_cash == pytest.approx(
            current.opening_cash + cash_movement, abs=0.005
        )


@pytest.mark.parametrize("period_id", PERIODS)
def test_fee_discount_and_fx_labels_agree_with_bank_arithmetic(
    dataset: SyntheticDataset, period_id: str
) -> None:
    bank = {row.id: row for row in dataset.bank_transactions}
    receivables = {row.id: row for row in dataset.ar_invoices}
    payments = {row.id: row for row in dataset.payments}
    for truth in dataset.ground_truth:
        if truth.entity_type != "bank_transaction" or truth.period_id != period_id:
            continue
        transaction = bank[truth.entity_id]
        if truth.exception_type in ("processor_fee", "early_pay_discount"):
            invoice = receivables[truth.matched_ids[0]]
            rate = 0.03 if truth.exception_type == "processor_fee" else 0.02
            assert transaction.amount == pytest.approx(
                round(invoice.amount * (1 - rate), 2), abs=0.005
            )
        elif truth.exception_type == "vendor_fee":
            payment = payments[truth.matched_ids[0]]
            rate = 0.025 if period_id == "2026-03" else 0.02
            assert transaction.amount == pytest.approx(
                -round(payment.amount * (1 + rate), 2), abs=0.005
            )
        elif truth.exception_type == "wire_fee":
            payment = payments[truth.matched_ids[0]]
            assert transaction.amount == pytest.approx(
                -(payment.amount + 25), abs=0.005
            )
        elif truth.exception_type == "fx_variance":
            payment = payments[truth.matched_ids[0]]
            variance = abs(abs(transaction.amount) / payment.amount - 1)
            assert transaction.amount < 0
            assert 0.005 - 0.000001 <= variance <= 0.015 + 0.000001


def test_split_and_combined_payments_balance_to_their_invoices(
    dataset: SyntheticDataset,
) -> None:
    bank = {row.id: row for row in dataset.bank_transactions}
    receivables = {row.id: row for row in dataset.ar_invoices}
    for truth in dataset.ground_truth:
        if (
            truth.entity_type == "ap_invoice"
            and truth.exception_type == "split_payment"
        ):
            invoice = next(
                row for row in dataset.ap_invoices if row.id == truth.entity_id
            )
            assert len(truth.matched_ids) == 2
            assert all(bank[identifier].amount < 0 for identifier in truth.matched_ids)
            assert sum(bank[identifier].amount for identifier in truth.matched_ids) == (
                pytest.approx(-invoice.amount, abs=0.005)
            )
        elif (
            truth.entity_type == "bank_transaction"
            and truth.exception_type == "combined_payment"
        ):
            invoices = [receivables[identifier] for identifier in truth.matched_ids]
            assert 2 <= len(invoices) <= 4
            assert len({invoice.customer_id for invoice in invoices}) == 1
            assert bank[truth.entity_id].amount == pytest.approx(
                sum(invoice.amount for invoice in invoices), abs=0.005
            )


def test_timing_lag_crosses_period_boundary_including_april(
    dataset: SyntheticDataset,
) -> None:
    payments = {row.id: row for row in dataset.payments}
    periods = {row.id: row for row in dataset.periods}
    bank = {row.id: row for row in dataset.bank_transactions}
    origin_counts: Counter[str] = Counter()
    for truth in dataset.ground_truth:
        if (
            truth.entity_type != "bank_transaction"
            or truth.exception_type != "timing_lag"
        ):
            continue
        transaction = bank[truth.entity_id]
        payment = payments[truth.matched_ids[0]]
        origin_counts[payment.period_id] += 1
        assert transaction.period_id != payment.period_id
        assert 0 <= (periods[payment.period_id].end_date - payment.date).days <= 2
        assert (
            0
            <= (transaction.date - periods[transaction.period_id].start_date).days
            <= 2
        )
        assert transaction.amount == pytest.approx(-payment.amount, abs=0.005)
    assert origin_counts == dict.fromkeys(PERIODS, 2)


def test_collision_traps_have_equal_amounts_and_distinct_vendors(
    dataset: SyntheticDataset,
) -> None:
    invoices = {row.id: row for row in dataset.ap_invoices}
    groups: dict[str, list[str]] = defaultdict(list)
    for truth in dataset.ground_truth:
        if (
            truth.entity_type == "ap_invoice"
            and truth.exception_type == "amount_collision"
        ):
            assert truth.group_id is not None
            groups[truth.group_id].append(truth.entity_id)

    assert len(groups) == 3
    for identifiers in groups.values():
        assert len(identifiers) == 2
        first, second = (invoices[identifier] for identifier in identifiers)
        assert first.vendor_id != second.vendor_id
        assert first.amount == second.amount


def test_policy_and_duplicate_labels_are_supported_by_actual_rows(
    dataset: SyntheticDataset,
) -> None:
    invoices = {row.id: row for row in dataset.ap_invoices}
    orders = {row.id: row for row in dataset.purchase_orders}
    for truth in dataset.ground_truth:
        if truth.entity_type == "ap_invoice":
            invoice = invoices[truth.entity_id]
            if truth.exception_type == "no_po_invoice":
                assert invoice.po_id is None
                assert invoice.amount > 5_000
                assert truth.requires_human
            elif truth.exception_type == "price_variance":
                assert invoice.po_id is not None
                assert invoice.amount > orders[invoice.po_id].amount * 1.05
                assert truth.requires_human
            elif truth.exception_type == "duplicate_invoice":
                original = invoices[truth.matched_ids[0]]
                assert invoice.vendor_id == original.vendor_id
                assert invoice.amount == original.amount
                assert invoice.vendor_invoice_number != original.vendor_invoice_number
        elif truth.exception_type == "unknown_deposit":
            assert truth.requires_human
            assert truth.matched_ids == []


def test_seed_is_idempotent_without_reset_and_reproducible_with_reset(
    isolated_seed_db: Path,
) -> None:
    first = seed_database(reset=True, seed=42)
    assert first["inserted"] is True
    assert first["db_path"] == str(isolated_seed_db)
    before = _database_rows()

    with db_session.get_session() as session:
        assert (
            session.connection().exec_driver_sql("PRAGMA foreign_key_check").all() == []
        )
        assert (
            session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            == 1
        )
        transaction = session.scalars(select(BankTransaction)).first()
        assert transaction is not None
        transaction.reconciliation_note = "Keep existing controller work"
        period = session.get(Period, "2026-01")
        assert period is not None
        period.status = "pending_review"
    edited = _database_rows()

    kept = seed_database(reset=False, seed=99)
    assert kept["inserted"] is False
    assert _database_rows() == edited

    rebuilt = seed_database(reset=True, seed=42)
    assert rebuilt["inserted"] is True
    assert _database_rows() == before
