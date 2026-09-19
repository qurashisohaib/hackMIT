"""In-process event bus feeding SSE streams and the agent trace.

Usage (from agents): `bus.publish(AgentEvent(...))` — synchronous, safe from the event loop
thread; from other threads it hops onto the loop. Subscribers get replay of history
(optionally filtered by run_id) followed by live events.
"""
from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from typing import AsyncIterator

from app.schemas import AgentEvent


class EventBus:
    def __init__(self) -> None:
        self._history: dict[str, list[AgentEvent]] = defaultdict(list)
        self._all: list[AgentEvent] = []
        self._subs: list[tuple[str | None, asyncio.Queue]] = []
        self._seq = 0
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # -- publish ---------------------------------------------------------
    def publish(self, event: AgentEvent) -> AgentEvent:
        with self._lock:
            self._seq += 1
            event.seq = self._seq
            self._all.append(event)
            if event.run_id:
                self._history[event.run_id].append(event)
            subs = list(self._subs)
        for run_filter, q in subs:
            if run_filter is None or run_filter == event.run_id:
                self._put(q, event)
        return event

    def _put(self, q: asyncio.Queue, event: AgentEvent) -> None:
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is not None and running is not loop:
            loop.call_soon_threadsafe(q.put_nowait, event)
        else:
            q.put_nowait(event)

    # -- read ------------------------------------------------------------
    def history(self, run_id: str | None = None, since_seq: int = 0, limit: int | None = None) -> list[AgentEvent]:
        with self._lock:
            src = self._history.get(run_id, []) if run_id else self._all
            out = [e for e in src if e.seq > since_seq]
        return out[-limit:] if limit else out

    def load_history(self, run_id: str, events: list[AgentEvent]) -> None:
        """Rehydrate a persisted trace (e.g. after restart)."""
        with self._lock:
            if run_id in self._history and self._history[run_id]:
                return
            self._history[run_id] = list(events)
            self._all.extend(events)
            self._all.sort(key=lambda e: e.seq)
            self._seq = max(self._seq, max((e.seq for e in events), default=0))

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
            self._all.clear()
            self._seq = 0

    # -- subscribe -------------------------------------------------------
    async def subscribe(self, run_id: str | None = None, replay: bool = True) -> AsyncIterator[AgentEvent]:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs.append((run_id, q))
            snapshot = list(self._history.get(run_id, [])) if run_id else list(self._all)
        try:
            seen = 0
            if replay:
                for e in snapshot:
                    seen = e.seq
                    yield e
            while True:
                e = await q.get()
                if e.seq <= seen:
                    continue
                yield e
        finally:
            with self._lock:
                self._subs = [(r, qq) for (r, qq) in self._subs if qq is not q]


bus = EventBus()


def emit(run_id: str | None, agent: str, kind: str, title: str, detail: str = "", **data) -> AgentEvent:
    """Convenience wrapper used by every agent."""
    return bus.publish(AgentEvent(run_id=run_id, agent=agent, kind=kind, title=title, detail=detail, data=data))
