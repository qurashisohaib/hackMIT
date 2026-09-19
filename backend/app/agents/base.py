"""Agent runtime core: `AgentContext`, the `Tool` registry and `BaseAgent`.

Every concrete agent (cfo / ap_ar / recon / audit / close / forecast / report) is built on
these three pieces:

* `AgentContext` carries everything a run shares — run/period ids, the MemoryService, the
  Brain, a session factory, live counters and the demo pacing delay — and is the single place
  events are emitted from.
* `ToolRegistry` holds plain Python functions as `Tool`s and can render them as
  OpenAI-function-style JSON schemas (used by `OpenAIBrain`).
* `BaseAgent` gives every agent traced tool calls (`tool.call` / `tool.result` events),
  hand-offs and a uniform `run()` entry point.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import types
import typing
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable, Iterator, TYPE_CHECKING

from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import new_session
from app.events import emit as _emit_event
from app.schemas import AgentEvent

if TYPE_CHECKING:  # pragma: no cover - typing only, instances are injected
    from app.agents.brain import Brain
    from app.memory.service import MemoryService

log = logging.getLogger(__name__)

#: Counter keys every run starts with (RunView.counts mirrors this dict).
COUNTER_KEYS: tuple[str, ...] = (
    "items",
    "reconciled",
    "matched",
    "exceptions",
    "auto_resolved",
    "pending_audit",
    "human",
    "precedent_hits",
    "audit_challenges",
    "audit_passed",
    "tool_calls",
    "hypotheses",
    "llm_tokens",
    "llm_cost_micro_usd",
    "llm_calls",
    "brain_fallbacks",
    "held",
    "escalated",
    "propagated",
)

SUMMARY_LIMIT = 300


class ToolError(RuntimeError):
    """Raised when a tool invocation fails; carries the tool name for the trace."""

    def __init__(self, tool: str, message: str) -> None:
        super().__init__(f"{tool}: {message}")
        self.tool = tool


# ----------------------------------------------------------------------------- helpers
def jsonable(value: Any) -> Any:
    """Recursively convert a value into JSON-serialisable primitives (dates, enums, models)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if hasattr(value, "__table__"):  # SQLAlchemy row
        return {c.name: jsonable(getattr(value, c.name)) for c in value.__table__.columns}
    return str(value)


def summarize(value: Any, limit: int = SUMMARY_LIMIT) -> str:
    """Compact JSON rendering of `value` truncated to `limit` characters (for event payloads)."""
    try:
        text = json.dumps(jsonable(value), default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# ----------------------------------------------------------------------------- context
@dataclass
class AgentContext:
    """Shared state for one run: ids, memory, brain, DB access, counters and pacing."""

    run_id: str | None
    period_id: str
    memory: "MemoryService | None" = None
    brain: "Brain | None" = None
    session_factory: Callable[[], Session] = new_session
    counters: dict[str, int] = field(default_factory=lambda: {k: 0 for k in COUNTER_KEYS})
    step_delay_ms: int = field(default_factory=lambda: settings.step_delay_ms)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in COUNTER_KEYS:
            self.counters.setdefault(key, 0)
        if self.brain is None:
            from app.agents.brain import get_brain  # local import: brain imports tools -> base

            self.brain = get_brain()

    # -- events ---------------------------------------------------------------
    def emit(self, agent: str, kind: str, title: str, detail: str = "", **data: Any) -> AgentEvent:
        """Publish an AgentEvent for this run (data is made JSON-safe first)."""
        return _emit_event(self.run_id, agent, kind, title, detail, **jsonable(data))

    async def pace(self) -> None:
        """Cinematic pacing between investigations; a no-op when step_delay_ms == 0."""
        if self.step_delay_ms > 0:
            await asyncio.sleep(self.step_delay_ms / 1000.0)

    # -- counters -------------------------------------------------------------
    def bump(self, key: str, n: int = 1) -> int:
        """Increment a live counter and return its new value."""
        self.counters[key] = self.counters.get(key, 0) + n
        return self.counters[key]

    # -- database -------------------------------------------------------------
    @contextmanager
    def session(self) -> Iterator[Session]:
        """Open a SQLAlchemy session that commits on success and rolls back on error."""
        s = self.session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    @property
    def brain_name(self) -> str:
        return getattr(self.brain, "name", "heuristic")


# ----------------------------------------------------------------------------- tools
_JSON_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    Any: "object",
}


