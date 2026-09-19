from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import Manager
from app.schemas import GraphSubgraph, RuleView

router = APIRouter(prefix="/memory")


@router.get("/graph")
async def graph(
    manager: Manager, labels: str | None = None, period_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 600, focus: str | None = None,
) -> dict:
    if period_id is not None:
        manager.require_period(period_id)
    if focus is not None and manager.memory.store.get_node(focus) is None:
        raise HTTPException(404, f"Unknown memory node: {focus}")
    selected = [label.strip() for label in labels.split(",") if label.strip()] if labels else None
    return manager.memory.store.export_cytoscape(
        labels=selected, period_id=period_id, limit=limit, focus=focus,
    )


@router.get("/nodes/{node_id}")
async def node_detail(node_id: str, manager: Manager) -> dict:
    store = manager.memory.store
    node = store.get_node(node_id)
    if node is None:
        raise HTTPException(404, f"Unknown memory node: {node_id}")
    return {
        "node": node.model_dump(mode="json"),
        "edges": [edge.model_dump(mode="json") for edge in store.edges_of(node_id)],
        "neighbors": [neighbor.model_dump(mode="json") for neighbor in store.neighbors(node_id, direction="both")],
    }


@router.get("/provenance/{node_id}", response_model=GraphSubgraph)
async def provenance(node_id: str, manager: Manager) -> GraphSubgraph:
    if manager.memory.store.get_node(node_id) is None:
        raise HTTPException(404, f"Unknown memory node: {node_id}")
    return GraphSubgraph.model_validate(manager.memory.provenance(node_id))


@router.get("/rules", response_model=list[RuleView])
async def rules(manager: Manager) -> list[RuleView]:
    return [RuleView.model_validate(rule) for rule in manager.memory.rules()]


@router.get("/stats")
async def stats(manager: Manager) -> dict:
    return manager.memory.store.stats()
