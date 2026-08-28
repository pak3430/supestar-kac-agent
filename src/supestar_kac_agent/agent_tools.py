from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .graph import KnowledgeGraph
from .skill_compiler import skill_catalog
from .skill_runtime import execute_skill


class ToolEnvironment:
    def __init__(self, *, root: Path, run_dir: Path) -> None:
        self.root = root
        self.run_dir = run_dir
        self.graph = KnowledgeGraph(root)
        self.catalog = skill_catalog(root)
        self.catalog_by_name = {item["name"]: item for item in self.catalog}
        self.evidence: dict[str, dict[str, Any]] = {}
        self.skill_runs: list[dict[str, Any]] = []
        self.skill_runs_by_signature: dict[str, dict[str, Any]] = {}
        self.relation_expansions_by_signature: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _skill_signature(skill_name: str, inputs: dict[str, Any]) -> str:
        return json.dumps(
            {"skill_name":skill_name, "inputs":inputs},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _concept_choice_values(self, concept_ids: list[str]) -> list[str]:
        values: set[str] = set()
        for concept_id in concept_ids:
            node = self.graph.nodes.get(concept_id, {})
            values.update([
                concept_id,
                str(node.get("label_ko", "")),
                *(str(alias) for alias in node.get("aliases", [])),
            ])
        return sorted(value for value in values if value)

    def definitions(
        self,
        *,
        allowed_tool_names: set[str] | None = None,
        concept_choices: list[str] | None = None,
        relation_endpoint_choices: list[str] | None = None,
        skill_choices: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        requested_skill_names = self.catalog_by_name if skill_choices is None else skill_choices
        skill_names = sorted(set(requested_skill_names) & set(self.catalog_by_name))
        concept_schema: dict[str, Any] = {"type":"string"}
        if concept_choices:
            concept_schema["enum"] = self._concept_choice_values(concept_choices)
        relation_endpoint_schema: dict[str, Any] = {"type":"string"}
        if relation_endpoint_choices:
            relation_endpoint_schema["enum"] = self._concept_choice_values(relation_endpoint_choices)
        evidence_id_items: dict[str, Any] = {"type":"string"}
        if self.evidence:
            evidence_id_items["enum"] = sorted(self.evidence)
        definitions = [
            {"type":"function","function":{"name":"observe_concept","description":"CCS에서 아직 관찰하지 않은 필수 anchor의 정의·경계·출처와 적용 가능한 KAC Skill 계약을 관찰한다.","parameters":{"type":"object","properties":{"concept":concept_schema},"required":["concept"]}}},
            {"type":"function","function":{"name":"expand_relations","description":"관찰된 anchor 사이의 실제 CCS 연결 경로를 관찰한다. concept와 toward_concept는 서로 다른 anchor를 선택한다.","parameters":{"type":"object","properties":{"concept":relation_endpoint_schema,"toward_concept":relation_endpoint_schema,"purpose":{"type":"string"}},"required":["concept","toward_concept","purpose"]}}},
            {"type":"function","function":{"name":"invoke_kac_skill","description":"관찰된 필요에 따라 등록된 원자 KAC Skill을 실제 실행한다. 필수 입력은 Skill catalog를 따른다.","parameters":{"type":"object","properties":{"skill_name":{"type":"string","enum":skill_names},"inputs":{"type":"object"}},"required":["skill_name","inputs"]}}},
            {"type":"function","function":{"name":"request_missing_evidence","description":"판정에 필요한 사용자·기관 근거가 없을 때 보완 질문을 만든다.","parameters":{"type":"object","properties":{"missing_items":{"type":"array","items":{"type":"string"}},"question":{"type":"string"}},"required":["missing_items","question"]}}},
            {"type":"function","function":{"name":"submit_answer_candidate","description":"충분한 관찰과 Skill 실행 뒤 최종 답변 후보를 제출한다. 각 claim은 실제 Observation evidence_id를 enum에서 정확히 선택해야 한다.","parameters":{"type":"object","properties":{"answer":{"type":"string"},"claims":{"type":"array","items":{"type":"object","properties":{"text":{"type":"string"},"evidence_ids":{"type":"array","minItems":1,"items":evidence_id_items}},"required":["text","evidence_ids"]}}},"required":["answer","claims"]}}},
        ]
        if allowed_tool_names is not None:
            definitions = [
                item for item in definitions
                if item["function"]["name"] in allowed_tool_names
            ]
        return definitions

    def evidence_catalog(self) -> list[dict[str, Any]]:
        catalog = []
        for evidence_id in sorted(self.evidence):
            item = self.evidence[evidence_id]
            if evidence_id.startswith("skill:skill-run-"):
                stable_id = f"skill:{item.get('skill_name', '')}:latest"
                if stable_id in self.evidence:
                    continue
            concept = item.get("concept") if isinstance(item.get("concept"), dict) else {}
            output = item.get("output") if isinstance(item.get("output"), dict) else {}
            if evidence_id.startswith("concept:"):
                grounding = f"{concept.get('label_ko', '')}: {concept.get('definition', '')}"
            elif evidence_id.startswith("edge:"):
                grounding = (
                    f"{item.get('from', '')} --{item.get('relation', '')}--> {item.get('to', '')}: "
                    f"{item.get('reason', '')}"
                )
            else:
                grounding = (
                    f"Skill {item.get('skill_name', '')} / {item.get('verdict', '')}: "
                    f"{item.get('answer', '')}; ordered_nodes={output.get('ordered_nodes', [])}; "
                    f"concept_rows={output.get('concept_rows', [])}"
                )
            catalog_item = {
                "evidence_id": evidence_id,
                "grounding": grounding,
                "source_refs": item.get("source_refs", []),
            }
            if evidence_id.startswith("skill:"):
                catalog_item["authoritative_skill_output"] = {
                    key: output.get(key)
                    for key in (
                        "verdict", "answer", "candidate_scope", "missing_evidence",
                        "rule_trace", "ordered_nodes", "concept_rows",
                    )
                    if key in output
                }
            catalog.append(catalog_item)
        return catalog

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "observe_concept":
            resolved = self.graph.resolve(str(arguments.get("concept", "")))
            if resolved and f"concept:{resolved}" in self.evidence:
                existing = self.evidence[f"concept:{resolved}"]
                return {
                    **existing,
                    "status":"REUSED_EXISTING_CONCEPT_OBSERVATION",
                    "reason":"CONCEPT_ALREADY_OBSERVED",
                    "new_observation":False,
                }
            result = self.graph.observe(str(arguments.get("concept", "")))
            if result.get("status") == "OBSERVED":
                item = result["concept"]
                result["skill_contracts"] = [self.catalog_by_name[name] for name in item.get("applicable_skills", [])]
                self.evidence[result["evidence_id"]] = result
                result["new_observation"] = True
            return result
        if tool_name == "expand_relations":
            concept = str(arguments.get("concept", ""))
            toward_concept = str(arguments.get("toward_concept", ""))
            resolved_concept = self.graph.resolve(concept)
            resolved_toward = self.graph.resolve(toward_concept)
            if resolved_concept and resolved_concept == resolved_toward:
                return {
                    "status":"INVALID_RELATION_ENDPOINTS",
                    "reason":"DISTINCT_CONCEPTS_REQUIRED",
                    "concept":resolved_concept,
                    "toward_concept":resolved_toward,
                    "new_observation":False,
                }
            signature = json.dumps(
                {
                    "concept":resolved_concept or concept,
                    "toward_concept":resolved_toward or toward_concept,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            existing = self.relation_expansions_by_signature.get(signature)
            if existing:
                return {
                    **existing,
                    "status":"REUSED_EXISTING_RELATION_EXPANSION",
                    "reason":"RELATION_EXPANSION_ALREADY_OBSERVED",
                    "new_observation":False,
                }
            result = self.graph.expand(
                concept,
                toward_concept or None,
            )
            for relation in result.get("relations", []):
                self.evidence[relation["evidence_id"]] = relation
            path = result.get("candidate_path", {})
            for edge in path.get("edges", []):
                self.evidence[edge["id"]] = {"evidence_id":edge["id"], **edge}
            if result.get("status") == "EXPANDED":
                result["new_observation"] = True
                self.relation_expansions_by_signature[signature] = result
            return result
        if tool_name == "invoke_kac_skill":
            skill_name = str(arguments.get("skill_name", ""))
            inputs = arguments.get("inputs") if isinstance(arguments.get("inputs"), dict) else {}
            signature = self._skill_signature(skill_name, inputs)
            existing = self.skill_runs_by_signature.get(signature)
            if existing:
                return {
                    "status":"REUSED_EXISTING_SKILL_RUN",
                    "reason":"IDENTICAL_SKILL_AND_INPUT_ALREADY_EXECUTED",
                    "skill_run":existing,
                    "evidence_id":f"skill:{existing['skill_run_id']}",
                    "stable_evidence_id":f"skill:{existing['skill_name']}:latest",
                    "new_execution":False,
                }
            result = execute_skill(
                skill_name,
                inputs,
                root=self.root,
                agent_run_dir=self.run_dir,
            )
            if result.get("status") == "EXECUTED":
                run = result["skill_run"]
                self.skill_runs.append(run)
                self.skill_runs_by_signature[signature] = run
                evidence_id = f"skill:{run['skill_run_id']}"
                self.evidence[evidence_id] = {
                    "evidence_id": evidence_id,
                    "skill_run_id": run["skill_run_id"],
                    "skill_name": run["skill_name"],
                    "verdict": run["output"]["verdict"],
                    "answer": run["output"]["answer"],
                    "input_snapshot": run["input_snapshot"],
                    "output": run["output"],
                    "source_refs": run["output"]["evidence_refs"],
                }
                stable_evidence_id = f"skill:{run['skill_name']}:latest"
                self.evidence[stable_evidence_id] = {
                    **self.evidence[evidence_id],
                    "evidence_id": stable_evidence_id,
                }
                # A Skill observation may expose graph edges in its contract output.
                # Promote only edge IDs that both appear in that output and exist in
                # the admitted CCS graph; this is observed runtime evidence, not a
                # question-specific route or an invented relation.
                for item in run["output"].get("reason_per_edge", []):
                    edge_id = item.get("edge_id") if isinstance(item, dict) else None
                    edge = self.graph.edges.get(str(edge_id))
                    if edge:
                        self.evidence[edge["id"]] = {
                            "evidence_id": edge["id"],
                            **edge,
                            "observed_via_skill_run_id": run["skill_run_id"],
                        }
                result["evidence_id"] = evidence_id
                result["stable_evidence_id"] = stable_evidence_id
                result["promoted_edge_evidence_ids"] = sorted(
                    item_id
                    for item_id, item in self.evidence.items()
                    if item.get("observed_via_skill_run_id") == run["skill_run_id"]
                )
                result["new_execution"] = True
            return result
        if tool_name == "request_missing_evidence":
            return {
                "status":"USER_EVIDENCE_REQUIRED",
                "missing_items":arguments.get("missing_items", []),
                "question":arguments.get("question", ""),
                "external_action":False,
            }
        if tool_name == "submit_answer_candidate":
            return {"status":"CANDIDATE_RECEIVED","candidate":arguments}
        return {"status":"STOP","error":"UNREGISTERED_TOOL","allowed_tools":[item["function"]["name"] for item in self.definitions()]}