def _annotation_schema(annotation: Any) -> dict[str, Any]:
    """Translate a Python type hint into a (loose) JSON schema fragment."""
    if annotation is inspect.Parameter.empty:
        return {}
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        schema = _annotation_schema(non_none[0]) if len(non_none) == 1 else {"anyOf": [_annotation_schema(a) for a in non_none]}
        if len(non_none) != len(args):
            schema = dict(schema)
            schema["nullable"] = True
        return schema
    if origin is typing.Literal:
        return {"type": "string", "enum": list(args)}
    if origin in (list, tuple, set):
        item = _annotation_schema(args[0]) if args else {}
        return {"type": "array", "items": item}
    if origin is dict:
        return {"type": "object"}
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return {"type": "string", "enum": [e.value for e in annotation]}
    if annotation in _JSON_TYPES:
        return {"type": _JSON_TYPES[annotation]}
    return {"type": "object"}


def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """Return (summary, {param: description}) from a Google-style docstring."""
    if not doc:
        return "", {}
    lines = [ln.rstrip() for ln in inspect.cleandoc(doc).splitlines()]
    summary_lines: list[str] = []
    params: dict[str, str] = {}
    section: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.endswith(":") and " " not in stripped:  # section header, e.g. "Args:" / "Returns:"
            section = stripped[:-1].lower()
            continue
        if section in {"args", "arguments", "parameters"}:
            if ":" in stripped:
                name, desc = stripped.split(":", 1)
                params[name.split("(")[0].strip()] = desc.strip()
            continue
        if section is not None:
            continue
        if not stripped and summary_lines:
            section = "body"  # end of summary paragraph
            continue
        if stripped:
            summary_lines.append(stripped)
    return " ".join(summary_lines), params


@dataclass(frozen=True)
class Tool:
    """A callable exposed to agents (and optionally to the LLM brain)."""

    name: str
    description: str
    fn: Callable[..., Any]
    requires_session: bool = True
    mutating: bool = False

    def __call__(self, session: Session | None = None, /, **kwargs: Any) -> Any:
        if self.requires_session:
            return self.fn(session, **kwargs)
        return self.fn(**kwargs)

    def parameters(self) -> list[inspect.Parameter]:
        """Signature parameters excluding the injected `session`."""
        return [p for p in inspect.signature(self.fn).parameters.values() if p.name != "session"]

    def spec(self) -> dict[str, Any]:
        """OpenAI-function-style JSON schema derived from type hints + docstring."""
        try:
            hints = typing.get_type_hints(self.fn)
        except Exception:  # pragma: no cover - exotic annotations
            hints = {}
        summary, param_docs = _parse_docstring(self.fn.__doc__)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in self.parameters():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            schema = _annotation_schema(hints.get(p.name, p.annotation))
            if p.name in param_docs:
                schema["description"] = param_docs[p.name]
            if p.default is not inspect.Parameter.empty:
                schema["default"] = jsonable(p.default)
            else:
                required.append(p.name)
            properties[p.name] = schema
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description or summary,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }


