"""Store-agnostic helpers shared by GraphStore and Neo4jStore.

Everything here operates on the pydantic graph primitives from ``app.schemas`` so both
backends produce byte-identical provenance chains and Cytoscape exports.
"""
from __future__ import annotations

import json
from collections import deque
from datetime import date, datetime
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel

from app.schemas import GraphEdge, GraphNode, GraphSubgraph, new_id

# ---------------------------------------------------------------- constants
NODE_PREFIXES: dict[str, str] = {
    "Rule": "RULE",
    "Exception": "EXC",
    "Hypothesis": "HYP",
    "Decision": "DEC",
    "HumanCorrection": "HC",
    "AuditFinding": "AUD",
    "AgentRun": "RUN",
    "Observation": "OBS",
    "Vendor": "V",
    "Customer": "C",
    "Employee": "E",
    "Account": "ACCT",
    "Period": "PER",
    "Transaction": "BT",
    "Invoice": "INV",
    "PurchaseOrder": "PO",
    "Payment": "PAY",
}

#: Layer shown by default on /memory (ARCHITECTURE §3 + task brief).
KNOWLEDGE_LABELS: tuple[str, ...] = (
    "Rule", "HumanCorrection", "Exception", "Decision", "AuditFinding",
    "Vendor", "Customer", "Period", "Observation",
)
#: Financial-truth mirrors that only appear when attached to an Exception/Decision.
ATTACHED_LABELS: frozenset[str] = frozenset({"Transaction", "Invoice"})
ATTACHMENT_ANCHORS: frozenset[str] = frozenset({"Exception", "Decision"})
#: Context nodes included one hop away in provenance but never expanded.
CONTEXT_LABELS: frozenset[str] = frozenset({"Period", "Vendor", "Customer"})
#: Labels that have no period of their own but are kept in a period-filtered export
#: when adjacent to a kept node (a January rule used by a February decision).
PERIODLESS_LABELS: frozenset[str] = frozenset({"Vendor", "Customer", "Rule", "Period", "Account", "Employee"})

#: Out-edges followed backwards from a node (ARCHITECTURE §3, plus TESTED so a Decision
#: reaches the Hypothesis it executed).
PROVENANCE_OUT_RELS: tuple[str, ...] = (
    "USED_PRECEDENT", "DERIVED_FROM", "LEARNED_FROM", "CORRECTED_BY", "RESOLVED_BY", "CAUSED_BY",
    "VERIFIED_BY", "SUPERSEDES", "ABOUT", "APPLIES_TO", "PROPOSED_BY", "TESTED",
)
#: In-edges whose semantic points at the node (Exception -RESOLVED_BY-> Decision means the
#: decision's provenance includes the exception).
PROVENANCE_IN_RELS: tuple[str, ...] = ("RESOLVED_BY", "CORRECTED_BY", "VERIFIED_BY", "CHALLENGED_BY")

#: Props copied verbatim into Cytoscape node data (embedding / events are never exported).
CYTOSCAPE_PROPS: tuple[str, ...] = (
    "created_at", "description", "title", "category", "kind", "action", "verdict", "tier",
    "pattern_type", "scope_type", "scope_id", "scope_name", "params", "version", "trust",
    "trust_score", "learned_in_period", "created_by", "entity_type", "entity_id",
    "counterparty_type", "counterparty_id", "counterparty_name", "amount", "expected_amount",
    "difference", "exception_id", "decision_id", "hypothesis_id", "rule_id", "run_id", "agent",
    "key", "value", "generated_by", "passed", "tested", "matched_ids", "by", "explanation",
)

# ---------------------------------------------------------------- serialisation


