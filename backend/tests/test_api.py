from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from importlib.util import find_spec
from pathlib import Path
from typing import cast
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from app.api.runs import RunManager
from app.config import settings
from app.db import session as db_session
from app.db.models import BankTransaction, Period
from app.events import bus, emit
from app.main import create_app
from app.memory import service as memory_service
from app.schemas import (
    AgentEvent,
    CloseReport,
    ExceptionDetail,
    ExceptionRecord,
    ExceptionStatus,
    ForecastView,
    Hypothesis,
    MetricsResponse,
    PatternType,
    PeriodView,
    RuleSpec,
    RunMetrics,
    RunSummary,
    RunView,
    ScopeType,
    utcnow,
)


@dataclass
class API:
    app: FastAPI
    client: httpx.AsyncClient
    manager: RunManager


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[API]:
    db_session.reset_engine()
    with (
        patch.object(settings, "db_path", str(tmp_path / "api.sqlite")),
        patch.object(settings, "step_delay_ms", 0),
        patch.object(settings, "openai_api_key", None),
        patch.object(settings, "llm_enabled", False),
        patch.object(settings, "neo4j_uri", None),
        patch.object(memory_service, "_service", None),
    ):
        app = create_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
            ) as client:
                yield API(app, client, cast(RunManager, app.state.run_manager))
        bus.clear()
        db_session.reset_engine()


def add_exception(api: API, status: ExceptionStatus = ExceptionStatus.NEEDS_HUMAN) -> ExceptionRecord:
    with db_session.get_session() as session:
        transaction = session.scalars(
            select(BankTransaction).where(BankTransaction.period_id == "2026-01"),
        ).first()
        assert transaction is not None
        record = ExceptionRecord(
            period_id="2026-01", run_id="RUN-EXAMPLE", entity_type="bank_transaction",
            entity_id=transaction.id, title="Review bank transaction", status=status,
            amount=transaction.amount,
        )
    api.manager.memory.record_exception(record)
    return record


def add_completed_run(
    api: API, run_id: str = "RUN-SAVED", period_id: str = "2026-01",
    offset: int = 0, accuracy: float = 0.9, reviews: int = 4,
) -> RunSummary:
    timestamp = utcnow() + timedelta(seconds=offset)
    metrics = RunMetrics(
        run_id=run_id, period_id=period_id, total_items=200, auto_resolved=180,
        human_reviews=reviews, accuracy=accuracy, coverage=0.9,
    )
    run = RunSummary(
        run_id=run_id, period_id=period_id, status="completed", metrics=metrics,
        started_at=timestamp, finished_at=timestamp,
    )
    emit(run_id, "cfo", "run.started", "Run started")
    emit(run_id, "recon", "agent.step", "Matching invoices")
    emit(run_id, "cfo", "run.completed", "Run completed", metrics=metrics.model_dump())
    api.manager.memory.record_agent_run(run)
    api.manager.memory.store.update_node(
        run_id, {"events": [event.model_dump(mode="json") for event in bus.history(run_id)]},
    )
    api.manager.rehydrate()
    return run


async def test_health_periods_and_cors(api: API) -> None:
    response = await api.client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "brain": "heuristic", "llm_available": False,
        "company": settings.company_name, "version": "0.1.0",
    }
    periods = [PeriodView.model_validate(row) for row in (await api.client.get("/api/periods")).json()]
    assert [period.id for period in periods] == ["2026-01", "2026-02", "2026-03"]
    for period in periods:
        assert period.stats["bank_transactions"] > 0
        assert period.stats["unreconciled"] == period.stats["bank_transactions"]
        assert period.last_run_id is None
    cors = await api.client.options(
        "/api/runs", headers={"Origin": "http://frontend", "Access-Control-Request-Method": "POST"},
    )
    assert cors.status_code == 200
    assert cors.headers["access-control-allow-origin"] == "*"


