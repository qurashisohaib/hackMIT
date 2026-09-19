from typing import Annotated

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from app.api.dependencies import Manager
from app.events import bus
from app.schemas import AgentEvent, RunView

router = APIRouter()


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period_id: str = Field(min_length=1)


@router.post("/runs", status_code=202)
async def create_run(request: CreateRunRequest, manager: Manager) -> dict[str, str]:
    return {"run_id": manager.start(request.period_id)}


@router.get("/runs", response_model=list[RunView])
async def list_runs(manager: Manager) -> list[RunView]:
    return manager.list()


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(run_id: str, manager: Manager) -> RunView:
    return manager.get(run_id)


@router.get("/runs/{run_id}/trace", response_model=list[AgentEvent])
async def get_trace(run_id: str, manager: Manager) -> list[AgentEvent]:
    manager.get(run_id)
    return bus.history(run_id)


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str, manager: Manager,
    last_event_id: Annotated[str | None, Header()] = None,
) -> EventSourceResponse:
    manager.get(run_id)
    return EventSourceResponse(
        manager.events(run_id, last_event_id, manager.stream_epoch),
        ping=15, send_timeout=30, headers={"X-Accel-Buffering": "no"},
    )


@router.get("/events/stream")
async def all_events(
    manager: Manager, last_event_id: Annotated[str | None, Header()] = None,
) -> EventSourceResponse:
    return EventSourceResponse(
        manager.events(None, last_event_id, manager.stream_epoch),
        ping=15, send_timeout=30, headers={"X-Accel-Buffering": "no"},
    )
