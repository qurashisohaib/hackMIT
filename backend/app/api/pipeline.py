"""Deferred access to the finance pipeline's shared interface."""
from importlib import import_module
from typing import Protocol, cast

from app.agents.base import AgentContext
from app.memory import MemoryService
from app.schemas import CloseReport, ForecastView, ResolveRequest, ResolveResponse, RunMetrics, RunSummary


class CFO(Protocol):
    def make_context(
        self, period_id: str, run_id: str | None = None, step_delay_ms: int | None = None,
    ) -> AgentContext: ...

    async def run_period_close(
        self, period_id: str, run_id: str | None = None, ctx: AgentContext | None = None,
    ) -> RunSummary: ...

    async def resolve_exception(
        self, exception_id: str, request: ResolveRequest, ctx: AgentContext | None = None,
    ) -> ResolveResponse: ...

    def reset_period_state(self, period_id: str, memory: MemoryService | None = None, keep_run_id: str | None = None) -> None: ...

    def compute_metrics(self, ctx: AgentContext, wall_ms: float = 0) -> RunMetrics: ...


class ForecastBuilder(Protocol):
    async def build_forecast(self, ctx: AgentContext) -> ForecastView: ...


class ReportBuilder(Protocol):
    async def build_report(self, ctx: AgentContext) -> CloseReport: ...


def get_cfo() -> CFO:
    return cast(CFO, import_module("app.agents.cfo"))


def get_forecast_builder() -> ForecastBuilder:
    return cast(ForecastBuilder, import_module("app.agents.forecast"))


def get_report_builder() -> ReportBuilder:
    return cast(ReportBuilder, import_module("app.agents.report"))