async def test_startup_preserves_existing_data_and_rehydrates(api: API) -> None:
    saved = add_completed_run(api)
    with db_session.get_session() as session:
        period = session.get(Period, "2026-01")
        assert period is not None
        period.name = "Preserved period"
    replacement = create_app()
    async with replacement.router.lifespan_context(replacement):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=replacement), base_url="http://test",
        ) as client:
            periods = (await client.get("/api/periods")).json()
            assert periods[0]["name"] == "Preserved period"
            view = RunView.model_validate((await client.get(f"/api/runs/{saved.run_id}")).json())
            assert view.status == "completed"
            assert len((await client.get(f"/api/runs/{saved.run_id}/trace")).json()) == 3


@pytest.mark.parametrize("table", [
    "bank_transactions", "ap_invoices", "ar_invoices", "purchase_orders", "payments", "ledger_entries",
])
async def test_allowlisted_data_is_period_scoped(api: API, table: str) -> None:
    response = await api.client.get(f"/api/data/{table}", params={"period_id": "2026-02"})
    assert response.status_code == 200
    rows = response.json()
    assert rows
    assert all(row["period_id"] == "2026-02" for row in rows)
    assert all("exception_type" not in row for row in rows)


async def test_truth_requires_explicit_reveal_and_no_arbitrary_tables(api: API) -> None:
    assert (await api.client.get("/api/data/ground_truth")).status_code == 403
    assert (await api.client.get("/api/data/ground_truth?reveal=0")).status_code == 403
    assert (await api.client.get("/api/data/graph_nodes?reveal=1")).status_code == 404
    assert (await api.client.get("/api/data/vendors")).status_code == 404
    response = await api.client.get("/api/data/ground_truth?reveal=1&period_id=2026-01")
    assert response.status_code == 200
    assert all(row["period_id"] == "2026-01" for row in response.json())
    assert "exception_type" in response.json()[0]
    assert (await api.client.get("/api/data/bank_transactions?period_id=missing")).status_code == 404


async def test_exception_detail_populates_entity_candidates_trace(api: API) -> None:
    record = add_exception(api)
    invoices = (await api.client.get("/api/data/ap_invoices?period_id=2026-01")).json()
    hypothesis = Hypothesis(kind="observed_pattern", description="Evidence", candidate_ids=[invoices[0]["id"]])
    api.manager.memory.record_hypothesis(record.id, hypothesis)
    api.manager.memory.store.update_node(record.id, {"ground_truth_type": "must not leak"})
    relevant = emit(record.run_id, "recon", "hypothesis.generated", "Candidate",
                    exception_id=record.id, hypothesis_id=hypothesis.id)
    emit(record.run_id, "recon", "agent.step", "Unrelated")
    response = await api.client.get(f"/api/exceptions/{record.id}")
    assert response.status_code == 200
    detail = ExceptionDetail.model_validate(response.json())
    assert detail.entity["id"] == record.entity_id
    assert [row["id"] for row in detail.related] == [invoices[0]["id"]]
    assert [event.id for event in detail.trace] == [relevant.id]
    assert detail.exception.ground_truth_type is None
    listed = (await api.client.get("/api/exceptions?period_id=2026-01&status=needs_human")).json()
    assert [row["id"] for row in listed] == [record.id]
    assert listed[0]["ground_truth_type"] is None
    api.manager.memory.store.update_node(record.id, {"status": "superseded"})
    assert (await api.client.get("/api/exceptions")).json() == []


@pytest.mark.parametrize("payload", [
    {"action": "unknown"},
    {"action": "approve_hypothesis"},
    {"action": "approve_hypothesis", "hypothesis_id": "OTHER-HYPOTHESIS"},
    {"action": "teach"},
    {"action": "manual_match"},
    {"action": "manual_match", "matched_ids": ["MISSING"]},
])
async def test_invalid_resolutions_are_4xx_without_mutations(api: API, payload: dict) -> None:
    record = add_exception(api)
    before = api.manager.memory.store.stats()
    response = await api.client.post(f"/api/exceptions/{record.id}/resolve", json=payload)
    assert response.status_code == 422
    assert api.manager.memory.store.stats() == before


