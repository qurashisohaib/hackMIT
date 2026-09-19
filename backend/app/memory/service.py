"""MemoryService: rules, precedents, exception lifecycle and provenance over a GraphStore.

All financial-truth mirrors use the truth id as node id, so agents can hop from a bank
transaction straight to the exceptions, decisions and rules that touched it.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.memory.common import canonical_json, jsonable
from app.memory.embeddings import Embedder, cosine, get_embedder
from app.memory.graph import GraphStore
from app.schemas import (
    AgentEvent,
    AuditFinding,
    Decision,
    DecisionStatus,
    ExceptionRecord,
    ExceptionStatus,
    GraphNode,
    HumanCorrection,
    Hypothesis,
    PrecedentMatch,
    PrecedentQuery,
    RuleSpec,
    RuleTrust,
    RunSummary,
)

ENTITY_LABELS: dict[str, str] = {
    "bank_transaction": "Transaction",
    "transaction": "Transaction",
    "ap_invoice": "Invoice",
    "ar_invoice": "Invoice",
    "invoice": "Invoice",
    "purchase_order": "PurchaseOrder",
    "payment": "Payment",
    "payroll_run": "Payment",
}
COUNTERPARTY_LABELS: dict[str, str] = {"vendor": "Vendor", "customer": "Customer"}
SCOPE_LABELS: dict[str, str] = {"vendor": "Vendor", "customer": "Customer", "account": "Account"}
ID_PREFIX_LABELS: tuple[tuple[str, str], ...] = (
    ("BT-", "Transaction"),
    ("INV-", "Invoice"),
    ("AR-", "Invoice"),
    ("PO-", "PurchaseOrder"),
    ("PAY-", "Payment"),
    ("PR-", "Payment"),
    ("GR-", "PurchaseOrder"),
)
DECISION_TO_EXCEPTION_STATUS: dict[str, str] = {
    DecisionStatus.EXECUTED.value: ExceptionStatus.RESOLVED.value,
    DecisionStatus.PENDING_AUDIT.value: ExceptionStatus.PENDING_AUDIT.value,
    DecisionStatus.ESCALATED.value: ExceptionStatus.NEEDS_HUMAN.value,
    DecisionStatus.REJECTED.value: ExceptionStatus.NEEDS_HUMAN.value,
    DecisionStatus.PROPOSED.value: ExceptionStatus.OPEN.value,
}

STRUCTURAL_WEIGHT = 0.55
SEMANTIC_WEIGHT = 0.30
TRUST_WEIGHT = 0.15


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _created(node: GraphNode) -> str:
    return str(node.props.get("created_at") or "")


def _newest_first(nodes: list[GraphNode]) -> list[GraphNode]:
    return sorted(nodes, key=lambda n: (_created(n), n.id), reverse=True)


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def label_for_entity_id(entity_id: str) -> str:
    """Guess the mirror label of a financial-truth id from its prefix (defaults to Transaction)."""
    for prefix, label in ID_PREFIX_LABELS:
        if entity_id.startswith(prefix):
            return label
    return "Transaction"


def compute_trust(trust: RuleTrust) -> float:
    """ARCHITECTURE §5: clamp(0.5 + 0.25*human_verified + 0.05*confirmed - 0.15*refuted, 0, 1)."""
    raw = 0.5 + 0.25 * float(trust.human_verified) + 0.05 * trust.times_confirmed - 0.15 * trust.times_refuted
    return max(0.0, min(1.0, raw))


class MemoryService:
    """High-level memory operations for agents and the API layer."""

    def __init__(self, store: GraphStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder
        self._lock = threading.RLock()

    # ------------------------------------------------------------ helpers
    def _ensure_period(self, period_id: str) -> str:
        if self.store.get_node(period_id) is None:
            self.store.upsert_node(period_id, "Period", {"name": period_id})
        return period_id

    def _link(self, src: str, rel: str, dst: str, props: dict | None = None) -> None:
        """Add ``src -rel-> dst`` once (no duplicate edges), skipping unknown endpoints."""
        if self.store.get_node(src) is None or self.store.get_node(dst) is None:
            return
        if not self.store.has_edge(src, rel, dst):
            self.store.add_edge(src, rel, dst, props)

    def _link_period(self, node_id: str, period_id: str | None) -> None:
        if period_id:
            self._ensure_period(period_id)
            self._link(node_id, "IN_PERIOD", period_id)

    def _trust_of(self, node: GraphNode) -> RuleTrust:
        return RuleTrust(**(node.props.get("trust") or {}))

    def _rule_node(self, rule_id: str) -> GraphNode:
        node = self.store.get_node(rule_id)
        if node is None or node.label != "Rule":
            raise KeyError(f"unknown rule {rule_id}")
        return node

    def _update_trust(self, rule_id: str, **changes: Any) -> RuleTrust:
        node = self._rule_node(rule_id)
        trust = self._trust_of(node)
        data = trust.model_dump()
        for key, delta in changes.items():
            if key == "human_verified":
                data[key] = bool(data[key] or delta)
            else:
                data[key] = int(data[key]) + int(delta)
        trust = RuleTrust(**data)
        self.store.update_node(rule_id, {"trust": trust.model_dump(), "trust_score": compute_trust(trust)})
        return trust

    # ------------------------------------------------------------ entity mirrors
    def ensure_entity(self, label: str, entity_id: str, props: dict) -> str:
        """Idempotently mirror a financial-truth row as a node (id == truth id); links IN_PERIOD."""
        with self._lock:
            self.store.upsert_node(entity_id, label, props)
            period_id = (props or {}).get("period_id")
            if isinstance(period_id, str) and period_id and label != "Period":
                self._link_period(entity_id, period_id)
            return entity_id

    # ------------------------------------------------------------ exception lifecycle
    def record_exception(self, exc: ExceptionRecord) -> str:
        """Create the Exception node with ABOUT / IN_PERIOD / FLAGGED edges. Returns the exception id."""
        with self._lock:
            props = exc.model_dump(mode="json")
            self.store.upsert_node(exc.id, "Exception", props)
            entity_label = ENTITY_LABELS.get(exc.entity_type, label_for_entity_id(exc.entity_id))
            self.ensure_entity(entity_label, exc.entity_id, {"period_id": exc.period_id})
            self._link(exc.id, "ABOUT", exc.entity_id)
            self._link_period(exc.id, exc.period_id)
            cp_label = COUNTERPARTY_LABELS.get(exc.counterparty_type or "")
            if cp_label and exc.counterparty_id:
                cp_props: dict[str, Any] = {}
                if exc.counterparty_name:
                    cp_props["name"] = exc.counterparty_name
                self.ensure_entity(cp_label, exc.counterparty_id, cp_props)
                self._link(exc.id, "FLAGGED", exc.counterparty_id)
            return exc.id

    def record_hypothesis(self, exception_id: str, hyp: Hypothesis) -> str:
        """Create the Hypothesis node, PROPOSED_BY from the exception, USED_PRECEDENT to its rule."""
        with self._lock:
            props = hyp.model_dump(mode="json")
            props["exception_id"] = exception_id
            self.store.upsert_node(hyp.id, "Hypothesis", props)
            exc = self.store.get_node(exception_id)
            if exc is not None:
                self._link(exception_id, "PROPOSED_BY", hyp.id, {"generated_by": hyp.generated_by})
                ids = list(exc.props.get("hypothesis_ids") or [])
                if hyp.id not in ids:
                    ids.append(hyp.id)
                    self.store.update_node(exception_id, {"hypothesis_ids": ids})
                self._link_period(hyp.id, exc.props.get("period_id"))
            if hyp.rule_id:
                self._link(hyp.id, "USED_PRECEDENT", hyp.rule_id)
            return hyp.id

    def record_decision(self, exception_id: str, decision: Decision) -> str:
        """Create the Decision node, its edges, and update the exception's status/decision/confidence."""
        with self._lock:
            props = decision.model_dump(mode="json")
            props["exception_id"] = exception_id
            self.store.upsert_node(decision.id, "Decision", props)
            self._link(exception_id, "RESOLVED_BY", decision.id)
            self._link_period(decision.id, decision.period_id)
            if decision.hypothesis_id:
                self._link(decision.id, "TESTED", decision.hypothesis_id)
            for matched in decision.matched_ids:
                if self.store.get_node(matched) is None:
                    self.ensure_entity(label_for_entity_id(matched), matched, {"period_id": decision.period_id})
                self._link(decision.id, "MATCHES", matched)
            if decision.rule_id and self.store.get_node(decision.rule_id) is not None:
                self.mark_applied(decision.rule_id, decision.id)
            status_value = _enum_value(decision.status)
            update: dict[str, Any] = {
                "decision_id": decision.id,
                "confidence": decision.confidence,
                "tier": _enum_value(decision.tier),
                "status": DECISION_TO_EXCEPTION_STATUS.get(status_value, ExceptionStatus.OPEN.value),
            }
            if status_value == DecisionStatus.EXECUTED.value:
                update["resolved_at"] = jsonable(decision.executed_at) or _now_iso()
                update["resolved_by"] = f"agent:{decision.agent}"
            if self.store.get_node(exception_id) is not None:
                self.store.update_node(exception_id, update)
            return decision.id

    def record_audit_finding(self, decision_id: str, finding: AuditFinding) -> str:
        """Create the AuditFinding node; CHALLENGED_BY always, VERIFIED_BY when approved."""
        with self._lock:
            props = finding.model_dump(mode="json")
            props["decision_id"] = decision_id
            self.store.upsert_node(finding.id, "AuditFinding", props)
            self._link(decision_id, "CHALLENGED_BY", finding.id)
            if finding.verdict == "approved":
                self._link(decision_id, "VERIFIED_BY", finding.id)
            decision = self.store.get_node(decision_id)
            if decision is not None:
                self._link_period(finding.id, decision.props.get("period_id"))
            return finding.id

    def record_human_correction(self, exception_id: str, correction: HumanCorrection) -> str:
        """Create the HumanCorrection node with CORRECTED_BY from the exception (and its decision)."""
        with self._lock:
            props = correction.model_dump(mode="json")
            props["exception_id"] = exception_id
            self.store.upsert_node(correction.id, "HumanCorrection", props)
            self._link(exception_id, "CORRECTED_BY", correction.id)
            exc = self.store.get_node(exception_id)
            if exc is not None:
                self._link_period(correction.id, exc.props.get("period_id"))
                if correction.action == "approve_hypothesis":
                    decision_id = exc.props.get("decision_id")
                    if decision_id:
                        self._link(decision_id, "CORRECTED_BY", correction.id)
            if correction.hypothesis_id:
                self._link(correction.hypothesis_id, "APPROVED_BY", correction.id)
            return correction.id

    def record_agent_run(self, run: RunSummary, events: list[AgentEvent] | None = None) -> str:
        """Upsert the AgentRun node (id == run_id); ``events`` (or an existing trace) is kept in props."""
        with self._lock:
            props = run.model_dump(mode="json")
            props["name"] = f"Run {run.run_id} ({run.period_id})"
            if events is not None:
                props["events"] = [e.model_dump(mode="json") for e in events]
            self.store.upsert_node(run.run_id, "AgentRun", props)
            self._link_period(run.run_id, run.period_id)
            return run.run_id

    def record_observation(self, scope_type: str, scope_id: str, key: str, value: Any, period_id: str) -> str:
        """Upsert an Observation keyed by scope+key+period, OBSERVED_IN the scope entity."""
        with self._lock:
            digest = hashlib.blake2b(
                f"{scope_type}|{scope_id}|{key}|{period_id}".encode("utf-8"), digest_size=6
            ).hexdigest()
            obs_id = f"OBS-{digest}"
            props = {
                "scope_type": scope_type,
                "scope_id": scope_id,
                "key": key,
                "value": jsonable(value),
                "period_id": period_id,
                "name": f"{key}={jsonable(value)} ({scope_id}, {period_id})",
                "observed_at": _now_iso(),
            }
            self.store.upsert_node(obs_id, "Observation", props)
            scope_label = SCOPE_LABELS.get(scope_type)
            if scope_label and self.store.get_node(scope_id) is None:
                self.store.upsert_node(scope_id, scope_label, {})
            self._link(obs_id, "OBSERVED_IN", scope_id)
            self._link_period(obs_id, period_id)
            return obs_id

    # ------------------------------------------------------------ learning
    def learn_rule(self, spec: RuleSpec, source: str, period_id: str, human_verified: bool) -> str:
        """Create a Rule from ``spec`` (or bump a version when one exists for the same scope+pattern).

        Re-teaching an active rule with identical params only confirms it and returns its id.
        """
        with self._lock:
            pattern_type = _enum_value(spec.pattern_type)
            scope_type = _enum_value(spec.scope_type)
            scope_id = spec.scope_id if scope_type != "global" else None
            existing = [
                r for r in self.active_rules(scope_type=scope_type, pattern_type=pattern_type)
                if r.props.get("scope_id") == scope_id
            ]
            existing = _newest_first(existing)
            if existing and canonical_json(existing[0].props.get("params")) == canonical_json(spec.params):
                rule_id = existing[0].id
                self.confirm_rule(rule_id, source)
                if human_verified:
                    self._update_trust(rule_id, human_verified=True)
                self._link(rule_id, "LEARNED_FROM", source, {"reconfirmed_in": period_id})
                return rule_id

            version = 1 + max((int(r.props.get("version") or 1) for r in existing), default=0)
            trust = RuleTrust(human_verified=human_verified)
            scope_name: str | None = None
            if scope_id:
                scope_node = self.store.get_node(scope_id)
                if scope_node is not None:
                    scope_name = scope_node.props.get("name") or scope_id
            source_node = self.store.get_node(source)
            created_by = "human" if (source_node is not None and source_node.label == "HumanCorrection") else "agent"
            if source_node is None and human_verified:
                created_by = "human"
            props: dict[str, Any] = {
                "pattern_type": pattern_type,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "scope_name": scope_name,
                "params": jsonable(spec.params),
                "description": spec.description,
                "status": "active",
                "version": version,
                "trust": trust.model_dump(),
                "trust_score": compute_trust(trust),
                "learned_in_period": period_id,
                "created_by": created_by,
                "source_id": source,
                "embedding": self.embedder.embed(spec.description),
                "name": spec.description,
            }
            rule_id = self.store.add_node("Rule", props)
            self._link(rule_id, "LEARNED_FROM", source)
            if scope_id:
                if self.store.get_node(scope_id) is None:
                    self.store.upsert_node(scope_id, SCOPE_LABELS.get(scope_type, "Account"), {})
                self._link(rule_id, "APPLIES_TO", scope_id)
            self._link_period(rule_id, period_id)
            for old in existing:
                self.store.add_edge(rule_id, "SUPERSEDES", old.id, {"reason": "re-taught", "period_id": period_id})
                self.store.update_node(old.id, {"status": "superseded", "superseded_by": rule_id, "superseded_at": _now_iso()})
            return rule_id

    def confirm_rule(self, rule_id: str, by: str) -> None:
        """``times_confirmed += 1`` and VERIFIED_BY Rule→``by`` when that node exists."""
        with self._lock:
            self._update_trust(rule_id, times_confirmed=1)
            self._link(rule_id, "VERIFIED_BY", by, {"at": _now_iso()})

    def refute_rule(self, rule_id: str, by: str) -> None:
        """``times_refuted += 1`` and CHALLENGED_BY Rule→``by`` when that node exists."""
        with self._lock:
            self._update_trust(rule_id, times_refuted=1)
            self._link(rule_id, "CHALLENGED_BY", by, {"at": _now_iso()})

    def mark_applied(self, rule_id: str, decision_id: str) -> None:
        """``times_applied += 1`` and USED_PRECEDENT Decision→Rule."""
        with self._lock:
            self._update_trust(rule_id, times_applied=1)
            self._link(decision_id, "USED_PRECEDENT", rule_id)

    def rule_trust(self, rule_id: str) -> float:
        """Trust score in [0, 1] per ARCHITECTURE §5."""
        return compute_trust(self._trust_of(self._rule_node(rule_id)))

    # ------------------------------------------------------------ retrieval
    def active_rules(
        self, scope_type: str | None = None, scope_id: str | None = None, pattern_type: str | None = None
    ) -> list[GraphNode]:
        """Active Rule nodes filtered by scope_type / scope_id / pattern_type (newest first)."""
        filters: dict[str, Any] = {"status": "active"}
        if scope_type is not None:
            filters["scope_type"] = _enum_value(scope_type)
        if scope_id is not None:
            filters["scope_id"] = scope_id
        if pattern_type is not None:
            filters["pattern_type"] = _enum_value(pattern_type)
        return _newest_first(self.store.find_nodes("Rule", **filters))

    def find_precedents(self, query: PrecedentQuery) -> list[PrecedentMatch]:
        """Score active rules: 0.55*structural + 0.30*semantic + 0.15*trust (ARCHITECTURE §3)."""
        hints = {_enum_value(h) for h in query.pattern_hints}
        query_vec = self.embedder.embed(query.text) if query.text.strip() else []
        matches: list[PrecedentMatch] = []
        for rule in self.active_rules():
            props = rule.props
            if hints and props.get("pattern_type") not in hints:
                continue
            if query.counterparty_id and props.get("scope_id") == query.counterparty_id:
                structural = 1.0
            elif props.get("scope_type") == "global":
                structural = 0.6
            else:
                continue
            semantic = cosine(query_vec, props.get("embedding") or []) if query_vec else 0.0
            semantic = max(0.0, semantic)
            trust = compute_trust(self._trust_of(rule))
            score = STRUCTURAL_WEIGHT * structural + SEMANTIC_WEIGHT * semantic + TRUST_WEIGHT * trust
            matches.append(
                PrecedentMatch(
                    rule_id=rule.id,
                    rule=self._rule_view(rule),
                    score=round(score, 6),
                    structural=structural,
                    semantic=round(semantic, 6),
                    trust=trust,
                )
            )
        matches.sort(key=lambda m: (-m.score, m.rule_id))
        return matches[: max(0, query.limit)]

    def observations(self, scope_type: str, scope_id: str, key: str) -> list[GraphNode]:
        """Observation nodes for scope+key, newest first."""
        return _newest_first(self.store.find_nodes("Observation", scope_type=scope_type, scope_id=scope_id, key=key))

    # ------------------------------------------------------------ views
    def _hypotheses_of(self, exception_id: str) -> list[dict[str, Any]]:
        nodes = self.store.neighbors(exception_id, rel="PROPOSED_BY", direction="out", label="Hypothesis")
        nodes.sort(key=lambda n: (_created(n), n.id))
        return [dict(n.props) for n in nodes]

    def _exception_view(self, node: GraphNode) -> dict[str, Any]:
        view = dict(node.props)
        view["id"] = node.id
        view.setdefault("tags", [])
        hyps = self._hypotheses_of(node.id)
        view["best_hypothesis"] = max(hyps, key=lambda h: float(h.get("confidence") or 0.0)) if hyps else None
        view.setdefault("ground_truth_type", None)
        return view

    def exceptions(self, period_id: str | None = None, status: str | None = None) -> list[dict]:
        """ExceptionView-shaped dicts, newest first, filtered by period and/or status."""
        filters: dict[str, Any] = {}
        if period_id is not None:
            filters["period_id"] = period_id
        if status is not None:
            filters["status"] = _enum_value(status)
        nodes = _newest_first(self.store.find_nodes("Exception", **filters))
        return [self._exception_view(n) for n in nodes]

    def _decision_of(self, exception_id: str) -> GraphNode | None:
        decisions = self.store.neighbors(exception_id, rel="RESOLVED_BY", direction="out", label="Decision")
        if not decisions:
            return None
        return _newest_first(decisions)[0]

    def exception_detail(self, exception_id: str) -> dict:
        """ExceptionDetail-shaped dict (entity/related/trace left empty for the API layer)."""
        node = self.store.get_node(exception_id)
        if node is None or node.label != "Exception":
            raise KeyError(f"unknown exception {exception_id}")
        hypotheses = self._hypotheses_of(exception_id)
        decision = self._decision_of(exception_id)
        audit: list[dict[str, Any]] = []
        rule_ids: list[str] = []
        if decision is not None:
            findings = self.store.neighbors(decision.id, rel="CHALLENGED_BY", direction="out", label="AuditFinding")
            audit = [dict(n.props) for n in sorted(findings, key=lambda n: (_created(n), n.id))]
            rule_ids.extend(r.id for r in self.store.neighbors(decision.id, rel="USED_PRECEDENT", direction="out", label="Rule"))
        for hyp in hypotheses:
            rid = hyp.get("rule_id")
            if rid and self.store.get_node(rid) is not None:
                rule_ids.append(rid)
        corrections = self.store.neighbors(exception_id, rel="CORRECTED_BY", direction="out", label="HumanCorrection")
        corrections.sort(key=lambda n: (_created(n), n.id))
        seen: set[str] = set()
        rules_used: list[dict[str, Any]] = []
        for rid in rule_ids:
            if rid in seen:
                continue
            seen.add(rid)
            rule = self.store.get_node(rid)
            if rule is not None and rule.label == "Rule":
                rules_used.append(self._rule_view(rule))
        return {
            "exception": self._exception_view(node),
            "hypotheses": hypotheses,
            "decision": dict(decision.props) if decision is not None else None,
            "audit": audit,
            "corrections": [dict(n.props) for n in corrections],
            "rules_used": rules_used,
            "entity": {},
            "related": [],
            "trace": [],
        }

    def _rule_view(self, node: GraphNode) -> dict[str, Any]:
        props = node.props
        trust = self._trust_of(node)
        supersedes = self.store.neighbors(node.id, rel="SUPERSEDES", direction="out", label="Rule")
        sources = self.store.neighbors(node.id, rel="LEARNED_FROM", direction="out")
        return {
            "id": node.id,
            "pattern_type": props.get("pattern_type"),
            "scope_type": props.get("scope_type"),
            "scope_id": props.get("scope_id"),
            "scope_name": props.get("scope_name"),
            "params": dict(props.get("params") or {}),
            "description": props.get("description") or "",
            "status": props.get("status") or "active",
            "version": int(props.get("version") or 1),
            "trust": trust.model_dump(),
            "trust_score": compute_trust(trust),
            "learned_in_period": props.get("learned_in_period"),
            "created_by": props.get("created_by") or "agent",
            "source_id": props.get("source_id") or (sources[0].id if sources else None),
            "supersedes": supersedes[0].id if supersedes else None,
            "created_at": props.get("created_at"),
        }

    def rules(self) -> list[dict]:
        """RuleView-shaped dicts for every rule, newest first."""
        return [self._rule_view(n) for n in _newest_first(self.store.find_nodes("Rule"))]

    def provenance(self, node_id: str) -> dict:
        """``GraphSubgraph.model_dump()`` of the backward provenance chain."""
        return self.store.provenance_chain(node_id).model_dump()


# ---------------------------------------------------------------- singleton
_service: MemoryService | None = None
_service_lock = threading.Lock()


def _build_store():
    if settings.neo4j_uri:
        from app.memory.neo4j_store import Neo4jStore

        return Neo4jStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    return GraphStore(settings.db_path)


def get_memory_service() -> MemoryService:
    """Process-wide MemoryService (GraphStore on ``settings.db_path``, or Neo4jStore when NEO4J_URI is set)."""
    global _service
    with _service_lock:
        if _service is None:
            _service = MemoryService(_build_store(), get_embedder())
        return _service


def reset_memory_service() -> MemoryService:
    """Clear the graph and rebuild the singleton (used by /reset and tests)."""
    global _service
    with _service_lock:
        if _service is not None:
            _service.store.clear()
        _service = MemoryService(_build_store(), get_embedder())
        return _service
