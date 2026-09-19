import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api import routes_data, routes_exceptions, routes_memory, routes_runs, routes_views
from app.api.runs import RunManager
from app.config import settings
from app.data.seed import seed_database
from app.db.models import Period
from app.db.session import get_session, init_db
from app.events import bus
from app.memory import get_memory_service


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    with get_session() as session:
        empty = session.scalar(select(Period.id).limit(1)) is None
    if empty:
        seed_database(reset=False)
    bus.clear()
    bus.bind_loop(asyncio.get_running_loop())
    manager = RunManager(get_memory_service())
    manager.rehydrate()
    app.state.run_manager = manager
    try:
        yield
    finally:
        await manager.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="AI Office of the CFO", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in (
        routes_runs.router, routes_views.router, routes_exceptions.router,
        routes_memory.router, routes_data.router,
    ):
        app.include_router(router, prefix="/api")
    return app


app = create_app()