async def test_unknown_resources_and_resolved_exception_conflicts(api: API) -> None:
    for path in (
        "/api/runs/missing", "/api/runs/missing/trace", "/api/runs/missing/events",
        "/api/exceptions/missing", "/api/memory/nodes/missing", "/api/memory/provenance/missing",
        "/api/forecast?period_id=missing", "/api/reports/missing",
    ):
        assert (await api.client.get(path)).status_code == 404
    assert (await api.client.post("/api/runs", json={"period_id": "missing"})).status_code == 404
    assert (await api.client.post("/api/runs", json={})).status_code == 422
    assert (await api.client.post("/api/runs", json={"period_id": "2026-04"})).status_code == 409
    record = add_exception(api, ExceptionStatus.RESOLVED)
    response = await api.client.post(f"/api/exceptions/{record.id}/resolve", json={"action": "dismiss"})
    assert response.status_code == 409


async def test_mutations_conflict_while_writer_active(api: API) -> None:
    record = add_exception(api)
    with api.manager.mutation("run RUN-BUSY is active"):
        for path, payload in (
            ("/api/runs", {"period_id": "2026-01"}),
            ("/api/reset", {}),
            (f"/api/exceptions/{record.id}/resolve", {"action": "dismiss"}),
        ):
            response = await api.client.post(path, json=payload)
            assert response.status_code == 409
            assert "RUN-BUSY" in response.json()["detail"]
        assert (await api.client.get("/api/forecast?period_id=2026-01")).status_code == 409
        assert (await api.client.get("/api/reports/2026-01")).status_code == 409
        assert (await api.client.get("/api/periods")).status_code == 200


async def test_background_failure_persisted_and_slot_released(api: API) -> None:
    with patch("app.api.runs.get_cfo", side_effect=RuntimeError("Pipeline unavailable")):
        response = await api.client.post("/api/runs", json={"period_id": "2026-01"})
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        with pytest.raises(HTTPException) as error:
            api.manager.start("2026-02")
        assert isinstance(error.value, HTTPException)
        assert error.value.status_code == 409
        assert api.manager._task is not None
        await api.manager._task
    view = RunView.model_validate((await api.client.get(f"/api/runs/{run_id}")).json())
    assert view.status == "failed"
    assert view.error == "Pipeline unavailable"
    assert view.finished_at is not None
    trace = (await api.client.get(f"/api/runs/{run_id}/trace")).json()
    assert trace[-1]["kind"] == "run.failed"
    node = api.manager.memory.store.get_node(run_id)
    assert node is not None and node.props["events"] == trace
    bus.clear()
    api.manager.rehydrate()
    assert (await api.client.get(f"/api/runs/{run_id}/trace")).json() == trace
    with api.manager.mutation("a new operation"):
        pass


async def test_shutdown_marks_queued_run_failed(api: API) -> None:
    run_id = api.manager.start("2026-01")
    await api.manager.shutdown()
    assert api.manager.get(run_id).status == "failed"
    assert bus.history(run_id)[-1].kind == "run.failed"
    assert (await api.client.post("/api/runs", json={"period_id": "2026-02"})).status_code == 503


async def test_restart_recovers_interrupted_run_and_live_counts(api: API) -> None:
    record = add_exception(api)
    run = RunSummary(run_id=record.run_id or "", period_id="2026-01", status="running", started_at=utcnow())
    api.manager.memory.record_agent_run(run)
    with db_session.get_session() as session:
        transaction = session.get(BankTransaction, record.entity_id)
        assert transaction is not None
        transaction.reconciled = True
        period = session.get(Period, "2026-01")
        assert period is not None
        period.status = "in_progress"
    api.manager.rehydrate()
    view = api.manager.get(run.run_id)
    assert view.status == "failed"
    assert "restarted" in (view.error or "")
    assert view.counts["reconciled"] == 1
    assert view.counts["human"] == 1
    assert view.counts["exceptions"] == 1
    assert bus.history(run.run_id)[-1].kind == "run.failed"
    with db_session.get_session() as session:
        period = session.get(Period, "2026-01")
        assert period is not None and period.status == "pending_review"


