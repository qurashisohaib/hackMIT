"""Embedded property graph: SQLite-persisted (graph_nodes / graph_edges) + networkx mirror.

Every write goes through in one short SQLAlchemy transaction and is mirrored into a
``networkx.MultiDiGraph`` guarded by an ``RLock``; reads and traversals only touch memory.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import networkx as nx
from sqlalchemy import create_engine, delete, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import session as db_session
from app.db.models import Base, GraphEdgeRow, GraphNodeRow
from app.memory.common import (
    cytoscape_elements,
    display_name,
    generate_node_id,
    jsonable_props,
    provenance_bfs,
)
from app.schemas import GraphEdge, GraphNode, GraphSubgraph, new_id

_DIRECTIONS = {"in", "out", "both"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _same_path(a: str, b: str) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return a == b


class GraphStore:
    """Write-through property graph over ``graph_nodes``/``graph_edges`` with a networkx mirror."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._g: nx.MultiDiGraph = nx.MultiDiGraph()
        self._private_factory: sessionmaker | None = None
        if _same_path(db_path, settings.db_path):
            db_session.init_db()
        else:
            engine = create_engine(
                f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, future=True
            )

            @event.listens_for(engine, "connect")
            def _pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()

            Base.metadata.create_all(engine)
            self._private_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        self._load()

    # ------------------------------------------------------------ persistence
    @contextmanager
    def _session(self) -> Iterator[Session]:
        if self._private_factory is None:
            with db_session.get_session() as s:
                yield s
            return
        s = self._private_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def _load(self) -> None:
        """Rebuild the in-memory mirror from SQLite (called once at construction)."""
        with self._lock, self._session() as s:
            g = nx.MultiDiGraph()
            for row in s.scalars(select(GraphNodeRow).order_by(GraphNodeRow.created_at, GraphNodeRow.id)):
                props = dict(row.props or {})
                props.setdefault("created_at", (row.created_at or datetime.utcnow()).isoformat())
                props.setdefault("name", display_name(row.id, props))
                g.add_node(row.id, label=row.label, props=props)
            for row in s.scalars(select(GraphEdgeRow).order_by(GraphEdgeRow.created_at, GraphEdgeRow.id)):
                if row.src in g and row.dst in g:
                    g.add_edge(row.src, row.dst, key=row.id, rel=row.rel, props=dict(row.props or {}))
            self._g = g

    # ------------------------------------------------------------ nodes
    def _node(self, node_id: str) -> GraphNode | None:
        if node_id not in self._g:
            return None
        attrs = self._g.nodes[node_id]
        return GraphNode(id=node_id, label=attrs["label"], props=dict(attrs["props"]))

    def add_node(self, label: str, props: dict, node_id: str | None = None) -> str:
        """Create a node (or merge into an existing one when ``node_id`` already exists). Returns its id."""
        nid = node_id or generate_node_id(label)
        return self.upsert_node(nid, label, props)

    def upsert_node(self, node_id: str, label: str, props: dict) -> str:
        """Insert or merge ``props`` into node ``node_id`` (label is updated too). Returns the id."""
        clean = jsonable_props(props)
        with self._lock:
            if node_id in self._g:
                merged = dict(self._g.nodes[node_id]["props"])
                merged.update(clean)
            else:
                merged = clean
                merged.setdefault("created_at", _now_iso())
            merged["name"] = display_name(node_id, merged)
            with self._session() as s:
                row = s.get(GraphNodeRow, node_id)
                if row is None:
                    s.add(GraphNodeRow(id=node_id, label=label, props=merged))
                else:
                    row.label = label
                    row.props = dict(merged)
            self._g.add_node(node_id, label=label, props=merged)
        return node_id

    def get_node(self, node_id: str) -> GraphNode | None:
        """Return the node or ``None``."""
        with self._lock:
            return self._node(node_id)

    def update_node(self, node_id: str, props: dict) -> None:
        """Merge ``props`` into an existing node; raises ``KeyError`` for unknown ids."""
        with self._lock:
            if node_id not in self._g:
                raise KeyError(f"unknown node {node_id}")
            self.upsert_node(node_id, self._g.nodes[node_id]["label"], props)

    def find_nodes(self, label: str | None = None, **prop_filters: Any) -> list[GraphNode]:
        """Nodes matching ``label`` (optional) and equality on every ``prop_filters`` item."""
        with self._lock:
            out: list[GraphNode] = []
            for nid, attrs in self._g.nodes(data=True):
                if label is not None and attrs["label"] != label:
                    continue
                props = attrs["props"]
                if all(props.get(k) == v for k, v in prop_filters.items()):
                    out.append(GraphNode(id=nid, label=attrs["label"], props=dict(props)))
            return out

    # ------------------------------------------------------------ edges
    def _edge(self, src: str, dst: str, key: str, attrs: dict) -> GraphEdge:
        return GraphEdge(id=key, rel=attrs["rel"], src=src, dst=dst, props=dict(attrs.get("props") or {}))

    def add_edge(self, src: str, rel: str, dst: str, props: dict | None = None) -> str:
        """Create a directed edge ``src -rel-> dst``; both endpoints must exist. Returns the edge id."""
        clean = jsonable_props(props)
        with self._lock:
            if src not in self._g or dst not in self._g:
                raise KeyError(f"cannot add edge {rel}: unknown endpoint {src if src not in self._g else dst}")
            eid = new_id("E")
            with self._session() as s:
                s.add(GraphEdgeRow(id=eid, rel=rel, src=src, dst=dst, props=clean))
            self._g.add_edge(src, dst, key=eid, rel=rel, props=clean)
        return eid

    def has_edge(self, src: str, rel: str, dst: str) -> bool:
        """True when at least one ``src -rel-> dst`` edge exists."""
        with self._lock:
            if not self._g.has_edge(src, dst):
                return False
            return any(a["rel"] == rel for a in self._g[src][dst].values())

    def _out_edges(self, node_id: str) -> list[GraphEdge]:
        if node_id not in self._g:
            return []
        return [self._edge(s, d, k, a) for s, d, k, a in self._g.out_edges(node_id, keys=True, data=True)]

    def _in_edges(self, node_id: str) -> list[GraphEdge]:
        if node_id not in self._g:
            return []
        return [self._edge(s, d, k, a) for s, d, k, a in self._g.in_edges(node_id, keys=True, data=True)]

    def edges_of(self, node_id: str, rel: str | None = None, direction: str = "both") -> list[GraphEdge]:
        """Edges touching ``node_id`` filtered by ``rel`` and ``direction`` (in|out|both)."""
        if direction not in _DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(_DIRECTIONS)}")
        with self._lock:
            edges: list[GraphEdge] = []
            if direction in ("out", "both"):
                edges.extend(self._out_edges(node_id))
            if direction in ("in", "both"):
                edges.extend(self._in_edges(node_id))
        if rel is not None:
            edges = [e for e in edges if e.rel == rel]
        return edges

    def neighbors(
        self, node_id: str, rel: str | None = None, direction: str = "out", label: str | None = None
    ) -> list[GraphNode]:
        """Distinct neighbour nodes reached over ``rel`` in ``direction``, optionally filtered by label."""
        with self._lock:
            seen: set[str] = set()
            out: list[GraphNode] = []
            for e in self.edges_of(node_id, rel=rel, direction=direction):
                other = e.dst if e.src == node_id else e.src
                if other in seen:
                    continue
                node = self._node(other)
                if node is None or (label is not None and node.label != label):
                    continue
                seen.add(other)
                out.append(node)
            return out

    # ------------------------------------------------------------ traversal / export
    def node(self, node_id: str) -> GraphNode | None:
        """``GraphView`` protocol: alias of ``get_node``."""
        return self.get_node(node_id)

    def out_edges(self, node_id: str) -> list[GraphEdge]:
        """``GraphView`` protocol: outgoing edges."""
        with self._lock:
            return self._out_edges(node_id)

    def in_edges(self, node_id: str) -> list[GraphEdge]:
        """``GraphView`` protocol: incoming edges."""
        with self._lock:
            return self._in_edges(node_id)

    def provenance_chain(self, node_id: str, max_depth: int = 8) -> GraphSubgraph:
        """Backward provenance walk (see ``common.provenance_bfs``), nodes/edges in traversal order."""
        with self._lock:
            return provenance_bfs(self, node_id, max_depth=max_depth)

    def subgraph(self, node_ids: list[str], include_edges: bool = True) -> GraphSubgraph:
        """The given nodes (unknown ids skipped) and, optionally, every edge among them."""
        with self._lock:
            nodes = [n for n in (self._node(i) for i in node_ids) if n is not None]
            keep = {n.id for n in nodes}
            edges: list[GraphEdge] = []
            if include_edges:
                for s, d, k, a in self._g.edges(keys=True, data=True):
                    if s in keep and d in keep:
                        edges.append(self._edge(s, d, k, a))
            return GraphSubgraph(nodes=nodes, edges=edges, order=[n.id for n in nodes], root=None)

    def all_nodes(self) -> list[GraphNode]:
        """Snapshot of every node."""
        with self._lock:
            return [GraphNode(id=n, label=a["label"], props=dict(a["props"])) for n, a in self._g.nodes(data=True)]

    def all_edges(self) -> list[GraphEdge]:
        """Snapshot of every edge."""
        with self._lock:
            return [self._edge(s, d, k, a) for s, d, k, a in self._g.edges(keys=True, data=True)]

    def export_cytoscape(
        self,
        labels: list[str] | None = None,
        period_id: str | None = None,
        limit: int = 600,
        focus: str | None = None,
    ) -> dict:
        """Cytoscape elements (see ``common.cytoscape_elements`` for the selection rules)."""
        with self._lock:
            nodes = self.all_nodes()
            edges = self.all_edges()
        return cytoscape_elements(nodes, edges, labels=labels, period_id=period_id, limit=limit, focus=focus)

    def stats(self) -> dict:
        """``{"nodes", "edges", "by_label", "by_rel"}`` counts."""
        with self._lock:
            by_label: dict[str, int] = {}
            for _, a in self._g.nodes(data=True):
                by_label[a["label"]] = by_label.get(a["label"], 0) + 1
            by_rel: dict[str, int] = {}
            for _, _, a in self._g.edges(data=True):
                by_rel[a["rel"]] = by_rel.get(a["rel"], 0) + 1
            return {
                "nodes": self._g.number_of_nodes(),
                "edges": self._g.number_of_edges(),
                "by_label": dict(sorted(by_label.items())),
                "by_rel": dict(sorted(by_rel.items())),
            }

    def clear(self) -> None:
        """Delete every node and edge from SQLite and memory."""
        with self._lock:
            with self._session() as s:
                s.execute(delete(GraphEdgeRow))
                s.execute(delete(GraphNodeRow))
            self._g = nx.MultiDiGraph()
