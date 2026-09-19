from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import Table, select

from app.api.dependencies import Manager
from app.db.models import Base
from app.db.session import get_session

router = APIRouter()
DATA_TABLES = {
    name: Base.metadata.tables[name]
    for name in (
        "bank_transactions", "ap_invoices", "ar_invoices",
        "purchase_orders", "payments", "ledger_entries",
    )
}
ENTITY_TABLES = {
    "bank_transaction": DATA_TABLES["bank_transactions"],
    "ap_invoice": DATA_TABLES["ap_invoices"],
    "ar_invoice": DATA_TABLES["ar_invoices"],
    "purchase_order": DATA_TABLES["purchase_orders"],
    "payment": DATA_TABLES["payments"],
    "payroll_run": Base.metadata.tables["payroll_runs"],
}


def entity_row(entity_type: str, entity_id: str) -> dict:
    table = ENTITY_TABLES.get(entity_type)
    if table is None:
        return {}
    with get_session() as session:
        row = session.execute(select(table).where(table.c.id == entity_id)).mappings().first()
        return jsonable_encoder(dict(row)) if row is not None else {}


def related_rows(entity_ids: set[str]) -> list[dict]:
    if not entity_ids:
        return []
    result: list[dict] = []
    with get_session() as session:
        for table in ENTITY_TABLES.values():
            rows = session.execute(
                select(table).where(table.c.id.in_(entity_ids)).order_by(table.c.id),
            ).mappings()
            result.extend(jsonable_encoder(dict(row)) for row in rows)
    return result


@router.get("/data/{table_name}", response_model=list[dict])
async def data_rows(
    table_name: str, manager: Manager, period_id: str | None = None,
    reveal: Annotated[int, Query(ge=0, le=1)] = 0,
) -> list[dict]:
    table: Table | None = DATA_TABLES.get(table_name)
    if table_name == "ground_truth":
        if reveal != 1:
            raise HTTPException(403, "Ground truth is hidden; use reveal=1 for demo transparency")
        table = Base.metadata.tables["ground_truth"]
    if table is None:
        raise HTTPException(404, f"Unknown data table: {table_name}")
    statement = select(table).order_by(table.c.id)
    if period_id is not None:
        manager.require_period(period_id)
        statement = statement.where(table.c.period_id == period_id)
    with get_session() as session:
        return [jsonable_encoder(dict(row)) for row in session.execute(statement).mappings()]