def jsonable(value: Any) -> Any:
    """Convert ``value`` into a JSON-serialisable structure.

    datetimes/dates become ISO strings, enums their value, pydantic models ``model_dump(mode="json")``,
    sets/tuples lists, and mappings are converted recursively with string keys.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return jsonable(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if hasattr(value, "tolist"):  # numpy arrays / scalars
        return jsonable(value.tolist())
    return str(value)


def jsonable_props(props: dict[str, Any] | None) -> dict[str, Any]:
    """Return a JSON-safe copy of ``props`` (never mutates the input)."""
    return jsonable(dict(props or {}))


def canonical_json(value: Any) -> str:
    """Stable JSON text used to compare parameter dicts for equality."""
    return json.dumps(jsonable(value), sort_keys=True, separators=(",", ":"))


def node_prefix(label: str) -> str:
    """Id prefix used by ``new_id`` for a node label."""
    return NODE_PREFIXES.get(label, label.upper())


def generate_node_id(label: str) -> str:
    """Generate a fresh node id for ``label`` (``RULE-…``, ``EXC-…``)."""
    return new_id(node_prefix(label))


def display_name(node_id: str, props: dict[str, Any]) -> str:
    """Derive the ``name`` display prop: name → title → description → key → id."""
    for key in ("name", "title", "description", "key"):
        val = props.get(key)
        if isinstance(val, str) and val.strip():
            text = val.strip()
            return text if len(text) <= 80 else text[:77] + "..."
    return node_id


def period_of(node: GraphNode) -> str | None:
    """Period a node belongs to: ``period_id`` prop, ``learned_in_period`` for rules, or its own id."""
    props = node.props
    pid = props.get("period_id") or props.get("learned_in_period")
    if pid:
        return str(pid)
    if node.label == "Period":
        return node.id
    return None


def created_at_of(node: GraphNode) -> str:
    """ISO created_at of a node (empty string when unknown, sorting oldest)."""
    return str(node.props.get("created_at") or "")


# ---------------------------------------------------------------- traversal


class GraphView(Protocol):
    """Minimal read interface the traversal algorithms need."""

    def node(self, node_id: str) -> GraphNode | None: ...

    def out_edges(self, node_id: str) -> list[GraphEdge]: ...

    def in_edges(self, node_id: str) -> list[GraphEdge]: ...


def provenance_bfs(view: GraphView, root: str, max_depth: int = 8) -> GraphSubgraph:
    """Breadth-first provenance walk backwards from ``root``.

    Follows out-edges in ``PROVENANCE_OUT_RELS`` and in-edges in ``PROVENANCE_IN_RELS``. Context
    nodes (Period/Vendor/Customer) one hop away are included but never expanded. ``order`` lists
    node ids in the order they were discovered (root first) for UI highlighting.
    """
    root_node = view.node(root)
    if root_node is None:
        return GraphSubgraph(root=root)
    nodes: dict[str, GraphNode] = {root: root_node}
    edges: list[GraphEdge] = []
    seen_edges: set[str] = set()
    order: list[str] = [root]
    queue: deque[tuple[str, int]] = deque([(root, 0)])

    def visit(edge: GraphEdge, target_id: str, depth: int) -> None:
        if edge.id not in seen_edges:
            seen_edges.add(edge.id)
            edges.append(edge)
        if target_id in nodes:
            return
        target = view.node(target_id)
        if target is None:
            return
        nodes[target_id] = target
        order.append(target_id)
        if target.label not in CONTEXT_LABELS:
            queue.append((target_id, depth + 1))

    while queue:
        nid, depth = queue.popleft()
        if depth >= max_depth:
            continue
        outgoing = view.out_edges(nid)
        incoming = view.in_edges(nid)
        for e in outgoing:
            if e.rel in PROVENANCE_OUT_RELS:
                visit(e, e.dst, depth)
        for e in incoming:
            if e.rel in PROVENANCE_IN_RELS:
                visit(e, e.src, depth)
        # context nodes one hop away, any relation
        for e in outgoing:
            other = view.node(e.dst)
            if other is not None and other.label in CONTEXT_LABELS:
                visit(e, e.dst, depth)
        for e in incoming:
            other = view.node(e.src)
            if other is not None and other.label in CONTEXT_LABELS:
                visit(e, e.src, depth)

    return GraphSubgraph(nodes=[nodes[i] for i in order], edges=edges, order=order, root=root)


# ---------------------------------------------------------------- cytoscape export


def _node_data(node: GraphNode) -> dict[str, Any]:
    props = node.props
    data: dict[str, Any] = {
        "id": node.id,
        "label": node.label,
        "name": props.get("name") or display_name(node.id, props),
        "period_id": period_of(node),
        "status": props.get("status"),
        "confidence": props.get("confidence"),
    }
    for key in CYTOSCAPE_PROPS:
        if key in props and key not in data:
            data[key] = props[key]
    return data


def cytoscape_elements(
    all_nodes: list[GraphNode],
    all_edges: list[GraphEdge],
    labels: list[str] | None = None,
    period_id: str | None = None,
    limit: int = 600,
    focus: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build Cytoscape ``{"nodes": [...], "edges": [...]}`` from a full graph snapshot.

    * ``focus`` → the 2-hop neighbourhood of that node id, ignoring ``labels``.
    * ``labels is None`` → knowledge layer (``KNOWLEDGE_LABELS``) plus Transaction/Invoice nodes
      attached to an Exception/Decision.
    * ``period_id`` → nodes of that period plus period-less context nodes adjacent to them.
    * ``limit`` → keep the most recently created nodes.
    """
    by_id: dict[str, GraphNode] = {n.id: n for n in all_nodes}
    adjacency: dict[str, set[str]] = {nid: set() for nid in by_id}
    for e in all_edges:
        if e.src in by_id and e.dst in by_id:
            adjacency[e.src].add(e.dst)
            adjacency[e.dst].add(e.src)

    if focus is not None:
        if focus not in by_id:
            return {"nodes": [], "edges": []}
        selected: set[str] = {focus}
        frontier = {focus}
        for _ in range(2):
            nxt: set[str] = set()
            for nid in frontier:
                nxt |= adjacency[nid]
            nxt -= selected
            selected |= nxt
            frontier = nxt
    else:
        if labels is None:
            wanted = set(KNOWLEDGE_LABELS)
            selected = {n.id for n in all_nodes if n.label in wanted}
            for n in all_nodes:
                if n.label in ATTACHED_LABELS and any(
                    by_id[o].label in ATTACHMENT_ANCHORS for o in adjacency[n.id]
                ):
                    selected.add(n.id)
        else:
            wanted = set(labels)
            selected = {n.id for n in all_nodes if n.label in wanted}
        if period_id is not None:
            kept = {nid for nid in selected if period_of(by_id[nid]) == period_id}
            for nid in selected - kept:
                node = by_id[nid]
                if node.label in PERIODLESS_LABELS and adjacency[nid] & kept:
                    kept.add(nid)
            selected = kept

    ordered = sorted(selected, key=lambda nid: (created_at_of(by_id[nid]), nid), reverse=True)
    if focus is not None:
        ordered.remove(focus)
        ordered.insert(0, focus)
    if limit is not None and limit >= 0:
        ordered = ordered[:limit]
    keep = set(ordered)
    nodes_out = [{"data": _node_data(by_id[nid]), "classes": by_id[nid].label.lower()} for nid in ordered]
    edges_out = [
        {"data": {"id": e.id, "source": e.src, "target": e.dst, "rel": e.rel}}
        for e in all_edges
        if e.src in keep and e.dst in keep
    ]
    return {"nodes": nodes_out, "edges": edges_out}