async def test_metrics_latest_periods_deltas_and_full_history(api: API) -> None:
    add_completed_run(api, "RUN-JAN-OLD", accuracy=0.8, reviews=10)
    add_completed_run(api, "RUN-JAN-NEW", offset=1, accuracy=0.9, reviews=7)
    add_completed_run(api, "RUN-FEB", "2026-02", offset=2, accuracy=0.95, reviews=3)
    response = MetricsResponse.model_validate((await api.client.get("/api/metrics")).json())
    assert [row.run_id for row in response.periods] == ["RUN-JAN-NEW", "RUN-FEB"]
    assert [row.run_id for row in response.history] == ["RUN-JAN-OLD", "RUN-JAN-NEW", "RUN-FEB"]
    assert response.deltas["2026-02"]["previous_period_id"] == "2026-01"
    assert response.deltas["2026-02"]["accuracy"] == pytest.approx(0.05)
    assert response.deltas["2026-02"]["human_reviews"] == -4


async def test_completed_sse_replay_resume_and_end(api: API) -> None:
    run = add_completed_run(api)
    trace = bus.history(run.run_id)
    response = await api.client.get(f"/api/runs/{run.run_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: run.started" in response.text
    assert "event: run.completed" in response.text
    events = [AgentEvent.model_validate_json(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [event.id for event in events] == [event.id for event in trace]
    resumed = await api.client.get(
        f"/api/runs/{run.run_id}/events", headers={"Last-Event-ID": trace[-2].id},
    )
    assert "run.started" not in resumed.text and "event: run.completed" in resumed.text
    done = await api.client.get(
        f"/api/runs/{run.run_id}/events", headers={"Last-Event-ID": str(trace[-1].seq)},
    )
    assert done.text == ""


async def test_event_stream_replays_then_receives_live_and_cleans_up(api: API) -> None:
    first = emit("RUN-LIVE", "cfo", "run.started", "Start")
    stream = api.manager.events(None, None, api.manager.stream_epoch)
    assert (await anext(stream))["id"] == first.id
    second = emit("RUN-LIVE", "recon", "agent.step", "Live")
    assert (await asyncio.wait_for(anext(stream), timeout=1))["id"] == second.id
    pending = asyncio.create_task(anext(stream))
    api.manager.stream_epoch.set()
    with pytest.raises(StopAsyncIteration):
        await pending
    assert bus._subs == []


async def test_live_run_stream_ends_on_completion(api: API) -> None:
    run = RunSummary(run_id="RUN-LIVE", period_id="2026-01", status="running")
    api.manager._runs[run.run_id] = run
    first = emit(run.run_id, "cfo", "run.started", "Start")
    stream = api.manager.events(run.run_id, None, api.manager.stream_epoch)
    assert (await anext(stream))["id"] == first.id
    emit(run.run_id, "cfo", "run.completed", "Complete")
    assert json.loads((await anext(stream))["data"])["kind"] == "run.completed"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert bus._subs == []
    run.status = "completed"


async def test_memory_graph_rules_nodes_and_provenance(api: API) -> None:
    record = add_exception(api)
    rule_id = api.manager.memory.learn_rule(
        RuleSpec(
            pattern_type=PatternType.PERCENTAGE_FEE, scope_type=ScopeType.GLOBAL,
            params={"rate": 0.03}, description="Three percent processing fee",
        ),
        source=record.id, period_id="2026-01", human_verified=True,
    )
    rules = (await api.client.get("/api/memory/rules")).json()
    assert rules[0]["id"] == rule_id and rules[0]["trust"]["human_verified"]
    graph = (await api.client.get("/api/memory/graph?labels=Rule&limit=10")).json()
    assert graph["nodes"] and len(graph["nodes"]) <= 10
    node = (await api.client.get(f"/api/memory/nodes/{rule_id}")).json()
    assert node["node"]["id"] == rule_id and node["edges"] and node["neighbors"]
    provenance = (await api.client.get(f"/api/memory/provenance/{rule_id}")).json()
    assert provenance["root"] == rule_id
    assert (await api.client.get("/api/memory/stats")).json()["by_label"]["Rule"] == 1
    assert (await api.client.get("/api/memory/graph?limit=-1")).status_code == 422


async def test_forecast_and_report_read_observations_without_creating_runs(api: API) -> None:
    forecast = ForecastView(
        period_id="2026-01", as_of="2026-01-31", opening_cash=100, weeks=[], assumptions=[],
    )
    report = CloseReport(
        period_id="2026-01", run_id=None, status="open", summary="Awaiting close",
        sections=[], checklist=[], generated_at=utcnow().isoformat(),
    )
    api.manager.memory.record_observation("period", "2026-01", "forecast", forecast.model_dump(), "2026-01")
    api.manager.memory.record_observation("period", "2026-01", "close_report", report.model_dump(), "2026-01")
    with api.manager.mutation("a run is active"):
        assert (await api.client.get("/api/forecast?period_id=2026-01")).json() == forecast.model_dump()
        assert (await api.client.get("/api/reports/2026-01")).json() == report.model_dump()
    assert (await api.client.get("/api/runs")).json() == []
    assert api.manager.memory.store.find_nodes("AgentRun") == []


async def test_reset_clears_history_memory_and_reseeds(api: API) -> None:
    add_completed_run(api)
    record = add_exception(api)
    before = (await api.client.get("/api/data/bank_transactions?period_id=2026-01")).json()
    with db_session.get_session() as session:
        transaction = session.get(BankTransaction, record.entity_id)
        assert transaction is not None
        transaction.reconciled = True
    old_epoch = api.manager.stream_epoch
    response = await api.client.post("/api/reset")
    assert response.status_code == 200
    assert old_epoch.is_set()
    assert (await api.client.get("/api/runs")).json() == []
    assert (await api.client.get("/api/exceptions")).json() == []
    assert (await api.client.get("/api/metrics")).json() == {"periods": [], "deltas": {}, "history": []}
    assert (await api.client.get("/api/memory/stats")).json()["nodes"] == 0
    assert bus.history() == []
    assert (await api.client.get("/api/data/bank_transactions?period_id=2026-01")).json() == before


@pytest.mark.skipif(find_spec("app.agents.cfo") is None, reason="CFO branch must be integrated first")
async def test_actual_pipeline_api_close_teach_and_views(api: API) -> None:
    response = await api.client.post("/api/runs", json={"period_id": "2026-01"})
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    async with asyncio.timeout(90):
        while True:
            run = RunView.model_validate((await api.client.get(f"/api/runs/{run_id}")).json())
            if run.status in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
    assert run.status == "completed", run.error
    assert run.metrics is not None and run.metrics.human_reviews >= 8
    trace = (await api.client.get(f"/api/runs/{run_id}/trace")).json()
    assert trace[-1]["kind"] == "run.completed"
    queue = (await api.client.get("/api/exceptions?period_id=2026-01&status=needs_human")).json()
    stripe = next(item for item in queue if "stripe" in str(item["counterparty_name"]).lower())
    resolution = await api.client.post(
        f"/api/exceptions/{stripe['id']}/resolve",
        json={"action": "teach", "explanation": "Stripe deducts a 3% processing fee"},
    )
    assert resolution.status_code == 200, resolution.text
    assert resolution.json()["rule_id"] and resolution.json()["propagated"]
    forecast = await api.client.get("/api/forecast?period_id=2026-01")
    assert forecast.status_code == 200
    assert len(ForecastView.model_validate(forecast.json()).weeks) == 13
    report = await api.client.get("/api/reports/2026-01")
    assert report.status_code == 200
    CloseReport.model_validate(report.json())
    assert len((await api.client.get("/api/runs")).json()) == 1
