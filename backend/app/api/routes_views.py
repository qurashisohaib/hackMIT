from fastapi import APIRouter
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agents.brain import get_brain
from app.api.dependencies import Manager
from app.api.pipeline import get_cfo, get_forecast_builder, get_report_builder
from app.api.routes_data import DATA_TABLES
from app.config import settings
from app.data.seed import seed_database
from app.db.models import BankTransaction, Period
from app.db.session import get_session
from app.memory import reset_memory_service
from app.schemas import CloseReport, ForecastView, MetricsResponse, PeriodView

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str | bool]:
    return {
        "status": "ok", "brain": get_brain().name,
        "llm_available": settings.llm_available,
        "company": settings.company_name, "version": "0.1.0",
    }


@router.get("/periods", response_model=list[PeriodView])
async def periods(manager: Manager) -> list[PeriodView]:
    result: list[PeriodView] = []
    with get_session() as session:
        for period in session.scalars(
            select(Period).where(Period.status != "future").order_by(Period.start_date),
        ):
            stats = {
                name: session.scalar(
                    select(func.count()).select_from(table).where(table.c.period_id == period.id),
                ) or 0
                for name, table in DATA_TABLES.items()
            }
            stats["unreconciled"] = session.scalar(
                select(func.count()).select_from(BankTransaction).where(
                    BankTransaction.period_id == period.id, BankTransaction.reconciled.is_(False),
                ),
            ) or 0
            result.append(PeriodView(
                id=period.id, name=period.name,
                start_date=period.start_date.isoformat(), end_date=period.end_date.isoformat(),
                status=period.status, stats=stats, last_run_id=period.last_run_id,
            ))
    return result


@router.get("/metrics", response_model=MetricsResponse)
async def metrics(manager: Manager) -> MetricsResponse:
    return manager.metrics()


@router.get("/forecast", response_model=ForecastView)
async def forecast(manager: Manager, period_id: str | None = None) -> ForecastView:
    if period_id is None:
        with get_session() as session:
            period_id = session.scalar(
                select(Period.id).where(Period.status != "future")
                .order_by(Period.last_run_id.is_(None), Period.start_date.desc()).limit(1),
            )
        if period_id is None:
            return ForecastView(period_id="", as_of="", opening_cash=0, weeks=[], assumptions=[])
    manager.require_period(period_id)
    for node in manager.memory.store.find_nodes("Observation", scope_id=period_id, key="forecast"):
        try:
            return ForecastView.model_validate(node.props["value"])
        except (ValidationError, KeyError):
            continue
    with manager.mutation("the cash forecast is being refreshed"):
        context = get_cfo().make_context(period_id, step_delay_ms=0)
        context.run_id = None
        return await get_forecast_builder().build_forecast(context)


@router.get("/reports/{period_id}", response_model=CloseReport)
async def report(period_id: str, manager: Manager) -> CloseReport:
    manager.require_period(period_id)
    for key in ("close_report", "report"):
        for node in manager.memory.store.find_nodes("Observation", scope_id=period_id, key=key):
            try:
                return CloseReport.model_validate(node.props["value"])
            except (ValidationError, KeyError):
                continue
    with manager.mutation("the close report is being refreshed"):
        context = get_cfo().make_context(period_id, step_delay_ms=0)
        context.run_id = None
        return await get_report_builder().build_report(context)


@router.post("/reset")
async def reset(manager: Manager) -> dict[str, str]:
    with manager.mutation("the demo is being reset"):
        manager.memory.store.clear()
        seed_database(reset=True)
        memory = reset_memory_service()
        manager.clear(memory)
    return {"status": "ok", "message": "Financial data reseeded and memory cleared"}
