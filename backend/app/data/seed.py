"""Seed the SQLite financial-truth database with the synthetic company.

CLI::

    python -m app.data.seed            # seed only if the database is empty
    python -m app.data.seed --reset    # delete the SQLite file and rebuild from scratch
    python -m app.data.seed --reset --json --seed 7

Programmatic use: :func:`seed_database` (used by ``POST /api/reset``).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.config import settings
from app.data.generator import EXPECTED_PLANTED, GeneratorConfig, generate_dataset
from app.db import session as db_session
from app.db.models import Period


def _delete_sqlite_file(db_path: str) -> None:
    """Remove the database file and its WAL/SHM side files if present."""
    base = Path(db_path)
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(base) + suffix)
        if candidate.exists():
            candidate.unlink()


def seed_database(reset: bool = True, seed: int | None = None, config: GeneratorConfig | None = None) -> dict[str, Any]:
    """Populate the database and return generation statistics.

    ``reset=True`` disposes the engine, deletes the SQLite file, recreates the schema and
    inserts a fresh dataset. ``reset=False`` only creates missing tables and inserts when no
    periods exist yet (idempotent re-runs). The returned dict is ``SyntheticDataset.stats()``
    plus ``db_path`` and ``inserted`` (False when an existing dataset was kept).
    """
    if reset:
        db_session.reset_engine()
        _delete_sqlite_file(settings.db_path)
    db_session.init_db()
    with db_session.get_session() as s:
        existing = s.execute(select(func.count()).select_from(Period)).scalar_one()
    dataset = generate_dataset(seed=settings.seed if seed is None else seed, company_name=settings.company_name, config=config)
    stats = dataset.stats()
    stats["db_path"] = settings.db_path
    if existing and not reset:
        stats["inserted"] = False
        return stats
    with db_session.get_session() as s:
        for _, rows in dataset.tables():
            s.add_all(rows)
            s.flush()
    stats["inserted"] = True
    return stats


def format_stats(stats: dict[str, Any]) -> str:
    """Render the stats dict as the sanity-check table printed by the CLI."""
    lines: list[str] = []
    lines.append(f"{stats['company']}  (seed={stats['seed']})  ->  {stats.get('db_path', '')}")
    m = stats["master"]
    lines.append(f"master data: {m['vendors']} vendors, {m['customers']} customers, {m['employees']} employees, {m['gl_accounts']} GL accounts")
    lines.append("")
    periods = stats["periods"]
    cols = ("purchase_orders", "goods_receipts", "ap_invoices", "ar_invoices", "payments", "bank_transactions",
            "bank_deposits", "bank_debits", "payroll_runs", "ledger_entries", "ground_truth_rows")
    header = f"{'metric':<20}" + "".join(f"{pid:>16}" for pid in periods)
    lines.append(header)
    lines.append("-" * len(header))
    for col in cols:
        lines.append(f"{col:<20}" + "".join(f"{periods[pid][col]:>16}" for pid in periods))
    for col in ("opening_cash", "bank_net", "closing_cash"):
        lines.append(f"{col:<20}" + "".join(f"{periods[pid][col]:>16,.2f}" for pid in periods))
    lines.append("")
    lines.append("ground truth (planted cases per origin period; bank/ap/ar = truth rows in that period)")
    header = f"{'exception_type':<20}{'expected':>9}" + "".join(f"{pid:>22}" for pid in periods)
    lines.append(header)
    lines.append("-" * len(header))
    types = list(EXPECTED_PLANTED) + ["clean"]
    for etype in types:
        expected = EXPECTED_PLANTED.get(etype, "-")
        row = f"{etype:<20}{expected!s:>9}"
        for pid in periods:
            e = periods[pid]["exceptions"].get(etype, {"planted": 0, "bank": 0, "ap": 0, "ar": 0})
            cell = f"{e['planted']} (b{e['bank']}/ap{e['ap']}/ar{e['ar']})"
            row += f"{cell:>22}"
        lines.append(row)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: seed (optionally reset) and print the sanity-check table or JSON."""
    parser = argparse.ArgumentParser(description="Seed the CFO Office synthetic company database.")
    parser.add_argument("--reset", action="store_true", help="delete the SQLite file and rebuild")
    parser.add_argument("--seed", type=int, default=None, help="override CFO_SEED for this run")
    parser.add_argument("--json", action="store_true", help="print stats as JSON instead of a table")
    args = parser.parse_args(argv)
    stats = seed_database(reset=args.reset, seed=args.seed)
    if args.json:
        print(json.dumps(stats, indent=2, default=str))
    else:
        print(format_stats(stats))
        if not stats["inserted"]:
            print("\nexisting data kept (run with --reset to rebuild)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
