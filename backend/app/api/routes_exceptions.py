from fastapi import APIRouter, HTTPException

from app.api.dependencies import Manager
from app.api.pipeline import get_cfo
from app.api.routes_data import entity_row, related_rows
from app.events import bus
from app.schemas import ExceptionDetail, ExceptionView, ResolveRequest, ResolveResponse

router = APIRouter(prefix="/exceptions")


@router.get("", response_model=list[ExceptionView])
async def exceptions(
    manager: Manager, period_id: str | None = None, status: str | None = None,
) -> list[ExceptionView]:
    if period_id is not None:
        manager.require_period(period_id)
    return [
        ExceptionView.model_validate({**record, "ground_truth_type": None})
        for record in manager.memory.exceptions(period_id=period_id, status=status)
        if status == "superseded" or record.get("status") != "superseded"
    ]


@router.get("/{exception_id}", response_model=ExceptionDetail)
async def exception_detail(exception_id: str, manager: Manager) -> ExceptionDetail:
    try:
        detail = ExceptionDetail.model_validate(manager.memory.exception_detail(exception_id))
    except KeyError as exc:
        raise HTTPException(404, f"Unknown exception: {exception_id}") from exc
    record = detail.exception
    record.ground_truth_type = None
    detail.entity = entity_row(record.entity_type, record.entity_id)
    candidates = {candidate for hypothesis in detail.hypotheses for candidate in hypothesis.candidate_ids}
    if detail.decision is not None:
        candidates.update(detail.decision.matched_ids)
    candidates.discard(record.entity_id)
    detail.related = related_rows(candidates)
    relevant_ids = {exception_id, record.entity_id, *(hypothesis.id for hypothesis in detail.hypotheses)}
    if detail.decision is not None:
        relevant_ids.add(detail.decision.id)
    detail.trace = [
        event for event in bus.history(record.run_id)
        if any(event.data.get(key) in relevant_ids for key in (
            "exception_id", "entity_id", "hypothesis_id", "decision_id", "bank_txn_id",
        ) if isinstance(event.data.get(key), str))
    ]
    return detail


def validate_resolution(request: ResolveRequest, detail: ExceptionDetail) -> None:
    if detail.exception.status in {"resolved", "dismissed", "superseded"}:
        raise HTTPException(409, f"Exception is already {detail.exception.status}")
    if request.action == "approve_hypothesis":
        if not request.hypothesis_id:
            raise HTTPException(422, "approve_hypothesis requires hypothesis_id")
        if request.hypothesis_id not in {hypothesis.id for hypothesis in detail.hypotheses}:
            raise HTTPException(422, "The hypothesis does not belong to this exception")
    if request.action == "teach" and request.rule is None and not request.explanation.strip():
        raise HTTPException(422, "teach requires a rule or an explanation")
    if request.action == "manual_match":
        if not request.matched_ids or any(not entity_id.strip() for entity_id in request.matched_ids):
            raise HTTPException(422, "manual_match requires matched_ids")
        found = {row["id"] for row in related_rows(set(request.matched_ids))}
        if found != set(request.matched_ids):
            raise HTTPException(422, "One or more matched_ids do not exist")


@router.post("/{exception_id}/resolve", response_model=ResolveResponse)
async def resolve(
    exception_id: str, request: ResolveRequest, manager: Manager,
) -> ResolveResponse:
    with manager.mutation("a human correction is being applied"):
        detail = await exception_detail(exception_id, manager)
        validate_resolution(request, detail)
        try:
            response = await get_cfo().resolve_exception(exception_id, request)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        manager.refresh_persisted_runs()
        return response
