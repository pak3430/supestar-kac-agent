from __future__ import annotations

from collections import deque
import re
from typing import Any

from .graph import KnowledgeGraph


_KOREAN_SUFFIXES = (
    "하였습니다", "했습니다", "분류됩니다", "해당합니다", "아닙니다",
    "합니다", "됩니다", "입니다", "하면서", "하는", "하고", "하며", "하에",
    "에서", "에게", "부터", "까지", "보다", "처럼", "으로",
    "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "와", "과", "로", "한", "할",
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


def _token_matches(claim_token: str, ground_token: str) -> bool:
    if claim_token == ground_token:
        return True
    if (
        re.fullmatch(r"[가-힣]+", claim_token)
        and re.fullmatch(r"[가-힣]+", ground_token)
        and min(len(claim_token), len(ground_token)) >= 2
    ):
        return claim_token in ground_token or ground_token in claim_token
    return False


def _claim_has_grounding_overlap(claim: dict[str, Any], evidence: dict[str, dict[str, Any]], graph: KnowledgeGraph) -> bool:
    claim_tokens = _tokens(str(claim.get("text", "")))
    if not claim_tokens:
        return False
    ground_tokens: set[str] = set()
    for evidence_id in claim.get("evidence_ids", []):
        item = evidence.get(str(evidence_id))
        if item:
            ground_tokens.update(_tokens(_evidence_text(item, graph)))
    matched_claim_tokens = {
        claim_token
        for claim_token in claim_tokens
        if any(_token_matches(claim_token, ground_token) for ground_token in ground_tokens)
    }
    return bool(ground_tokens) and len(matched_claim_tokens) / len(claim_tokens) >= 0.18


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


def observed_path_edge_ids(
    anchors: list[str],
    observed_edges: set[str],
    graph: KnowledgeGraph,
) -> list[str]:
    if len(anchors) < 2:
        return []
    adjacency: dict[str, set[str]] = {}
    edge_by_pair: dict[frozenset[str], str] = {}
    for edge_id in observed_edges:
        edge = graph.edges.get(edge_id)
        if not edge:
            continue
        adjacency.setdefault(edge["from"], set()).add(edge["to"])
        adjacency.setdefault(edge["to"], set()).add(edge["from"])
        edge_by_pair[frozenset((edge["from"], edge["to"]))] = edge_id
    start = anchors[0]
    combined: list[str] = []
    for target in anchors[1:]:
        queue = deque([start])
        previous: dict[str, str | None] = {start:None}
        while queue and target not in previous:
            current = queue.popleft()
            for neighbor in sorted(adjacency.get(current, set())):
                if neighbor not in previous:
                    previous[neighbor] = current
                    queue.append(neighbor)
        if target not in previous:
            return []
        nodes = [target]
        while nodes[-1] != start:
            parent = previous[nodes[-1]]
            if parent is None:
                return []
            nodes.append(parent)
        nodes.reverse()
        for left, right in zip(nodes, nodes[1:]):
            edge_id = edge_by_pair[frozenset((left, right))]
            if edge_id not in combined:
                combined.append(edge_id)
    return combined


def anchors_connected(anchors: list[str], observed_edges: set[str], graph: KnowledgeGraph) -> bool:
    return len(anchors) < 2 or bool(observed_path_edge_ids(anchors, observed_edges, graph))


def verify_candidate(
    candidate: dict[str, Any],
    *,
    anchors: list[str],
    evidence: dict[str, dict[str, Any]],
    skill_runs: list[dict[str, Any]],
    graph: KnowledgeGraph,
    traversal: dict[str, Any] | None = None,
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
    traversal = traversal or {}
    selected_traversal_edges = {
        str(edge_id) for edge_id in traversal.get("active_path", {}).get("edge_ids", [])
        if str(edge_id) in graph.edges
    }
    for anchor in anchors:
        if anchor not in observed_concepts:
            missing.append(f"anchor_observation:{anchor}")
    if len(anchors) >= 2:
        if traversal.get("status") != "COMPLETED":
            missing.append("completed_agentic_relation_traversal")
        if not anchors_connected(anchors, selected_traversal_edges, graph):
            missing.append("agent_selected_relation_path_between_anchors")
    if not skill_runs:
        missing.append("executed_kac_skill")
    cited: set[str] = set()
    claimed_concepts: set[str] = set()
    uncovered_claim_concepts: dict[str, list[str]] = {}
    repair_relation_evidence_by_claim: dict[str, list[str]] = {}
    claim_question_anchor_sets: list[set[str]] = []
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
        claimed_concepts.update(mentioned_concepts)
        mentioned_question_anchors = [anchor for anchor in anchors if anchor in mentioned_concepts]
        claim_question_anchor_sets.append(set(mentioned_question_anchors))
        if len(mentioned_question_anchors) >= 2:
            cited_edges = {str(evidence_id) for evidence_id in ids if str(evidence_id).startswith("edge:")}
            if not anchors_connected(mentioned_question_anchors, cited_edges, graph):
                missing.append(f"claim_relation_path:{index}")
                repair_path = observed_path_edge_ids(mentioned_question_anchors, selected_traversal_edges, graph)
                if repair_path:
                    repair_relation_evidence_by_claim[str(index)] = repair_path
        uncovered = [
            concept_id
            for concept_id in mentioned_concepts
            if not any(_evidence_covers_concept(str(evidence_id), concept_id, evidence) for evidence_id in ids)
        ]
        if uncovered:
            uncovered_claim_concepts[str(index)] = uncovered
            missing.extend(f"claim_uncovered_concept:{index}:{concept_id}" for concept_id in uncovered)
    for anchor in anchors:
        if anchor not in claimed_concepts:
            missing.append(f"anchor_claim_coverage:{anchor}")
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
    if len(anchors) >= 2:
        if not any(set(anchors).issubset(anchor_set) for anchor_set in claim_question_anchor_sets):
            missing.append("relationship_claim_covering_all_question_anchors")
        cited_selected_edges = {
            evidence_id for evidence_id in cited
            if evidence_id in selected_traversal_edges
        }
        if not anchors_connected(anchors, cited_selected_edges, graph):
            missing.append("answer_cited_full_agent_relation_path")
    if len(anchors) >= 2 and skill_runs:
        expected_traversal_hash = traversal.get("skill_provenance_hash")
        if not expected_traversal_hash or not any(
            run.get("traversal_hash") == expected_traversal_hash
            for run in skill_runs
        ):
            missing.append("skill_run_missing_agent_traversal_provenance")
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
        "repair_relation_evidence_by_claim": repair_relation_evidence_by_claim,
        "anchor_ids": anchors,
        "observed_concept_ids": sorted(observed_concepts),
        "observed_edge_ids": sorted(observed_edges),
        "agent_selected_edge_ids": sorted(selected_traversal_edges),
        "relation_traversal_status":traversal.get("status", "NOT_AVAILABLE"),
        "executed_skill_names": [run["skill_name"] for run in skill_runs],
        "cited_skill_run_ids": sorted(cited_skill_run_ids),
        "cited_evidence_ids": sorted(cited),
        "source_refs": source_refs,
        "verified_answer": verified_answer,
    }
