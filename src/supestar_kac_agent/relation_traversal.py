from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .graph import KnowledgeGraph


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RelationTraversal:
    """A one-hop-at-a-time, model-directed relation traversal ledger."""

    def __init__(
        self,
        *,
        graph: KnowledgeGraph,
        anchors: list[str],
        run_dir: Path,
        ordering_salt: str,
        max_depth: int = 10,
    ) -> None:
        self.graph = graph
        self.anchors = list(dict.fromkeys(anchors))
        self.run_dir = run_dir
        self.ordering_salt = ordering_salt
        self.max_depth = max_depth
        self.status = "NOT_STARTED"
        self.current_concept_id: str | None = None
        self.active_node_ids: list[str] = []
        self.active_edge_ids: list[str] = []
        self.selected_steps: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self.visited_concept_ids: set[str] = set()
        self.current_candidates: dict[str, dict[str, Any]] = {}
        self.stop_reason: str | None = None
        self.post_hoc_validation: dict[str, Any] | None = None

    @property
    def started(self) -> bool:
        return self.status != "NOT_STARTED"

    @property
    def completed(self) -> bool:
        return self.status == "COMPLETED"

    @property
    def selected_edge_ids(self) -> list[str]:
        return [step["edge_id"] for step in self.selected_steps]

    @property
    def anchors_connected(self) -> bool:
        return self._anchors_connected()

    def _anchors_connected(self) -> bool:
        if len(self.anchors) < 2:
            return True
        adjacency: dict[str, set[str]] = {}
        # Only the current active path can complete the traversal. Edges from a
        # branch that the Agent explicitly backtracked remain in the audit
        # history but cannot silently reconnect the anchors.
        for edge_id in self.active_edge_ids:
            edge = self.graph.edges.get(edge_id)
            if not edge:
                continue
            adjacency.setdefault(edge["from"], set()).add(edge["to"])
            adjacency.setdefault(edge["to"], set()).add(edge["from"])
        start = self.anchors[0]
        visited = {start}
        pending = [start]
        while pending:
            current = pending.pop()
            for neighbor in adjacency.get(current, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    pending.append(neighbor)
        return all(anchor in visited for anchor in self.anchors)

    def _neighbor_candidates(self, concept_id: str) -> list[dict[str, Any]]:
        rows = self.graph.neighbors(concept_id, ordering_salt=self.ordering_salt)
        candidates = []
        for row in rows:
            neighbor_id = str(row["neighbor"]["id"])
            candidates.append({
                **row,
                "selectable":neighbor_id not in self.visited_concept_ids,
                "already_visited":neighbor_id in self.visited_concept_ids,
            })
        self.current_candidates = {
            row["evidence_id"]:row
            for row in candidates
            if row["selectable"]
        }
        return candidates

    def _concept_observation(self, concept_id: str) -> dict[str, Any]:
        node = self.graph.nodes[concept_id]
        return {
            "evidence_id":f"concept:{concept_id}",
            "concept":node,
            "source_refs":node["source_refs"],
            "graph_fingerprint":self.graph.fingerprint,
        }

    def observe_neighbors(self, concept: str, purpose: str) -> dict[str, Any]:
        resolved = self.graph.resolve(concept)
        if not resolved:
            return {"status":"UNKNOWN_CONCEPT", "concept":concept}
        if not self.started:
            if self.anchors and resolved not in self.anchors:
                return {
                    "status":"INVALID_TRAVERSAL_START",
                    "concept_id":resolved,
                    "allowed_anchor_ids":self.anchors,
                }
            self.status = "ACTIVE"
            self.current_concept_id = resolved
            self.active_node_ids = [resolved]
            self.visited_concept_ids.add(resolved)
        elif resolved != self.current_concept_id:
            return {
                "status":"INVALID_CURRENT_CONCEPT",
                "concept_id":resolved,
                "expected_concept_id":self.current_concept_id,
            }
        candidates = self._neighbor_candidates(resolved)
        self.actions.append({
            "action":"OBSERVE_NEIGHBORS",
            "concept_id":resolved,
            "purpose":purpose,
            "candidate_edge_ids":[row["evidence_id"] for row in candidates],
        })
        self._persist()
        return {
            "status":"NEIGHBORS_OBSERVED",
            "current_concept":self._concept_observation(resolved),
            "candidate_relations":candidates,
            "selectable_edge_ids":sorted(self.current_candidates),
            "visited_concept_ids":sorted(self.visited_concept_ids),
            "active_path":{"node_ids":list(self.active_node_ids), "edge_ids":list(self.active_edge_ids)},
            "candidate_order_policy":"RUN_SALTED_SHA256",
            "full_path_precomputed":False,
        }

    def select_step(self, edge_id: str, purpose: str) -> dict[str, Any]:
        if self.status != "ACTIVE" or not self.current_concept_id:
            return {"status":"TRAVERSAL_NOT_ACTIVE"}
        candidate = self.current_candidates.get(edge_id)
        if not candidate:
            return {
                "status":"UNOBSERVED_OR_UNSELECTABLE_EDGE",
                "edge_id":edge_id,
                "selectable_edge_ids":sorted(self.current_candidates),
            }
        if len(self.active_edge_ids) >= self.max_depth:
            return {
                "status":"TRAVERSAL_DEPTH_LIMIT_REACHED",
                "max_depth":self.max_depth,
                "backtrack_available":len(self.active_node_ids) > 1,
            }
        from_id = self.current_concept_id
        edge = self.graph.edges[edge_id]
        to_id = edge["to"] if edge["from"] == from_id else edge["from"]
        self.visited_concept_ids.add(to_id)
        self.active_edge_ids.append(edge_id)
        self.active_node_ids.append(to_id)
        self.current_concept_id = to_id
        step = {
            "step_index":len(self.selected_steps) + 1,
            "edge_id":edge_id,
            "from":from_id,
            "to":to_id,
            "direction":"OUTGOING" if edge["from"] == from_id else "INCOMING",
            "purpose":purpose,
            "source_refs":edge["source_refs"],
        }
        self.selected_steps.append(step)
        self.actions.append({"action":"SELECT_RELATION_STEP", **step})
        candidates = self._neighbor_candidates(to_id)
        self._persist()
        return {
            "status":"RELATION_STEP_SELECTED",
            "selected_step":step,
            "arrived_concept":self._concept_observation(to_id),
            "candidate_relations":candidates,
            "selectable_edge_ids":sorted(self.current_candidates),
            "anchors_connected_by_selected_steps":self._anchors_connected(),
            "visited_concept_ids":sorted(self.visited_concept_ids),
            "active_path":{"node_ids":list(self.active_node_ids), "edge_ids":list(self.active_edge_ids)},
            "full_path_precomputed":False,
        }

    def backtrack(self, purpose: str) -> dict[str, Any]:
        if self.status != "ACTIVE" or len(self.active_node_ids) <= 1:
            return {"status":"BACKTRACK_UNAVAILABLE"}
        from_id = self.active_node_ids.pop()
        via_edge_id = self.active_edge_ids.pop()
        to_id = self.active_node_ids[-1]
        self.current_concept_id = to_id
        self.actions.append({
            "action":"BACKTRACK_RELATION_STEP",
            "from":from_id,
            "to":to_id,
            "via_edge_id":via_edge_id,
            "purpose":purpose,
        })
        candidates = self._neighbor_candidates(to_id)
        self._persist()
        return {
            "status":"RELATION_STEP_BACKTRACKED",
            "from":from_id,
            "to":to_id,
            "via_edge_id":via_edge_id,
            "candidate_relations":candidates,
            "selectable_edge_ids":sorted(self.current_candidates),
            "visited_concept_ids":sorted(self.visited_concept_ids),
            "active_path":{"node_ids":list(self.active_node_ids), "edge_ids":list(self.active_edge_ids)},
            "full_path_precomputed":False,
        }

    def stop(self, reason: str) -> dict[str, Any]:
        if self.status != "ACTIVE":
            return {"status":"TRAVERSAL_NOT_ACTIVE"}
        connected = self._anchors_connected()
        if not connected:
            self.status = "STOPPED"
            self.stop_reason = "ANCHORS_NOT_CONNECTED_BY_AGENT_SELECTED_STEPS"
            self.actions.append({
                "action":"STOP_RELATION_TRAVERSAL",
                "reason":reason,
                "result":"INCOMPLETE",
            })
            self._persist()
            return {
                "status":"STOP",
                "error":self.stop_reason,
                "selected_edge_ids":self.selected_edge_ids,
                "visited_concept_ids":sorted(self.visited_concept_ids),
            }
        self.status = "COMPLETED"
        self.stop_reason = reason
        self.actions.append({
            "action":"STOP_RELATION_TRAVERSAL",
            "reason":reason,
            "result":"COMPLETED",
        })
        if len(self.anchors) == 2:
            baseline = self.graph.shortest_path(self.anchors[0], self.anchors[1], bidirectional=True)
            self.post_hoc_validation = {
                "performed_after_agent_traversal":True,
                "algorithm_role":"POST_HOC_VALIDATION_ONLY",
                "baseline_status":baseline.get("status"),
                "baseline_edge_count":len(baseline.get("edge_ids", [])),
                "agent_active_path_edge_count":len(self.active_edge_ids),
                "selection_history_edge_count":len(self.selected_edge_ids),
                "agent_path_is_shortest":len(self.active_edge_ids) == len(baseline.get("edge_ids", [])),
            }
        self._persist()
        return {
            "status":"RELATION_TRAVERSAL_COMPLETED",
            "reason":reason,
            "traversal":self.skill_provenance(),
            "post_hoc_validation":self.post_hoc_validation,
        }

    def skill_provenance(self) -> dict[str, Any] | None:
        if not self.completed:
            return None
        core = {
            "status":self.status,
            "anchor_ids":self.anchors,
            "active_path":{"node_ids":list(self.active_node_ids), "edge_ids":list(self.active_edge_ids)},
            "selected_steps":list(self.selected_steps),
            "selected_edge_ids":self.selected_edge_ids,
            "visited_concept_ids":sorted(self.visited_concept_ids),
            "stop_reason":self.stop_reason,
            "full_path_precomputed_for_agent":False,
            "candidate_order_policy":"RUN_SALTED_SHA256",
        }
        return {**core, "traversal_hash":_hash(core)}

    def snapshot(self) -> dict[str, Any]:
        skill_provenance = self.skill_provenance()
        core = {
            "object_type":"RelationTraversalLedger",
            "status":self.status,
            "anchor_ids":self.anchors,
            "current_concept_id":self.current_concept_id,
            "active_path":{"node_ids":list(self.active_node_ids), "edge_ids":list(self.active_edge_ids)},
            "selected_steps":list(self.selected_steps),
            "selected_edge_ids":self.selected_edge_ids,
            "actions":list(self.actions),
            "visited_concept_ids":sorted(self.visited_concept_ids),
            "stop_reason":self.stop_reason,
            "post_hoc_validation":self.post_hoc_validation,
            "max_depth":self.max_depth,
            "candidate_order_policy":"RUN_SALTED_SHA256",
            "full_path_precomputed_for_agent":False,
            "skill_provenance_hash":skill_provenance.get("traversal_hash") if skill_provenance else None,
        }
        return {**core, "ledger_hash":_hash(core)}

    def _persist(self) -> None:
        target = self.run_dir / "relation_traversal.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.snapshot(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
