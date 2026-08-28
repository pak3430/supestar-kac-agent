from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from pathlib import Path
from typing import Any

from .policy import project_root


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", value.casefold())


class KnowledgeGraph:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or project_root()).resolve()
        self.data = json.loads((self.root / "knowledge" / "graph.json").read_text(encoding="utf-8"))
        source_data = json.loads((self.root / "knowledge" / "source_registry.json").read_text(encoding="utf-8"))
        import_data = json.loads((self.root / "provenance" / "import_manifest.json").read_text(encoding="utf-8"))
        self.sources = {item["id"]: item for item in source_data["sources"]}
        self.active_skills = set(import_data["active_skill_names"])
        self.nodes = {item["id"]: item for item in self.data["nodes"]}
        self.edges = {item["id"]: item for item in self.data["edges"]}
        self.aliases: dict[str, str] = {}
        self._validate()

    def _validate(self) -> None:
        if len(self.nodes) != len(self.data["nodes"]):
            raise ValueError("duplicate graph node id")
        if len(self.edges) != len(self.data["edges"]):
            raise ValueError("duplicate graph edge id")
        for node in self.nodes.values():
            aliases = [node["id"], node["label_ko"], *node.get("aliases", [])]
            for alias in aliases:
                normalized = _normalize(alias)
                existing = self.aliases.get(normalized)
                if existing and existing != node["id"]:
                    raise ValueError(f"ambiguous graph alias: {alias}")
                self.aliases[normalized] = node["id"]
            self._validate_sources(node)
            unknown_skills = set(node.get("applicable_skills", [])) - self.active_skills
            if unknown_skills:
                raise ValueError(f"node references inactive skills: {sorted(unknown_skills)}")
        for edge in self.edges.values():
            if edge["from"] not in self.nodes or edge["to"] not in self.nodes:
                raise ValueError(f"edge endpoint missing: {edge['id']}")
            self._validate_sources(edge)

    def _validate_sources(self, value: dict[str, Any]) -> None:
        refs = value.get("source_refs", [])
        if not refs:
            raise ValueError(f"source refs missing: {value.get('id')}")
        for ref in refs:
            source = self.sources.get(ref)
            if not source or source.get("status") not in {"VERIFIED", "VERIFIED_LOCAL"}:
                raise ValueError(f"source is not admitted: {ref}")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical(self.data).encode("utf-8")).hexdigest()

    def resolve(self, query: str) -> str | None:
        return self.aliases.get(_normalize(query))

    def anchor_ids(self, question: str) -> list[str]:
        normalized_question = _normalize(question)
        matches: list[tuple[int, str]] = []
        for alias, node_id in self.aliases.items():
            if len(alias) >= 2 and alias in normalized_question:
                matches.append((len(alias), node_id))
        matches.sort(reverse=True)
        selected: list[str] = []
        for _, node_id in matches:
            if node_id not in selected:
                selected.append(node_id)
        return selected[:6]

    def observe(self, query: str) -> dict[str, Any]:
        node_id = self.resolve(query)
        if not node_id:
            return {
                "status": "UNKNOWN_CONCEPT",
                "query": query,
                "known_concept_ids": sorted(self.nodes),
                "source_refs": [],
            }
        node = self.nodes[node_id]
        return {
            "status": "OBSERVED",
            "evidence_id": f"concept:{node_id}",
            "concept": node,
            "relation_count": sum(
                1 for edge in self.edges.values() if edge["from"] == node_id or edge["to"] == node_id
            ),
            "graph_fingerprint": self.fingerprint,
            "source_refs": node["source_refs"],
        }

    def expand(self, query: str, toward_query: str | None = None) -> dict[str, Any]:
        node_id = self.resolve(query)
        if not node_id:
            return self.observe(query)
        related = self.neighbors(node_id)
        return {
            "status": "EXPANDED",
            "concept_id": node_id,
            "relations": related,
            "applicable_skills": self.nodes[node_id].get("applicable_skills", []),
            "graph_fingerprint": self.fingerprint,
            "source_refs": sorted({ref for item in related for ref in item["source_refs"]}),
        }

    def neighbors(self, query: str, *, ordering_salt: str = "") -> list[dict[str, Any]]:
        """Return only the directly adjacent relations for one concept.

        This method deliberately never computes or returns a path to another
        anchor. A run-specific salt changes presentation order without changing
        the admitted relation set, which makes first-item routing detectable.
        """
        node_id = self.resolve(query)
        if not node_id:
            return []
        related = []
        for edge in self.edges.values():
            if edge["from"] == node_id:
                neighbor_id, direction = edge["to"], "OUTGOING"
            elif edge["to"] == node_id:
                neighbor_id, direction = edge["from"], "INCOMING"
            else:
                continue
            related.append({
                "evidence_id": edge["id"],
                "direction": direction,
                "relation": edge["relation"],
                "reason": edge["reason"],
                "from": edge["from"],
                "to": edge["to"],
                "neighbor": {"id": neighbor_id, "label_ko": self.nodes[neighbor_id]["label_ko"]},
                "source_refs": edge["source_refs"],
            })
        related.sort(key=lambda item: hashlib.sha256(
            f"{ordering_salt}:{item['evidence_id']}".encode("utf-8")
        ).hexdigest())
        return related

    def shortest_path(self, start_query: str, end_query: str, *, bidirectional: bool = False) -> dict[str, Any]:
        start, end = self.resolve(start_query), self.resolve(end_query)
        if not start or not end:
            return {"status": "UNKNOWN_ENDPOINT", "start": start_query, "end": end_query}
        queue = deque([(start, [])])
        visited = {start}
        while queue:
            current, path = queue.popleft()
            if current == end:
                node_ids = [start]
                for edge_id in path:
                    edge = self.edges[edge_id]
                    node_ids.append(edge["to"] if edge["from"] == node_ids[-1] else edge["from"])
                return {
                    "status": "PATH_FOUND",
                    "from": start,
                    "to": end,
                    "node_ids": node_ids,
                    "edge_ids": path,
                    "edges": [self.edges[edge_id] for edge_id in path],
                    "source_refs": sorted({ref for edge_id in path for ref in self.edges[edge_id]["source_refs"]}),
                }
            for edge in self.edges.values():
                neighbor = None
                if edge["from"] == current:
                    neighbor = edge["to"]
                elif bidirectional and edge["to"] == current:
                    neighbor = edge["from"]
                if neighbor and neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, [*path, edge["id"]]))
        return {"status": "NO_PATH", "from": start, "to": end}
