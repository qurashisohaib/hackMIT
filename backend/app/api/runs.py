"""Single-writer run scheduling, durable traces and replayable event streams."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import aclosing, contextmanager
from typing import cast

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agents.base import AgentContext
from app.agents.brain import get_brain
from app.api.pipeline import get_cfo
from app.db.models import BankTransaction, Period
from app.db.session import get_session
from app.events import bus, emit
from app.memory import MemoryService
from app.schemas import AgentEvent, MetricsResponse, RunMetrics, RunView, new_id, utcnow

logger = logging.getLogger(__name__)
TERMINAL_STATUSES = {"completed", "failed"}
TERMINAL_EVENTS = {"run.completed", "run.failed"}


class RunManager:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory
        self._runs: dict[str, RunView] = {}
        self._task: asyncio.Task[None] | None = None
        self._context: AgentContext | None = None
        self._operation: str | None = None
        self._closing = False
        self.stream_epoch = asyncio.Event()

    def _ensure_available(self) -> None:
        if self._closing:
            raise HTTPException(503, "The application is shutting down")
        if self._operation is not None:
            raise HTTPException(409, f"Cannot modify financial state while {self._operation}")

    @contextmanager
    def mutation(self, operation: str) -> Iterator[None]:
        self._ensure_available()
        self._operation = operation
        try:
            yield
        finally:
            self._operation = None

    def require_period(self, period_id: str) -> Period:
        with get_session() as session:
            period = session.get(Period, period_id)
            if period is None:
                raise HTTPException(404, f"Unknown period: {period_id}")
            return period

    def start(self, period_id: str) -> str:
        self._ensure_available()
        period = self.require_period(period_id)
        if period.status == "future":
            raise HTTPException(409, "A future holding period cannot be closed")
        if any(
            node.props["period_id"] > period_id and not node.props.get("superseded")
            for node in self.memory.store.find_nodes("AgentRun", status="completed")
        ):
            raise HTTPException(409, "Later periods already ran. Reset the demo to rerun chronologically.")
        run_id = new_id("RUN")
        run = RunView(run_id=run_id, period_id=period_id, status="queued", brain=get_brain().name)
        self._runs[run_id] = run
        self._operation = f"run {run_id} is active"
        try:
            self._persist(run)
            self._task = asyncio.create_task(self._execute(run), name=f"close:{run_id}")
        except Exception:
            self._runs.pop(run_id, None)
            self._operation = None
            raise
        return run_id

    async def _execute(self, run: RunView) -> None:
        try:
            run.status = "running"
            run.started_at = utcnow()
            run.current_step = "Preparing period close"
            cfo = get_cfo()
            cfo.reset_period_state(run.period_id, memory=self.memory)
            with get_session() as session:
                period = session.get(Period, run.period_id)
                if period is None:
                    raise ValueError(f"Unknown period: {run.period_id}")
                period.last_run_id = run.run_id
            self._context = cfo.make_context(run.period_id, run_id=run.run_id)
            self._persist(run)
            summary = await cfo.run_period_close(run.period_id, run_id=run.run_id, ctx=self._context)
            if summary.run_id != run.run_id or summary.period_id != run.period_id:
                raise ValueError("The CFO returned a summary for a different run")
            if summary.status != "completed":
                raise ValueError(summary.error or "The CFO did not complete the run")
            run.status = "completed"
            run.finished_at = summary.finished_at or utcnow()
            run.metrics = summary.metrics
            run.brain = summary.brain
            run.counts = dict(summary.counts)
            run.current_step = "Completed"
            run.error = None
            self._refresh_counts(run)
        except asyncio.CancelledError:
            self._fail(run, "Run interrupted by application shutdown")
            raise
        except Exception as exc:
            logger.exception("Period close %s failed", run.run_id)
            self._fail(run, str(exc) or type(exc).__name__)
        finally:
            try:
                self._persist(run)
            finally:
                self._context = None
                self._operation = None

    def _fail(self, run: RunView, message: str) -> None:
        run.status = "failed"
        run.finished_at = utcnow()
        run.error = message
        run.current_step = "Failed"
        self._refresh_counts(run)
        with get_session() as session:
            period = session.get(Period, run.period_id)
            if period is not None and period.status == "in_progress":
                period.status = "pending_review"
        if not any(event.kind == "run.failed" for event in bus.history(run.run_id)):
            emit(run.run_id, "cfo", "run.failed", "Period close failed", message,
                 error=message, counts=run.counts)

    def _refresh_counts(self, run: RunView) -> None:
        if self._context is not None and self._context.run_id == run.run_id:
            run.counts.update(self._context.counters)
        with get_session() as session:
            reconciled = session.scalar(
                select(func.count()).select_from(BankTransaction).where(
                    BankTransaction.period_id == run.period_id,
                    BankTransaction.reconciled.is_(True),
                )
            )
        exceptions = [
            node for node in self.memory.store.find_nodes("Exception", run_id=run.run_id)
            if node.props.get("status") != "superseded"
        ]
        run.counts.update(
            reconciled=int(reconciled or 0),
            exceptions=len(exceptions),
            human=sum(node.props.get("status") == "needs_human" for node in exceptions),
        )
        run.counts.setdefault("matched", 0)
        run.counts.setdefault("precedent_hits", run.metrics.precedent_hits if run.metrics else 0)

    def get(self, run_id: str) -> RunView:
        run = self._runs.get(run_id)
        if run is None:
            raise HTTPException(404, f"Unknown run: {run_id}")
        if run.status == "running":
            self._refresh_counts(run)
            for event in reversed(bus.history(run_id)):
                if event.kind in {"agent.started", "agent.step", "run.plan", "handoff"}:
                    run.current_step = event.title
                    break
        return run.model_copy(deep=True)

    def list(self) -> list[RunView]:
        return [self.get(run_id) for run_id in reversed(self._runs)]

    def _persist(self, run: RunView) -> None:
        self.memory.store.upsert_node(run.run_id, "AgentRun", {
            **run.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in bus.history(run.run_id)],
        })

    def rehydrate(self) -> None:
        nodes = sorted(self.memory.store.find_nodes("AgentRun"),
                       key=lambda node: str(node.props.get("started_at") or node.props.get("created_at") or ""))
        interrupted: list[RunView] = []
        for node in nodes:
            try:
                run = RunView.model_validate({**node.props, "run_id": node.id})
            except ValidationError:
                logger.warning("Ignoring invalid persisted run %s", node.id, exc_info=True)
                continue
            events: list[AgentEvent] = []
            for raw in node.props.get("events", []):
                try:
                    event = AgentEvent.model_validate(raw)
                except ValidationError:
                    logger.warning("Ignoring invalid event in run %s", node.id)
                    continue
                if event.run_id == run.run_id:
                    events.append(event)
            bus.load_history(run.run_id, sorted(events, key=lambda event: event.seq))
            self._runs[run.run_id] = run
            if run.status not in TERMINAL_STATUSES:
                interrupted.append(run)
        for run in interrupted:
            self._fail(run, "Run interrupted before the application restarted")
            self._persist(run)

    def refresh_persisted_runs(self) -> None:
        for run_id, current in self._runs.items():
            node = self.memory.store.get_node(run_id)
            if node is not None:
                updated = RunView.model_validate(node.props)
                current.metrics = updated.metrics
                current.counts = updated.counts
            self._persist(current)

    def metrics(self) -> MetricsResponse:
        runs = sorted(self._runs.values(), key=lambda run: (
            (run.started_at or run.finished_at or utcnow()).isoformat(), run.run_id,
        ))
        history = [run.metrics for run in runs if run.status == "completed" and run.metrics is not None]
        latest: dict[str, RunMetrics] = {metrics.period_id: metrics for metrics in history}
        periods = [latest[period_id] for period_id in sorted(latest)]
        deltas: dict[str, dict[str, str | float | None]] = {}
        for previous, current in zip(periods, periods[1:]):
            before = previous.model_dump()
            changes: dict[str, str | float | None] = {"previous_period_id": previous.period_id}
            for key, value in current.model_dump().items():
                old = before[key]
                if isinstance(value, (int, float)) and isinstance(old, (int, float)):
                    changes[key] = round(value - old, 8)
                elif (value is None and isinstance(old, (int, float))) or (
                    old is None and isinstance(value, (int, float))
                ):
                    changes[key] = None
            deltas[current.period_id] = changes
        return MetricsResponse(periods=periods, deltas=deltas, history=history)

    def clear(self, memory: MemoryService) -> None:
        self.stream_epoch.set()
        self.stream_epoch = asyncio.Event()
        self._runs.clear()
        self._context = None
        self.memory = memory
        bus.clear()

    async def shutdown(self) -> None:
        self._closing = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        for run in self._runs.values():
            if run.status not in TERMINAL_STATUSES:
                self._fail(run, "Run interrupted by application shutdown")
                self._persist(run)
        self._operation = None
        self.stream_epoch.set()

    def cursor(self, run_id: str | None, last_event_id: str | None) -> int:
        if not last_event_id:
            return 0
        for event in reversed(bus.history(run_id)):
            if event.id == last_event_id:
                return event.seq
        if last_event_id.isdecimal():
            return int(last_event_id)
        return 0

    async def events(
        self, run_id: str | None, last_event_id: str | None, epoch: asyncio.Event,
    ) -> AsyncIterator[dict[str, str]]:
        cursor = self.cursor(run_id, last_event_id)
        if run_id is not None and self.get(run_id).status in TERMINAL_STATUSES:
            for event in bus.history(run_id, since_seq=cursor):
                yield self._sse(event)
            return
        subscription = cast(AsyncGenerator[AgentEvent, None], bus.subscribe(run_id, replay=True))
        async with aclosing(subscription) as stream:
            invalidated = asyncio.create_task(epoch.wait())
            pending = asyncio.create_task(anext(stream))
            try:
                while True:
                    done, _ = await asyncio.wait(
                        {pending, invalidated}, return_when=asyncio.FIRST_COMPLETED,
                    )
                    if invalidated in done:
                        return
                    event = pending.result()
                    if event.seq > cursor:
                        yield self._sse(event)
                        cursor = event.seq
                    if run_id is not None and event.kind in TERMINAL_EVENTS:
                        return
                    pending = asyncio.create_task(anext(stream))
            finally:
                pending.cancel()
                invalidated.cancel()
                await asyncio.gather(pending, invalidated, return_exceptions=True)

    @staticmethod
    def _sse(event: AgentEvent) -> dict[str, str]:
        return {"id": event.id, "event": event.kind, "data": event.model_dump_json()}