class ToolRegistry:
    """Registry of `Tool`s keyed by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        requires_session: bool = True,
        mutating: bool = False,
        description: str | None = None,
    ) -> Any:
        """Decorator (with or without arguments) that registers a function as a tool."""

        def _wrap(func: Callable[..., Any]) -> Callable[..., Any]:
            summary, _ = _parse_docstring(func.__doc__)
            tool = Tool(
                name=name or func.__name__,
                description=description or summary,
                fn=func,
                requires_session=requires_session,
                mutating=mutating,
            )
            self._tools[tool.name] = tool
            return func

        if fn is not None:
            return _wrap(fn)
        return _wrap

    def get(self, name: str) -> Tool:
        """Look up a tool; raises `ToolError` for unknown names."""
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(name, "unknown tool") from None

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def tools(self, *, include_mutating: bool = True) -> list[Tool]:
        """All registered tools (optionally only the read/compute ones)."""
        return [t for t in self._tools.values() if include_mutating or not t.mutating]

    def list_specs(self, *, include_mutating: bool = False) -> list[dict[str, Any]]:
        """OpenAI-function-style schemas for the registered tools."""
        return [t.spec() for t in self.tools(include_mutating=include_mutating)]

    def call(self, name: str, session: Session | None = None, /, **kwargs: Any) -> Any:
        """Invoke a tool by name (session is passed only when the tool requires one)."""
        return self.get(name)(session, **kwargs)


# ----------------------------------------------------------------------------- base agent
class BaseAgent(ABC):
    """Common behaviour for every agent: traced tool calls, hand-offs, events."""

    name: str = "agent"
    display_name: str = "Agent"

    def __init__(self, ctx: AgentContext, registry: ToolRegistry | None = None) -> None:
        self.ctx = ctx
        self.steps = 0
        if registry is None:
            from app.agents.tools import registry as default_registry

            registry = default_registry
        self.registry = registry

    # -- events ---------------------------------------------------------------
    def emit(self, kind: str, title: str, detail: str = "", **data: Any) -> AgentEvent:
        """Emit an event attributed to this agent."""
        return self.ctx.emit(self.name, kind, title, detail, **data)

    def started(self, detail: str = "", **data: Any) -> AgentEvent:
        """Emit `agent.started`."""
        return self.emit("agent.started", f"{self.display_name} started", detail, **data)

    def finished(self, detail: str = "", **data: Any) -> AgentEvent:
        """Emit `agent.finished` with the number of steps taken."""
        return self.emit("agent.finished", f"{self.display_name} finished", detail, steps=self.steps, **data)

    # -- tools ----------------------------------------------------------------
    def call_tool(self, name: str, **kwargs: Any) -> Any:
        """Run a registered tool with tracing; returns the full result (event holds a summary).

        Raises `ToolError` when the tool fails; the `tool.result` event still records the error.
        """
        tool = self.registry.get(name)
        self.steps += 1
        self.ctx.bump("tool_calls")
        self.emit("tool.call", f"{name}", summarize(kwargs, 200), tool=name, args=jsonable(kwargs))
        try:
            if tool.requires_session:
                with self.ctx.session() as s:
                    result = tool.fn(s, **kwargs)
                    result = jsonable(result)
            else:
                result = jsonable(tool.fn(**kwargs))
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced through the trace and re-raised typed
            log.warning("tool %s failed: %s", name, exc, exc_info=True)
            self.emit("tool.result", f"{name} failed", str(exc)[:SUMMARY_LIMIT], tool=name, ok=False, error=str(exc))
            raise ToolError(name, str(exc)) from exc
        self.emit("tool.result", f"{name} → {summarize(result, 80)}", summarize(result), tool=name, ok=True)
        return result

    def try_tool(self, name: str, default: Any = None, **kwargs: Any) -> Any:
        """Like `call_tool` but returns `default` instead of raising."""
        try:
            return self.call_tool(name, **kwargs)
        except ToolError:
            return default

    # -- hand-off ---------------------------------------------------------------
    def handoff(self, to_agent_name: str, reason: str, payload: dict[str, Any] | None = None) -> AgentEvent:
        """Emit a `handoff` event to another agent."""
        return self.emit(
            "handoff",
            f"{self.display_name} → {to_agent_name}",
            reason,
            **{"from": self.name, "to": to_agent_name, "reason": reason, "payload": jsonable(payload or {})},
        )

    # -- entry point ------------------------------------------------------------
    @abstractmethod
    async def run(self, **kwargs: Any) -> Any:
        """Execute the agent's job for the run in `self.ctx`."""
