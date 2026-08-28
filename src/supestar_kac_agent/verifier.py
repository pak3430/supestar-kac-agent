from __future__ import annotations

from collections import deque
import re
from typing import Any

from .graph import KnowledgeGraph


_KOREAN_SUFFIXES = (
    "하였습니다", "했습니다", "분류됩니다", "해당합니다", "아닙니다",
    "합니다", "됩니다", "입니다", "하면서", "하는", "하고", "하며", "하에",
    "에서", "에게", "부터", "까지", "보다", "처럼", "으로",
    "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "와", "과", "로", "한",
)


def _stem_token(token: str) -> str:
    if not re.fullmatch(r"[가-힣]+", token):
        return token
    for suffix in _KOREAN_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[:-len(suffix)]
    return token


def _tokens(value: str) -> set[str]:
    return {
        _stem_token(token.casefold())
        for token in re.findall(r"[0-9A-Za-z가-힣]+", value)
        if len(token) >= 2
    }


def _scalar_texts(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [text for item in value.values() for text in _scalar_texts(item)]
    if isinstance(value, list):
        return [text for item in value for text in _scalar_texts(item)]
    return [str(value)] if value is not None else []


def _evidence_text(item: dict[str, Any], graph: KnowledgeGraph) -> str:
    concept = item.get("concept") if isinstance(item.get("concept"), dict) else {}
    output = item.get("output") if isinstance(item.get("output"), dict) else {}
    parts = [
        str(item.get("answer", "")), str(item.get("reason", "")), str(item.get("relation", "")),
        str(concept.get("label_ko", "")), str(concept.get("definition", "")),
        " ".join(str(alias) for alias in concept.get("aliases", [])),
        str(output.get("answer", "")),
        " ".join(str(row.get("reason", "")) for row in output.get("reason_per_edge", []) if isinstance(row, dict)),
        " ".join(_scalar_texts(item.get("input_snapshot", {}))),
        " ".join(_scalar_texts(output.get("rule_trace", []))),
    ]
    for endpoint in (item.get("from"), item.get("to")):
        if endpoint in graph.nodes:
            node = graph.nodes[endpoint]
            parts.extend([node["label_ko"], node["definition"], " ".join(node.get("aliases", []))])
    return " ".join(parts)


def _claim_has_grounding_overlap(claim: dict[str, Any], evidence: dict[str, dict[str, Any]], graph: KnowledgeGraph) -> bool:
    claim_tokens = _tokens(str(claim.get("text", "")))
    if not claim_tokens:
        return False
    ground_tokens: set[str] = set()
    for evidence_id in claim.get("evidence_ids", []):
        item = evidence.get(str(evidence_id))
        if item:
            ground_tokens.update(_tokens(_evidence_text(item, graph)))
    return bool(ground_tokens) and len(claim_tokens & ground_tokens) / len(claim_tokens) >= 0.18


def _evidence_covers_concept(evidence_id: str, concept_id: str, evidence: dict[str, dict[str, Any]]) -> bool:
    item = evidence.get(evidence_id, {})
    if evidence_id == f"concept:{concept_id}":
        return True
    if item.get("from") == concept_id or item.get("to") == concept_id:
        return True
    output = item.get("output") if isinstance(item.get("output"), dict) else {}
    if concept_id in output.get("ordered_nodes", []):
        return True
    if str(output.get("candidate_scope", "")).casefold() == concept_id.casefold():
        return True
    for row in output.get("concept_rows", []):
        if isinstance(row, dict) and str(row.get("concept", "")).casefold() == concept_id.casefold():
            return True
    return False


def anchors_connected(anchors: list[str], observed_edges: set[str], graph: KnowledgeGraph) -> bool:
    if len(anchors) < 2:
        return True
    adjacency: dict[str, set[str]] = {}
    for edge_id in observed_edges:
        edge = graph.edges.get(edge_id)
        if not edge:
            continue
        adjacency.setdefault(edge["from"], set()).add(edge["to"])
        adjacency.setdefault(edge["to"], set()).add(edge["from"])
    start, targets = anchors[0], set(anchors[1:])
    queue, visited = deque([start]), {start}
    while queue:
        current = queue.popleft()
        for neighbor in adjacency.get(current, set()):
            if neighbor in targets:
                targets.remove(neighbor)
                if not targets:
                    return True
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return not targets


def verify_candidate(
    candidate: dict[str, Any],
    *,
    anchors: list[str],
    evidence: dict[str, dict[str, Any]],
    skill_runs: list[dict[str, Any]],
    graph: KnowledgeGraph,
) -> dict[str, Any]:
    missing: list[str] = []
    unsupported: list[str] = []
    answer = str(candidate.get("answer", "")).strip()
    claims = candidate.get("claims")
    claims = claims if isinstance(claims, list) else []
    if not answer:
        missing.append("answer")
    if not claims:
        missing.append("claims")
    observed_concepts = {item_id.split(":", 1)[1] for item_id in evidence if item_id.startswith("concept:")}
    observed_edges = {item_id for item_id in evidence if item_id.startswith("edge:")}
    for anchor in anchors:
        if anchor not in observed_concepts:
            missing.append(f"anchor_observation:{anchor}")
    if len(anchors) >= 2 and not anchors_connected(anchors, observed_edges, graph):
        missing.append("observed_relation_path_between_anchors")
    if not skill_runs:
        missing.append("executed_kac_skill")
    cited: set[str] = set()
    uncovered_claim_concepts: dict[str, list[str]] = {}
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict) or not str(claim.get("text", "")).strip():
            missing.append(f"claim_text:{index}")
            continue
        claim_text = str(claim.get("text", ""))
        if len(re.findall(r"[가-힣]", claim_text)) < 4 or re.search(r"[\u4e00-\u9fff]", claim_text):
            missing.append(f"claim_language_korean_required:{index}")
        ids = claim.get("evidence_ids")
        ids = ids if isinstance(ids, list) else []
        if not ids:
            missing.append(f"claim_evidence:{index}")
        for evidence_id in ids:
            cited.add(str(evidence_id))
            if evidence_id not in evidence:
                unsupported.append(str(evidence_id))
        if ids and not _claim_has_grounding_overlap(claim, evidence, graph):
            missing.append(f"claim_grounding_overlap:{index}")
        mentioned_concepts = graph.anchor_ids(claim_text)
        uncovered = [
            concept_id
            for concept_id in mentioned_concepts
            if not any(_evidence_covers_concept(str(evidence_id), concept_id, evidence) for evidence_id in ids)
        ]
        if uncovered:
            uncovered_claim_concepts[str(index)] = uncovered
            missing.extend(f"claim_uncovered_concept:{index}:{concept_id}" for concept_id in uncovered)
    lowered = answer.casefold().replace(" ", "")
    forbidden = []
    if "ccm은직접배출" in lowered or "ccm은scope1" in lowered:
        forbidden.append("CCM_DIRECT_EMISSIONS_CONFUSION")
    if "vcm은간접배출" in lowered or "vcm은scope" in lowered:
        forbidden.append("VCM_INDIRECT_EMISSIONS_CONFUSION")
    if "탄소크레딧은배출권" in lowered:
        forbidden.append("CREDIT_ALLOWANCE_CONFLATION")
    source_refs = sorted({ref for evidence_id in cited for ref in evidence.get(evidence_id, {}).get("source_refs", [])})
    if cited and not source_refs:
        missing.append("cited_source_refs")
    executed_skill_run_ids = {run["skill_run_id"] for run in skill_runs}
    cited_skill_run_ids = {
        evidence.get(evidence_id, {}).get("skill_run_id")
        for evidence_id in cited
        if evidence.get(evidence_id, {}).get("skill_run_id")
    }
    if skill_runs and not (executed_skill_run_ids & cited_skill_run_ids):
        missing.append("cited_executed_skill_output")
    verdict = "PASS" if not missing and not unsupported and not forbidden else "REVIEW"
    verified_answer = " ".join(
        str(claim.get("text", "")).strip()
        for claim in claims
        if isinstance(claim, dict) and str(claim.get("text", "")).strip()
    ) if verdict == "PASS" else None
    repair_evidence_by_concept = {
        concept_id: sorted(
            evidence_id
            for evidence_id in evidence
            if _evidence_covers_concept(evidence_id, concept_id, evidence)
        )
        for concept_ids in uncovered_claim_concepts.values()
        for concept_id in concept_ids
    }
    return {
        "verdict": verdict,
        "missing_requirements": sorted(set(missing)),
        "unsupported_evidence_ids": sorted(set(unsupported)),
        "forbidden_confusions": forbidden,
        "uncovered_claim_concepts": uncovered_claim_concepts,
        "repair_evidence_by_concept": repair_evidence_by_concept,
        "anchor_ids": anchors,
        "observed_concept_ids": sorted(observed_concepts),
        "observed_edge_ids": sorted(observed_edges),
        "executed_skill_names": [run["skill_name"] for run in skill_runs],
        "cited_skill_run_ids": sorted(cited_skill_run_ids),
        "cited_evidence_ids": sorted(cited),
        "source_refs": source_refs,
        "verified_answer": verified_answer,
    }
