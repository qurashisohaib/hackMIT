"""Optional Neo4j backend with the same public interface as ``GraphStore``.

Node props are stored as a JSON string (``props_json``) alongside a few indexable scalars
(``label``, ``name``, ``period_id``, ``status``); edges are typed relationships carrying
``id``/``props_json``. The ``neo4j`` driver is imported lazily so the package stays optional.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.memory.common import (
    PROVENANCE_IN_RELS,
    PROVENANCE_OUT_RELS,
    cytoscape_elements,
    display_name,
    generate_node_id,
    jsonable_props,
    period_of,
    provenance_bfs,
)
from app.schemas import GraphEdge, GraphNode, GraphSubgraph, new_id

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DIRECTIONS = {"in", "out", "both"}


def _ident(value: str, what: str) -> str:
    """Validate a label / relationship type so it can be interpolated into Cypher safely."""
    if not _IDENT_RE.match(value):
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Snapshot:
    """In-memory ``GraphView`` over rows fetched from Neo4j."""

    def __init__(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        self._nodes = {n.id: n for n in nodes}
        self._out: dict[str, list[GraphEdge]] = {}
        self._in: dict[str, list[GraphEdge]] = {}
        for e in edges:
            self._out.setdefault(e.src, []).append(e)
            self._in.setdefault(e.dst, []).append(e)

    def node(self, node_id: str) -> GraphNode | None:
        return self._nodes.get(node_id)

    def out_edges(self, node_id: str) -> list[GraphEdge]:
        return list(self._out.get(node_id, []))

    def in_edges(self, node_id: str) -> list[GraphEdge]:
        return list(self._in.get(node_id, []))


class Neo4jStore:
    """Property graph backed by Neo4j; identical public interface to ``GraphStore``."""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ) -> None:
        self.uri = uri or settings.neo4j_uri
        if not self.uri:
            raise ValueError("Neo4jStore requires a URI (NEO4J_URI)")
        self.user = user or settings.neo4j_user
        self.password = password or settings.neo4j_password
        self.database = database
        self._lock = threading.RLock()
        try:
            from neo4j import GraphDatabase  # imported lazily: optional dependency
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "The 'neo4j' package is required for Neo4jStore; install it with `uv add neo4j` "
                "or unset NEO4J_URI to use the embedded GraphStore."
            ) from exc
        self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        self._ensure_constraints()

    # ------------------------------------------------------------ plumbing
    def _run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self._lock, self._driver.session(database=self.database) as s:
            return [r.data() for r in s.run(query, **params)]

    def _ensure_constraints(self) -> None:
        self._run("CREATE CONSTRAINT mem_node_id IF NOT EXISTS FOR (n:MemNode) REQUIRE n.id IS UNIQUE")
        self._run("CREATE INDEX mem_node_label IF NOT EXISTS FOR (n:MemNode) ON (n.label)")
        self._run("CREATE INDEX mem_node_period IF NOT EXISTS FOR (n:MemNode) ON (n.period_id)")

    @staticmethod
    def _node_from_record(rec: dict[str, Any]) -> GraphNode:
        props = json.loads(rec.get("props_json") or "{}")
        return GraphNode(id=rec["id"], label=rec["label"], props=props)

    @staticmethod
    def _edge_from_record(rec: dict[str, Any]) -> GraphEdge:
        props = json.loads(rec.get("props_json") or "{}")
        return GraphEdge(id=rec["id"], rel=rec["rel"], src=rec["src"], dst=rec["dst"], props=props)

    _NODE_RETURN = "n.id AS id, n.label AS label, n.props_json AS props_json"
    _EDGE_RETURN = "r.id AS id, type(r) AS rel, a.id AS src, b.id AS dst, r.props_json AS props_json"

    def close(self) -> None:
        """Close the driver."""
        self._driver.close()

    # ------------------------------------------------------------ nodes
    def add_node(self, label: str, props: dict, node_id: str | None = None) -> str:
        """Create (or merge into) a node; returns its id."""
        return self.upsert_node(node_id or generate_node_id(label), label, props)

    def upsert_node(self, node_id: str, label: str, props: dict) -> str:
        """MERGE the node, merging ``props`` over existing ones. Returns the id."""
        _ident(label, "label")
        clean = jsonable_props(props)
        existing = self.get_node(node_id)
        merged = dict(existing.props) if existing else {}
        merged.update(clean)
        merged.setdefault("created_at", _now_iso())
        merged["name"] = display_name(node_id, merged)
        node = GraphNode(id=node_id, label=label, props=merged)
        old_label = existing.label if existing else None
        query = (
            "MERGE (n:MemNode {id: $id}) "
            + (f"REMOVE n:`{old_label}` " if old_label and old_label != label else "")
            + f"SET n:`{label}`, n.label = $label, n.name = $name, n.period_id = $period_id, "
            "n.status = $status, n.created_at = $created_at, n.props_json = $props_json"
        )
        self._run(
            query,
            id=node_id,
            label=label,
            name=merged["name"],
            period_id=period_of(node),
            status=merged.get("status"),
            created_at=merged["created_at"],
            props_json=json.dumps(merged, sort_keys=True),
        )
        return node_id

    def get_node(self, node_id: str) -> GraphNode | None:
        """Return the node or ``None``."""
        rows = self._run(f"MATCH (n:MemNode {{id: $id}}) RETURN {self._NODE_RETURN}", id=node_id)
        return self._node_from_record(rows[0]) if rows else None

    def update_node(self, node_id: str, props: dict) -> None:
        """Merge ``props`` into an existing node; raises ``KeyError`` for unknown ids."""
        existing = self.get_node(node_id)
        if existing is None:
            raise KeyError(f"unknown node {node_id}")
        self.upsert_node(node_id, existing.label, props)

    def find_nodes(self, label: str | None = None, **prop_filters: Any) -> list[GraphNode]:
        """Nodes matching ``label`` and equality on ``prop_filters`` (filtered client-side on props)."""
        if label is not None:
            rows = self._run(
                f"MATCH (n:MemNode:`{_ident(label, 'label')}`) RETURN {self._NODE_RETURN} "
                "ORDER BY n.created_at, n.id"
            )
        else:
            rows = self._run(f"MATCH (n:MemNode) RETURN {self._NODE_RETURN} ORDER BY n.created_at, n.id")
        nodes = [self._node_from_record(r) for r in rows]
        return [n for n in nodes if all(n.props.get(k) == v for k, v in prop_filters.items())]

    # ------------------------------------------------------------ edges
    def add_edge(self, src: str, rel: str, dst: str, props: dict | None = None) -> str:
        """Create ``src -rel-> dst``; both endpoints must exist. Returns the edge id."""
        _ident(rel, "relationship type")
        eid = new_id("E")
        rows = self._run(
            "MATCH (a:MemNode {id: $src}), (b:MemNode {id: $dst}) "
            f"CREATE (a)-[r:`{rel}` {{id: $eid, props_json: $props_json, created_at: $created_at}}]->(b) "
            "RETURN r.id AS id",
            src=src,
            dst=dst,
            eid=eid,
            props_json=json.dumps(jsonable_props(props), sort_keys=True),
            created_at=_now_iso(),
        )
        if not rows:
            raise KeyError(f"cannot add edge {rel}: unknown endpoint {src} or {dst}")
        return eid

    def has_edge(self, src: str, rel: str, dst: str) -> bool:
        """True when at least one ``src -rel-> dst`` edge exists."""
        rows = self._run(
            f"MATCH (:MemNode {{id: $src}})-[r:`{_ident(rel, 'relationship type')}`]->(:MemNode {{id: $dst}}) "
            "RETURN count(r) AS c",
            src=src,
            dst=dst,
        )
        return bool(rows and rows[0]["c"] > 0)

    def edges_of(self, node_id: str, rel: str | None = None, direction: str = "both") -> list[GraphEdge]:
        """Edges touching ``node_id`` filtered by ``rel`` and ``direction`` (in|out|both)."""
        if direction not in _DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(_DIRECTIONS)}")
        rel_part = f":`{_ident(rel, 'relationship type')}`" if rel else ""
        edges: list[GraphEdge] = []
        if direction in ("out", "both"):
            rows = self._run(
                f"MATCH (a:MemNode {{id: $id}})-[r{rel_part}]->(b:MemNode) RETURN {self._EDGE_RETURN} "
                "ORDER BY r.created_at, r.id",
                id=node_id,
            )
            edges.extend(self._edge_from_record(r) for r in rows)
        if direction in ("in", "both"):
            rows = self._run(
                f"MATCH (a:MemNode)-[r{rel_part}]->(b:MemNode {{id: $id}}) RETURN {self._EDGE_RETURN} "
                "ORDER BY r.created_at, r.id",
                id=node_id,
            )
            edges.extend(self._edge_from_record(r) for r in rows)
        return edges

    def neighbors(
        self, node_id: str, rel: str | None = None, direction: str = "out", label: str | None = None
    ) -> list[GraphNode]:
        """Distinct neighbour nodes over ``rel`` in ``direction``, optionally filtered by label."""
        ids: list[str] = []
        seen: set[str] = set()
        for e in self.edges_of(node_id, rel=rel, direction=direction):
            other = e.dst if e.src == node_id else e.src
            if other not in seen:
                seen.add(other)
                ids.append(other)
        nodes = self._nodes_by_ids(ids)
        return [n for n in nodes if label is None or n.label == label]

    def _nodes_by_ids(self, ids: list[str]) -> list[GraphNode]:
        if not ids:
            return []
        rows = self._run(f"MATCH (n:MemNode) WHERE n.id IN $ids RETURN {self._NODE_RETURN}", ids=ids)
        by_id = {r["id"]: self._node_from_record(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    # ------------------------------------------------------------ traversal / export
    def provenance_chain(self, node_id: str, max_depth: int = 8) -> GraphSubgraph:
        """Fetch the provenance-reachable region with a variable-length path query, then order it."""
        rel_types = "|".join(f"`{r}`" for r in sorted(set(PROVENANCE_OUT_RELS) | set(PROVENANCE_IN_RELS)))
        depth = max(1, int(max_depth))
        rows = self._run(
            f"MATCH p = (root:MemNode {{id: $id}})-[:{rel_types}*0..{depth}]-(m:MemNode) "
            "WITH collect(DISTINCT m) AS ms "
            "UNWIND ms AS n "
            f"RETURN DISTINCT {self._NODE_RETURN}",
            id=node_id,
        )
        nodes = [self._node_from_record(r) for r in rows]
        ids = [n.id for n in nodes]
        if not ids:
            return GraphSubgraph(root=node_id)
        erows = self._run(
            "MATCH (a:MemNode)-[r]->(b:MemNode) WHERE a.id IN $ids AND b.id IN $ids "
            f"RETURN {self._EDGE_RETURN}",
            ids=ids,
        )
        # Context nodes (Period/Vendor/Customer) one hop away over any relation.
        crows = self._run(
            "MATCH (a:MemNode)-[r]-(c:MemNode) WHERE a.id IN $ids AND c.label IN ['Period','Vendor','Customer'] "
            "AND NOT c.id IN $ids "
            "RETURN DISTINCT c.id AS id, c.label AS label, c.props_json AS props_json",
            ids=ids,
        )
        context_nodes = [self._node_from_record(r) for r in crows]
        cerows = self._run(
            "MATCH (a:MemNode)-[r]->(b:MemNode) WHERE a.id IN $ids AND b.id IN $ctx "
            f"RETURN {self._EDGE_RETURN} "
            "UNION "
            "MATCH (a:MemNode)-[r]->(b:MemNode) WHERE a.id IN $ctx AND b.id IN $ids "
            f"RETURN {self._EDGE_RETURN}",
            ids=ids,
            ctx=[n.id for n in context_nodes],
        ) if context_nodes else []
        edges = [self._edge_from_record(r) for r in erows] + [self._edge_from_record(r) for r in cerows]
        snapshot = _Snapshot(nodes + context_nodes, edges)
        return provenance_bfs(snapshot, node_id, max_depth=max_depth)

    def subgraph(self, node_ids: list[str], include_edges: bool = True) -> GraphSubgraph:
        """The given nodes and, optionally, every edge among them."""
        nodes = self._nodes_by_ids(node_ids)
        edges: list[GraphEdge] = []
        if include_edges and nodes:
            ids = [n.id for n in nodes]
            rows = self._run(
                "MATCH (a:MemNode)-[r]->(b:MemNode) WHERE a.id IN $ids AND b.id IN $ids "
                f"RETURN {self._EDGE_RETURN}",
                ids=ids,
            )
            edges = [self._edge_from_record(r) for r in rows]
        return GraphSubgraph(nodes=nodes, edges=edges, order=[n.id for n in nodes], root=None)

    def all_nodes(self) -> list[GraphNode]:
        """Every node."""
        rows = self._run(f"MATCH (n:MemNode) RETURN {self._NODE_RETURN} ORDER BY n.created_at, n.id")
        return [self._node_from_record(r) for r in rows]

    def all_edges(self) -> list[GraphEdge]:
        """Every edge."""
        rows = self._run(
            f"MATCH (a:MemNode)-[r]->(b:MemNode) RETURN {self._EDGE_RETURN} ORDER BY r.created_at, r.id"
        )
        return [self._edge_from_record(r) for r in rows]

    def export_cytoscape(
        self,
        labels: list[str] | None = None,
        period_id: str | None = None,
        limit: int = 600,
        focus: str | None = None,
    ) -> dict:
        """Cytoscape elements with the same selection rules as ``GraphStore``."""
        for lab in labels or []:
            _ident(lab, "label")
        return cytoscape_elements(
            self.all_nodes(), self.all_edges(), labels=labels, period_id=period_id, limit=limit, focus=focus
        )

    def stats(self) -> dict:
        """``{"nodes", "edges", "by_label", "by_rel"}`` counts."""
        by_label = {
            r["label"]: r["c"]
            for r in self._run("MATCH (n:MemNode) RETURN n.label AS label, count(n) AS c ORDER BY label")
        }
        by_rel = {
            r["rel"]: r["c"]
            for r in self._run(
                "MATCH (:MemNode)-[r]->(:MemNode) RETURN type(r) AS rel, count(r) AS c ORDER BY rel"
            )
        }
        return {
            "nodes": sum(by_label.values()),
            "edges": sum(by_rel.values()),
            "by_label": by_label,
            "by_rel": by_rel,
        }

    def clear(self) -> None:
        """Delete every memory node and relationship."""
        self._run("MATCH (n:MemNode) DETACH DELETE n")
