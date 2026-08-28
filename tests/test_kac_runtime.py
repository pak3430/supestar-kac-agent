from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from supestar_kac_agent.agent import (
    _bind_trusted_skill_context,
    _lifecycle_gate,
    _normalize_model_tool_arguments,
    _normalize_executed_skill_evidence_ids,
    _repair_observed_concept_citations,
    run_agent,
)
from supestar_kac_agent.agent_tools import ToolEnvironment
from supestar_kac_agent.graph import KnowledgeGraph
from supestar_kac_agent.skill_compiler import compile_skills, skill_catalog
from supestar_kac_agent.skill_runtime import execute_skill
from supestar_kac_agent.verifier import verify_candidate


ROOT = Path(__file__).resolve().parents[1]


def tool_call(name: str, arguments: dict) -> dict:
    return {"function": {"name": name, "arguments": arguments}}


def complete_traversal(environment: ToolEnvironment, start: str, edge_ids: list[str]) -> None:
    observed = environment.execute("observe_neighbors", {
        "concept":start,
        "purpose":"관계 탐색 시작",
    })
    if observed["status"] not in {"NEIGHBORS_OBSERVED", "REUSED_EXISTING_NEIGHBOR_OBSERVATION"}:
        raise AssertionError(observed)
    for edge_id in edge_ids:
        selected = environment.execute("select_relation_step", {
            "edge_id":edge_id,
            "purpose":"현재 1-hop Observation에서 질문과 관련된 관계 선택",
        })
        if selected["status"] != "RELATION_STEP_SELECTED":
            raise AssertionError(selected)
    stopped = environment.execute("stop_relation_traversal", {
        "reason":"질문의 anchor가 AI 선택 edge로 연결됨",
    })
    if stopped["status"] != "RELATION_TRAVERSAL_COMPLETED":
        raise AssertionError(stopped)


class ScriptedLocalQwen:
    """Deterministic stand-in used only to test the Agent loop contract."""

    def __init__(self) -> None:
        self.turn = 0

    def identity(self) -> dict:
        return {
            "provider": "OLLAMA_LOCAL_TEST_DOUBLE",
            "endpoint": "http://127.0.0.1:11434",
            "endpoint_scope": "LOOPBACK_ONLY",
            "ollama_version": "test",
            "model": "qwen2.5:test",
            "model_digest": "sha256:test",
            "family": "qwen2",
            "parameter_size": "test",
            "quantization_level": "test",
            "capabilities": ["completion", "tools"],
        }

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        scripted = [
            [
                tool_call("observe_concept", {"concept": "ESG"}),
                tool_call("observe_concept", {"concept": "탄소크레딧"}),
            ],
            [tool_call("observe_neighbors", {
                "concept":"ESG",
                "purpose":"ESG에서 질문과 관련된 1-hop 관계부터 관찰",
            })],
            [tool_call("select_relation_step", {"edge_id":"edge:esg:sdgs", "purpose":"기후행동 체계로 연결"})],
            [tool_call("select_relation_step", {"edge_id":"edge:sdgs:13", "purpose":"탄소 질문과 직접 관련된 기후행동 선택"})],
            [tool_call("select_relation_step", {"edge_id":"edge:forest:sdg13", "purpose":"기후행동과 산림탄소의 실제 관계 선택"})],
            [tool_call("select_relation_step", {"edge_id":"edge:forest:credit", "purpose":"산림탄소에서 탄소크레딧으로 연결"})],
            [tool_call("stop_relation_traversal", {"reason":"ESG와 탄소크레딧 anchor가 선택 edge로 연결됨"})],
            [tool_call("invoke_kac_skill", {
                "skill_name": "carbon-market-unit-comparison",
                "inputs": {
                    "question": "ESG 관점에서 탄소크레딧과 어떤 상관관계가 있습니까?",
                    "purpose": "LEARNING",
                    "asOfDate": "2026-08-27",
                },
            })],
            [tool_call("submit_answer_candidate", {
                "answer": "ESG는 조직 책임을 운영에 반영하는 관점이다. 탄소크레딧은 검증된 감축·제거 성과의 단위다. ESG 행동은 기후행동과 산림탄소사업을 거쳐 탄소크레딧과 연결해 살필 수 있다.",
                "claims": [
                    {"text": "ESG는 조직 책임을 운영에 반영하는 관점이다.", "evidence_ids": ["concept:ESG"]},
                    {"text": "탄소크레딧은 검증된 감축·제거 성과의 단위다.", "evidence_ids": ["concept:CARBON_CREDIT"]},
                    {"text": "ESG 행동은 기후행동과 산림탄소사업을 거쳐 탄소크레딧과 연결해 살필 수 있다.", "evidence_ids": ["edge:esg:sdgs", "edge:sdgs:13", "edge:forest:sdg13", "edge:forest:credit", "skill:carbon-market-unit-comparison:latest"]},
                ],
            })],
        ]
        calls = scripted[self.turn]
        self.turn += 1
        return {
            "message": {"role": "assistant", "content": "", "tool_calls": calls},
            "metrics": {"client_elapsed_ms": 1.0, "done_reason": "stop"},
        }


class StructuredFallbackQwen:
    def __init__(self) -> None:
        self.turn = 0
        self.structured = False

    def identity(self) -> dict:
        return ScriptedLocalQwen().identity()

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        turns = [
            [
                tool_call("observe_concept", {"concept": "ESG"}),
                tool_call("observe_concept", {"concept": "탄소크레딧"}),
            ],
            [tool_call("observe_neighbors", {"concept":"ESG", "purpose":"관계 탐색 시작"})],
            [tool_call("select_relation_step", {"edge_id":"edge:esg:sdgs", "purpose":"기후행동 체계 선택"})],
            [tool_call("select_relation_step", {"edge_id":"edge:sdgs:13", "purpose":"SDG 13 선택"})],
            [tool_call("select_relation_step", {"edge_id":"edge:forest:sdg13", "purpose":"산림탄소 선택"})],
            [tool_call("select_relation_step", {"edge_id":"edge:forest:credit", "purpose":"탄소크레딧 연결"})],
            [tool_call("stop_relation_traversal", {"reason":"anchor 연결 완료"})],
            [tool_call("invoke_kac_skill", {
                "skill_name": "carbon-market-unit-comparison",
                "inputs": {"question": "ESG와 탄소크레딧 관계", "purpose": "LEARNING", "asOfDate": "2026-08-27"},
            })],
            [],
        ]
        calls = turns[self.turn]
        self.turn += 1
        return {
            "message": {"role": "assistant", "content": "관찰 결과를 설명하는 자연어 초안", "tool_calls": calls},
            "metrics": {"client_elapsed_ms": 1.0, "done_reason": "stop"},
        }

    def structure_candidate(self, **kwargs: object) -> dict:
        self.structured = True
        return {
            "candidate": {
                "answer": "검증 전 초안",
                "claims": [
                    {"text": "ESG는 조직 책임을 운영에 반영하는 관점이다.", "evidence_ids": ["concept:ESG"]},
                    {"text": "탄소크레딧은 검증된 감축·제거 성과의 단위다.", "evidence_ids": ["concept:CARBON_CREDIT"]},
                    {"text": "ESG 행동은 기후행동과 산림탄소사업을 거쳐 탄소크레딧과 연결해 살필 수 있다.", "evidence_ids": ["edge:esg:sdgs", "edge:sdgs:13", "edge:forest:sdg13", "edge:forest:credit", "skill:carbon-market-unit-comparison:latest"]},
                ],
            },
            "metrics": {"phase": "candidate_structuring", "client_elapsed_ms": 1.0},
        }


class ActionRecoveryQwen:
    def __init__(self) -> None:
        self.turn = 0
        self.recovered = False

    def identity(self) -> dict:
        return ScriptedLocalQwen().identity()

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        turns = [
            [tool_call("observe_concept", {"concept": "OPERATIONAL_BOUNDARY"})],
            [],
            [tool_call("submit_answer_candidate", {
                "answer": "외부에서 구매해 소비한 전기는 Scope 2 후보입니다.",
                "claims": [{
                    "text": "외부에서 구매해 소비한 전기는 Scope 2 후보입니다.",
                    "evidence_ids": ["skill:scope-activity-classification:latest"],
                }],
            })],
        ]
        calls = turns[self.turn]
        self.turn += 1
        return {
            "message": {"role": "assistant", "content": "Scope 2입니다." if not calls else "", "tool_calls": calls},
            "metrics": {"client_elapsed_ms": 1.0, "done_reason": "stop"},
        }

    def select_tool_action(self, **kwargs: object) -> dict:
        self.recovered = True
        allowed = kwargs["allowed_tools"]
        self.asserted_tool_names = [item["function"]["name"] for item in allowed]
        return {
            "action": {
                "tool_name": "invoke_kac_skill",
                "arguments": {
                    "skill_name": "scope-activity-classification",
                    "inputs": {
                        "activity_description": "외부 전력회사에서 구매한 전기를 사무실에서 사용",
                        "organization_boundary": "회사 조직 경계 안의 사무실",
                        "source_ownership_or_control": "UNKNOWN",
                        "purchased_energy_type": "ELECTRICITY",
                        "value_chain_relation": "UNKNOWN",
                    },
                },
            },
            "metrics": {"phase": "tool_action_recovery", "client_elapsed_ms": 1.0},
        }


class CitationLoopQwen:
    def __init__(self) -> None:
        self.allowed_tool_history: list[list[str]] = []

    def identity(self) -> dict:
        return ScriptedLocalQwen().identity()

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        allowed = [item["function"]["name"] for item in tools]
        self.allowed_tool_history.append(allowed)
        selected = allowed[0]
        if selected == "observe_concept":
            call = tool_call(selected, {"concept":"OPERATIONAL_BOUNDARY"})
        elif selected == "invoke_kac_skill":
            call = tool_call(selected, {
                "skill_name":"scope-activity-classification",
                "inputs":{
                    "activity_description":"외부 전력회사에서 구매한 전기를 사무실에서 사용",
                    "organization_boundary":"회사 조직 경계 안의 사무실",
                    "source_ownership_or_control":"UNKNOWN",
                    "purchased_energy_type":"ELECTRICITY",
                    "value_chain_relation":"UNKNOWN",
                },
            })
        else:
            call = tool_call("submit_answer_candidate", {
                "answer":"CCM은 Scope 1입니다.",
                "claims":[{
                    "text":"CCM은 Scope 1입니다.",
                    "evidence_ids":["skill:scope-activity-classification:latest"],
                }],
            })
        return {
            "message":{"role":"assistant", "content":"", "tool_calls":[call]},
            "metrics":{"client_elapsed_ms":1.0, "done_reason":"stop"},
        }


class GateViolationQwen:
    def identity(self) -> dict:
        return ScriptedLocalQwen().identity()

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        return {
            "message":{
                "role":"assistant",
                "content":"",
                "tool_calls":[tool_call("invoke_kac_skill", {
                    "skill_name":"scope-activity-classification",
                    "inputs":{},
                })],
            },
            "metrics":{"client_elapsed_ms":1.0, "done_reason":"stop"},
        }

class KACRuntimeTests(unittest.TestCase):
    def test_local_model_edge_object_field_is_normalized_only_for_exact_allowed_enum(self) -> None:
        definition = {
            "parameters":{
                "properties":{
                    "edge_id":{"enum":["edge:credit:vcm", "edge:forest:credit"]},
                },
            },
        }
        normalized, changes = _normalize_model_tool_arguments(
            "select_relation_step",
            {"object":"edge:forest:credit", "purpose":"산림탄소 관계 선택"},
            definition,
        )
        self.assertEqual(normalized, {
            "edge_id":"edge:forest:credit",
            "purpose":"산림탄소 관계 선택",
        })
        self.assertEqual(changes[0]["value"], "edge:forest:credit")
        rejected, changes = _normalize_model_tool_arguments(
            "select_relation_step",
            {"object":"edge:not-observed", "purpose":"없는 관계"},
            definition,
        )
        self.assertNotIn("edge_id", rejected)
        self.assertEqual(changes, [])

    def test_agentic_traversal_exposes_one_hop_and_defers_shortest_path_until_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(
                root=ROOT,
                run_dir=Path(directory),
                anchors=["ESG", "CARBON_CREDIT"],
            )
            with patch.object(
                environment.graph,
                "shortest_path",
                wraps=environment.graph.shortest_path,
            ) as shortest_path:
                observed = environment.execute("observe_neighbors", {
                    "concept":"ESG",
                    "purpose":"현재 node의 직접 관계만 관찰",
                })
                neighbor_ids = {
                    item["neighbor"]["id"]
                    for item in observed["candidate_relations"]
                }
                self.assertEqual(neighbor_ids, {
                    "ESG_MANAGEMENT",
                    "ENVIRONMENTAL_PILLAR",
                    "SOCIAL_PILLAR",
                    "GOVERNANCE_PILLAR",
                    "SDGS",
                })
                self.assertNotIn("CARBON_CREDIT", neighbor_ids)
                self.assertFalse(observed["full_path_precomputed"])
                for edge_id in [
                    "edge:esg:sdgs",
                    "edge:sdgs:13",
                    "edge:forest:sdg13",
                    "edge:forest:credit",
                ]:
                    selected = environment.execute("select_relation_step", {
                        "edge_id":edge_id,
                        "purpose":"현재 Observation에서 다음 관계 선택",
                    })
                    self.assertEqual(selected["status"], "RELATION_STEP_SELECTED")
                    self.assertFalse(selected["full_path_precomputed"])
                self.assertEqual(shortest_path.call_count, 0)
                stopped = environment.execute("stop_relation_traversal", {
                    "reason":"질문 anchor 연결 완료",
                })
                self.assertEqual(stopped["status"], "RELATION_TRAVERSAL_COMPLETED")
                self.assertEqual(shortest_path.call_count, 1)
                self.assertEqual(
                    stopped["post_hoc_validation"]["algorithm_role"],
                    "POST_HOC_VALIDATION_ONLY",
                )

    def test_relation_candidate_order_is_run_salted_not_semantic_priority(self) -> None:
        graph = KnowledgeGraph(ROOT)
        first = [item["evidence_id"] for item in graph.neighbors("ESG", ordering_salt="run-a")]
        second = [item["evidence_id"] for item in graph.neighbors("ESG", ordering_salt="run-b")]
        self.assertEqual(set(first), set(second))
        self.assertNotEqual(first, second)

    def test_reverse_traversal_is_built_from_qwen_selected_edges(self) -> None:
        anchors = ["CARBON_CREDIT", "ESG"]
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(root=ROOT, run_dir=Path(directory), anchors=anchors)
            complete_traversal(environment, "CARBON_CREDIT", [
                "edge:forest:credit",
                "edge:forest:sdg13",
                "edge:sdgs:13",
                "edge:esg:sdgs",
            ])
            snapshot = environment.traversal.snapshot()
            self.assertEqual(snapshot["status"], "COMPLETED")
            self.assertEqual(snapshot["active_path"]["node_ids"], [
                "CARBON_CREDIT",
                "FOREST_CARBON_PROJECT",
                "SDG_13",
                "SDGS",
                "ESG",
            ])
            self.assertTrue(all(
                environment.evidence[edge_id].get("observed_via_agent_traversal_step")
                for edge_id in snapshot["selected_edge_ids"]
            ))

    def test_removed_or_unobserved_edge_cannot_be_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(
                root=ROOT,
                run_dir=Path(directory),
                anchors=["ESG", "CARBON_CREDIT"],
            )
            environment.graph.edges.pop("edge:esg:sdgs")
            observed = environment.execute("observe_neighbors", {
                "concept":"ESG",
                "purpose":"edge 제거 환경 검증",
            })
            self.assertNotIn("edge:esg:sdgs", observed["selectable_edge_ids"])
            rejected = environment.execute("select_relation_step", {
                "edge_id":"edge:esg:sdgs",
                "purpose":"존재하지 않는 edge 선택 시도",
            })
            self.assertEqual(rejected["status"], "UNOBSERVED_OR_UNSELECTABLE_EDGE")
            self.assertNotIn("edge:esg:sdgs", environment.evidence)

    def test_backtrack_preserves_audit_history_and_allows_an_alternate_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(
                root=ROOT,
                run_dir=Path(directory),
                anchors=["ESG", "CARBON_CREDIT"],
            )
            environment.execute("observe_neighbors", {"concept":"ESG", "purpose":"탐색 시작"})
            environment.execute("select_relation_step", {
                "edge_id":"edge:esg:environment",
                "purpose":"환경 축 후보 확인",
            })
            backtracked = environment.execute("backtrack_relation_step", {
                "purpose":"환경 축에서 탄소크레딧으로 이어지는 관찰 관계가 없어 복귀",
            })
            self.assertEqual(backtracked["status"], "RELATION_STEP_BACKTRACKED")
            self.assertEqual(backtracked["to"], "ESG")
            selected = environment.execute("select_relation_step", {
                "edge_id":"edge:esg:sdgs",
                "purpose":"기후행동 관계로 대안 선택",
            })
            self.assertEqual(selected["status"], "RELATION_STEP_SELECTED")
            actions = environment.traversal.snapshot()["actions"]
            self.assertTrue(any(item["action"] == "BACKTRACK_RELATION_STEP" for item in actions))
            self.assertEqual(environment.traversal.active_edge_ids, ["edge:esg:sdgs"])
            self.assertEqual(environment.traversal.selected_edge_ids, [
                "edge:esg:environment",
                "edge:esg:sdgs",
            ])

    def test_backtracked_edge_cannot_complete_or_ground_the_active_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(
                root=ROOT,
                run_dir=Path(directory),
                anchors=["ESG", "CARBON_CREDIT"],
            )
            environment.execute("observe_neighbors", {"concept":"ESG", "purpose":"탐색 시작"})
            for edge_id in [
                "edge:esg:sdgs",
                "edge:sdgs:13",
                "edge:forest:sdg13",
                "edge:forest:credit",
            ]:
                environment.execute("select_relation_step", {
                    "edge_id":edge_id,
                    "purpose":"질문의 anchor를 향한 실제 1-hop 선택",
                })
            self.assertTrue(environment.traversal.anchors_connected)
            environment.execute("backtrack_relation_step", {
                "purpose":"마지막 탄소크레딧 관계 선택 철회",
            })
            self.assertFalse(environment.traversal.anchors_connected)
            stopped = environment.execute("stop_relation_traversal", {
                "reason":"철회된 edge로 연결된 것처럼 종료 시도",
            })
            self.assertEqual(stopped["status"], "STOP")
            self.assertEqual(stopped["error"], "ANCHORS_NOT_CONNECTED_BY_AGENT_SELECTED_STEPS")

    def test_trusted_request_context_overrides_missing_or_model_guessed_skill_values(self) -> None:
        environment = ToolEnvironment(root=ROOT, run_dir=ROOT / "runs" / "unit-test-unused")
        bound, changes = _bind_trusted_skill_context(
            {
                "skill_name":"esg-carbon-action-path",
                "inputs":{
                    "question":"모델이 바꾼 질문",
                    "userRole":"REVIEWER",
                    "asOfDate":None,
                    "focus":"FOREST_CARBON",
                },
            },
            question="ESG 관점에서 탄소크레딧과 어떤 상관관계가 있습니까?",
            role="LEARNER",
            as_of_date="2026-08-28",
            catalog_by_name=environment.catalog_by_name,
        )
        self.assertEqual(bound["inputs"], {
            "question":"ESG 관점에서 탄소크레딧과 어떤 상관관계가 있습니까?",
            "userRole":"LEARNER",
            "asOfDate":"2026-08-28",
            "focus":"FOREST_CARBON",
        })
        self.assertEqual({item["field"] for item in changes}, {"question", "userRole", "asOfDate"})

    def test_relation_claim_requires_and_repairs_full_observed_anchor_path(self) -> None:
        graph = KnowledgeGraph(ROOT)
        anchors = ["ESG", "CARBON_CREDIT"]
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(root=ROOT, run_dir=Path(directory), anchors=anchors)
            for concept in anchors:
                environment.execute("observe_concept", {"concept":concept})
            complete_traversal(environment, "ESG", [
                "edge:esg:sdgs",
                "edge:sdgs:13",
                "edge:forest:sdg13",
                "edge:forest:credit",
            ])
            environment.execute("invoke_kac_skill", {
                "skill_name":"esg-carbon-action-path",
                "inputs":{
                    "question":"ESG 관점에서 탄소크레딧과 어떤 상관관계가 있습니까?",
                    "userRole":"LEARNER",
                    "asOfDate":"2026-08-28",
                    "focus":"FOREST_CARBON",
                    "measurementEvidence":[],
                },
            })
            candidate = {
                "answer":"ESG와 탄소크레딧은 기후행동과 산림탄소를 통해 연결됩니다.",
                "claims":[{
                    "text":"ESG와 탄소크레딧은 기후행동과 산림탄소를 통해 연결됩니다.",
                    "evidence_ids":[
                        "edge:esg:sdgs",
                        "edge:forest:credit",
                        "skill:esg-carbon-action-path:latest",
                    ],
                }],
            }
            verification = verify_candidate(
                candidate,
                anchors=anchors,
                evidence=environment.evidence,
                skill_runs=environment.skill_runs,
                graph=graph,
                traversal=environment.traversal.snapshot(),
            )
            self.assertIn("claim_relation_path:0", verification["missing_requirements"])
            self.assertEqual(verification["repair_relation_evidence_by_claim"]["0"], [
                "edge:esg:sdgs",
                "edge:sdgs:13",
                "edge:forest:sdg13",
                "edge:forest:credit",
            ])
            repaired = _repair_observed_concept_citations(candidate, verification)
            self.assertIsNotNone(repaired)
            repaired_verification = verify_candidate(
                repaired,
                anchors=anchors,
                evidence=environment.evidence,
                skill_runs=environment.skill_runs,
                graph=graph,
                traversal=environment.traversal.snapshot(),
            )
            self.assertEqual(repaired_verification["verdict"], "PASS")
            split_candidate = {
                "answer":"탄소크레딧은 산림탄소사업과 관련됩니다. ESG는 SDGs와 연결됩니다.",
                "claims":[
                    {
                        "text":"탄소크레딧은 산림탄소사업과 관련됩니다.",
                        "evidence_ids":["concept:CARBON_CREDIT", "edge:forest:credit"],
                    },
                    {
                        "text":"ESG는 SDGs와 연결됩니다.",
                        "evidence_ids":["concept:ESG", "edge:esg:sdgs", "skill:esg-carbon-action-path:latest"],
                    },
                ],
            }
            split_verification = verify_candidate(
                split_candidate,
                anchors=anchors,
                evidence=environment.evidence,
                skill_runs=environment.skill_runs,
                graph=graph,
                traversal=environment.traversal.snapshot(),
            )
            self.assertEqual(split_verification["verdict"], "REVIEW")
            self.assertIn(
                "relationship_claim_covering_all_question_anchors",
                split_verification["missing_requirements"],
            )
            self.assertIn(
                "answer_cited_full_agent_relation_path",
                split_verification["missing_requirements"],
            )
            leaked_candidate = {
                "answer":"ESG와 탄소크레딧은 연결됩니다. (edge:esg:sdgs)",
                "claims":[{
                    "text":"ESG와 탄소크레딧은 연결됩니다. (edge:esg:sdgs)",
                    "evidence_ids":[
                        "edge:esg:sdgs",
                        "edge:sdgs:13",
                        "edge:forest:sdg13",
                        "edge:forest:credit",
                        "skill:esg-carbon-action-path:latest",
                    ],
                }],
            }
            leaked_verification = verify_candidate(
                leaked_candidate,
                anchors=anchors,
                evidence=environment.evidence,
                skill_runs=environment.skill_runs,
                graph=graph,
                traversal=environment.traversal.snapshot(),
            )
            self.assertEqual(leaked_verification["verdict"], "REVIEW")
            self.assertIn(
                "claim_internal_evidence_id_leak:0",
                leaked_verification["missing_requirements"],
            )

    def test_verifier_rejects_preloaded_relation_edges_without_agent_traversal(self) -> None:
        graph = KnowledgeGraph(ROOT)
        anchors = ["ESG", "CARBON_CREDIT"]
        evidence = {
            "concept:ESG":graph.observe("ESG"),
            "concept:CARBON_CREDIT":graph.observe("CARBON_CREDIT"),
        }
        path = graph.shortest_path("ESG", "CARBON_CREDIT", bidirectional=True)
        for edge in path["edges"]:
            evidence[edge["id"]] = {"evidence_id":edge["id"], **edge}
        skill_result = execute_skill("carbon-market-unit-comparison", {
            "question":"ESG와 탄소크레딧 관계",
            "purpose":"LEARNING",
            "asOfDate":"2026-08-28",
        }, root=ROOT)
        run = skill_result["skill_run"]
        evidence["skill:carbon-market-unit-comparison:latest"] = {
            "evidence_id":"skill:carbon-market-unit-comparison:latest",
            "skill_run_id":run["skill_run_id"],
            "skill_name":run["skill_name"],
            "answer":run["output"]["answer"],
            "output":run["output"],
            "source_refs":run["output"]["evidence_refs"],
        }
        candidate = {
            "answer":"ESG는 기후행동과 산림탄소를 통해 탄소크레딧과 연결됩니다.",
            "claims":[{
                "text":"ESG는 기후행동과 산림탄소를 통해 탄소크레딧과 연결됩니다.",
                "evidence_ids":[
                    *path["edge_ids"],
                    "skill:carbon-market-unit-comparison:latest",
                ],
            }],
        }
        verification = verify_candidate(
            candidate,
            anchors=anchors,
            evidence=evidence,
            skill_runs=[run],
            graph=graph,
            traversal={"status":"NOT_STARTED", "selected_edge_ids":[]},
        )
        self.assertEqual(verification["verdict"], "REVIEW")
        self.assertIn("completed_agentic_relation_traversal", verification["missing_requirements"])
        self.assertIn("agent_selected_relation_path_between_anchors", verification["missing_requirements"])
        self.assertIn("skill_run_missing_agent_traversal_provenance", verification["missing_requirements"])

    def test_observed_skill_citation_can_repair_matching_uncovered_concept(self) -> None:
        candidate = {
            "answer":"산림탄소는 SDG 13과 연결됩니다.",
            "claims":[{
                "text":"산림탄소 프로젝트는 SDG 13과 연결됩니다.",
                "evidence_ids":["edge:forest:sdg13"],
            }],
        }
        repaired = _repair_observed_concept_citations(candidate, {
            "missing_requirements":[
                "cited_executed_skill_output",
                "claim_uncovered_concept:0:SDGS",
            ],
            "unsupported_evidence_ids":[],
            "forbidden_confusions":[],
            "repair_evidence_by_concept":{
                "SDGS":[
                    "edge:sdgs:13",
                    "skill:esg-carbon-action-path:latest",
                    "skill:skill-run-exact",
                ],
            },
        })
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired["claims"][0]["evidence_ids"], [
            "edge:forest:sdg13",
            "skill:esg-carbon-action-path:latest",
        ])

    def test_forest_carbon_focus_uses_grounded_bidirectional_relation_fallback(self) -> None:
        result = execute_skill("esg-carbon-action-path", {
            "question":"ESG와 산림탄소의 관계를 알려주세요.",
            "userRole":"LEARNER",
            "asOfDate":"2026-08-28",
            "focus":"FOREST_CARBON",
            "measurementEvidence":["측정 근거 확인"],
        }, root=ROOT)
        self.assertEqual(result["status"], "EXECUTED")
        output = result["skill_run"]["output"]
        self.assertEqual(output["verdict"], "PROCEED")
        self.assertEqual(output["ordered_nodes"][0], "ESG")
        self.assertEqual(output["ordered_nodes"][-1], "FOREST_CARBON_PROJECT")
        self.assertIn("BIDIRECTIONAL_GROUNDED_RELATION_FALLBACK", output["rule_trace"])

    def test_skill_handler_exception_is_contained_as_stop_observation(self) -> None:
        def broken_handler(payload: dict, graph: KnowledgeGraph) -> dict:
            raise RuntimeError("simulated handler failure")

        with patch.dict(
            "supestar_kac_agent.skill_runtime.HANDLERS",
            {"esg_carbon_action_path_v1":broken_handler},
        ):
            result = execute_skill("esg-carbon-action-path", {
                "question":"ESG란 무엇인가요?",
                "userRole":"LEARNER",
                "asOfDate":"2026-08-28",
            }, root=ROOT)
        self.assertEqual(result["status"], "STOP")
        self.assertEqual(result["error"], "SKILL_HANDLER_RUNTIME_ERROR")
        self.assertEqual(result["error_type"], "RuntimeError")

    def test_tool_outside_current_lifecycle_gate_is_not_executed(self) -> None:
        request = {
            "question":"이 배출원은 Scope 몇인가요?",
            "userRole":"LEARNER",
            "asOfDate":"2026-08-28",
        }
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            result = run_agent(
                request,
                root=ROOT,
                runs_root=temporary / "runs",
                db_path=temporary / "state.sqlite3",
                client=GateViolationQwen(),
            )
            self.assertEqual(result["status"], "STOP")
            self.assertEqual(result["skill_run_ids"], [])
            events = json.loads((Path(result["run_directory"]) / "events.json").read_text(encoding="utf-8"))
            rejected = [
                item for item in events
                if item["event_type"] == "observation"
                and item["status"] == "REJECTED_TOOL_OUTSIDE_LIFECYCLE_GATE"
            ]
            self.assertTrue(rejected)
            self.assertEqual(rejected[0]["observation"]["allowed_tool_names"], ["observe_concept"])

    def test_repeated_citation_error_never_reopens_skill_execution(self) -> None:
        request = {
            "question":"외부 전력회사에서 구매한 전기를 사용합니다. 이 활동은 Scope 몇인가요?",
            "userRole":"LEARNER",
            "asOfDate":"2026-08-28",
        }
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            client = CitationLoopQwen()
            result = run_agent(
                request,
                root=ROOT,
                runs_root=temporary / "runs",
                db_path=temporary / "state.sqlite3",
                client=client,
            )
            self.assertEqual(result["status"], "STOP")
            self.assertEqual(len(result["skill_run_ids"]), 1)
            self.assertEqual(client.allowed_tool_history.count(["invoke_kac_skill"]), 1)
            skill_gate_index = client.allowed_tool_history.index(["invoke_kac_skill"])
            self.assertTrue(all(
                tools == ["submit_answer_candidate"]
                for tools in client.allowed_tool_history[skill_gate_index + 1:]
            ))
            events = json.loads((Path(result["run_directory"]) / "events.json").read_text(encoding="utf-8"))
            self.assertTrue(any(item["event_type"] == "repeated_verification_blocked" for item in events))

    def test_observed_concept_repair_can_resolve_its_grounding_overlap_consequence(self) -> None:
        candidate = {
            "answer":"Scope 1입니다.",
            "claims":[{
                "text":"소유하고 통제하는 사업장의 직접 연소이므로 Scope 1입니다.",
                "evidence_ids":["skill:scope-activity-classification:latest"],
            }],
        }
        repaired = _repair_observed_concept_citations(candidate, {
            "missing_requirements":[
                "claim_grounding_overlap:0",
                "claim_uncovered_concept:0:OPERATIONAL_BOUNDARY",
            ],
            "unsupported_evidence_ids":[],
            "forbidden_confusions":[],
            "repair_evidence_by_concept":{
                "OPERATIONAL_BOUNDARY":["concept:OPERATIONAL_BOUNDARY"],
            },
        })
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired["claims"][0]["evidence_ids"], [
            "skill:scope-activity-classification:latest",
            "concept:OPERATIONAL_BOUNDARY",
        ])

    def test_single_claim_can_bind_the_one_executed_skill_citation(self) -> None:
        candidate = {
            "answer":"ESG는 조직의 환경·사회·지배구조 책임을 운영에 반영하는 관점입니다.",
            "claims":[{
                "text":"ESG는 조직의 환경·사회·지배구조 책임을 운영에 반영하는 관점입니다.",
                "evidence_ids":["concept:ESG"],
            }],
        }
        repaired = _repair_observed_concept_citations(candidate, {
            "missing_requirements":["cited_executed_skill_output"],
            "unsupported_evidence_ids":[],
            "forbidden_confusions":[],
            "repair_evidence_by_concept":{},
            "repair_relation_evidence_by_claim":{},
            "executed_skill_names":["esg-carbon-action-path"],
        })
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired["claims"][0]["evidence_ids"], [
            "concept:ESG",
            "skill:esg-carbon-action-path:latest",
        ])

    def test_lifecycle_gate_advances_without_reopening_completed_stages(self) -> None:
        graph = KnowledgeGraph(ROOT)
        anchors = ["OPERATIONAL_BOUNDARY", "ACTIVITY_DATA"]
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(root=ROOT, run_dir=Path(directory), anchors=anchors)
            gate, definitions, state = _lifecycle_gate(environment, anchors, graph)
            self.assertEqual(gate, "OBSERVE_REQUIRED_ANCHORS")
            self.assertEqual(definitions[0]["function"]["name"], "observe_concept")
            initial_choices = definitions[0]["function"]["parameters"]["properties"]["concept"]["enum"]
            self.assertEqual({graph.resolve(choice) for choice in initial_choices}, set(anchors))
            environment.execute("observe_concept", {"concept":"OPERATIONAL_BOUNDARY"})
            gate, definitions, state = _lifecycle_gate(environment, anchors, graph)
            self.assertEqual(gate, "OBSERVE_REQUIRED_ANCHORS")
            remaining_choices = definitions[0]["function"]["parameters"]["properties"]["concept"]["enum"]
            self.assertEqual({graph.resolve(choice) for choice in remaining_choices}, {"ACTIVITY_DATA"})
            environment.execute("observe_concept", {"concept":"ACTIVITY_DATA"})
            gate, definitions, state = _lifecycle_gate(environment, anchors, graph)
            self.assertEqual(gate, "START_AGENTIC_RELATION_TRAVERSAL")
            self.assertEqual(definitions[0]["function"]["name"], "observe_neighbors")
            environment.execute("observe_neighbors", {
                "concept":"OPERATIONAL_BOUNDARY",
                "purpose":"필수 anchor 관계의 1-hop 후보 관찰",
            })
            gate, definitions, state = _lifecycle_gate(environment, anchors, graph)
            self.assertEqual(gate, "ADVANCE_AGENTIC_RELATION_TRAVERSAL")
            self.assertIn("select_relation_step", [item["function"]["name"] for item in definitions])
            environment.execute("select_relation_step", {
                "edge_id":"edge:scopes:activity",
                "purpose":"운영 경계와 활동자료를 직접 연결",
            })
            gate, definitions, state = _lifecycle_gate(environment, anchors, graph)
            self.assertEqual(gate, "COMPLETE_AGENTIC_RELATION_TRAVERSAL")
            environment.execute("stop_relation_traversal", {"reason":"필수 anchor 연결 완료"})
            gate, definitions, state = _lifecycle_gate(environment, anchors, graph)
            self.assertEqual(gate, "EXECUTE_KAC_SKILL")
            self.assertEqual(definitions[0]["function"]["name"], "invoke_kac_skill")

    def test_neighbor_candidates_are_not_evidence_until_qwen_selects_an_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(
                root=ROOT,
                run_dir=Path(directory),
                anchors=["ESG", "CARBON_CREDIT"],
            )
            first_concept = environment.execute("observe_concept", {"concept":"ESG"})
            second_concept = environment.execute("observe_concept", {"concept":"ESG"})
            self.assertEqual(first_concept["status"], "OBSERVED")
            self.assertEqual(second_concept["status"], "REUSED_EXISTING_CONCEPT_OBSERVATION")
            observed = environment.execute("observe_neighbors", {
                "concept":"ESG",
                "purpose":"1-hop 후보 관찰",
            })
            self.assertEqual(observed["status"], "NEIGHBORS_OBSERVED")
            self.assertFalse(any(item.startswith("edge:") for item in environment.evidence))
            selected = environment.execute("select_relation_step", {
                "edge_id":"edge:esg:sdgs",
                "purpose":"질문과 관련된 기후행동 관계 선택",
            })
            self.assertEqual(selected["status"], "RELATION_STEP_SELECTED")
            self.assertEqual(
                [item for item in environment.evidence if item.startswith("edge:")],
                ["edge:esg:sdgs"],
            )

    def test_natural_korean_scope_claim_is_grounded_by_skill_input_and_observed_concept(self) -> None:
        graph = KnowledgeGraph(ROOT)
        anchors = ["OPERATIONAL_BOUNDARY", "ACTIVITY_DATA"]
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(root=ROOT, run_dir=Path(directory), anchors=anchors)
            for concept in anchors:
                environment.execute("observe_concept", {"concept":concept})
            complete_traversal(environment, "OPERATIONAL_BOUNDARY", ["edge:scopes:activity"])
            environment.execute("invoke_kac_skill", {
                "skill_name":"scope-activity-classification",
                "inputs":{
                    "activity_description":"우리 회사가 소유하고 직접 운영·통제하는 사업장 보일러에서 도시가스를 연소합니다.",
                    "organization_boundary":"OWNED_CONTROLLED",
                    "source_ownership_or_control":"OWNED_CONTROLLED",
                    "purchased_energy_type":"NONE",
                    "value_chain_relation":"NONE",
                },
            })
            candidate = {
                "answer":"Scope 1입니다.",
                "claims":[{
                    "text":"제시된 활동자료에서 배출원이 우리 회사의 소유와 통제하에 있으며 직접 연소한 도시가스이므로 Scope 1으로 분류됩니다.",
                    "evidence_ids":[
                        "skill:scope-activity-classification:latest",
                        "concept:OPERATIONAL_BOUNDARY",
                        "concept:ACTIVITY_DATA",
                        "edge:scopes:activity",
                    ],
                }],
            }
            verification = verify_candidate(
                candidate,
                anchors=anchors,
                evidence=environment.evidence,
                skill_runs=environment.skill_runs,
                graph=graph,
                traversal=environment.traversal.snapshot(),
            )
            self.assertEqual(verification["verdict"], "PASS")

    def test_verifier_requires_every_question_anchor_to_appear_in_claims(self) -> None:
        graph = KnowledgeGraph(ROOT)
        anchors = ["CARBON_CREDIT", "ESG"]
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(root=ROOT, run_dir=Path(directory), anchors=anchors)
            for concept in anchors:
                environment.execute("observe_concept", {"concept":concept})
            complete_traversal(environment, "ESG", [
                "edge:esg:sdgs",
                "edge:sdgs:13",
                "edge:forest:sdg13",
                "edge:forest:credit",
            ])
            environment.execute("invoke_kac_skill", {
                "skill_name":"carbon-market-unit-comparison",
                "inputs":{
                    "question":"ESG와 탄소크레딧의 관계",
                    "purpose":"LEARNING",
                    "asOfDate":"2026-08-28",
                },
            })
            candidate = {
                "answer":"ESG 설명만 포함합니다.",
                "claims":[{
                    "text":"ESG는 조직 책임을 운영에 반영하는 관점입니다.",
                    "evidence_ids":["concept:ESG", "skill:carbon-market-unit-comparison:latest"],
                }],
            }
            verification = verify_candidate(
                candidate,
                anchors=anchors,
                evidence=environment.evidence,
                skill_runs=environment.skill_runs,
                graph=graph,
                traversal=environment.traversal.snapshot(),
            )
            self.assertEqual(verification["verdict"], "REVIEW")
            self.assertIn("anchor_claim_coverage:CARBON_CREDIT", verification["missing_requirements"])

    def test_missing_skill_namespace_is_normalized_only_for_exact_observed_run(self) -> None:
        evidence = {
            "skill:skill-run-exact": {"skill_run_id":"skill-run-exact"},
            "concept:ESG": {"evidence_id":"concept:ESG"},
        }
        candidate, replacements = _normalize_executed_skill_evidence_ids({
            "answer":"답변",
            "claims":[{"text":"검증 문장입니다.","evidence_ids":["skill-run-exact","skill-run-unknown","concept:ESG"]}],
        }, evidence)
        self.assertEqual(candidate["claims"][0]["evidence_ids"], [
            "skill:skill-run-exact", "skill-run-unknown", "concept:ESG",
        ])
        self.assertEqual(replacements, [{"from":"skill-run-exact","to":"skill:skill-run-exact"}])

    def test_submit_schema_uses_observed_evidence_enum_and_duplicate_skill_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(root=ROOT, run_dir=Path(directory))
            environment.execute("observe_concept", {"concept":"OPERATIONAL_BOUNDARY"})
            inputs = {
                "activity_description":"소유 사업장 보일러 도시가스 연소",
                "organization_boundary":"회사 경계",
                "source_ownership_or_control":"OWNED_CONTROLLED",
                "purchased_energy_type":"NONE",
                "value_chain_relation":"NONE",
            }
            first = environment.execute("invoke_kac_skill", {
                "skill_name":"scope-activity-classification", "inputs":inputs,
            })
            second = environment.execute("invoke_kac_skill", {
                "skill_name":"scope-activity-classification", "inputs":inputs,
            })
            self.assertEqual(first["status"], "EXECUTED")
            self.assertEqual(second["status"], "REUSED_EXISTING_SKILL_RUN")
            self.assertEqual(len(environment.skill_runs), 1)
            self.assertEqual(len(list((Path(directory) / "skills").glob("*.json"))), 1)
            submit = next(item for item in environment.definitions() if item["function"]["name"] == "submit_answer_candidate")
            enum = submit["function"]["parameters"]["properties"]["claims"]["items"]["properties"]["evidence_ids"]["items"]["enum"]
            self.assertEqual(enum, sorted(environment.evidence))

    def test_graph_is_source_linked_and_resolves_relation_path(self) -> None:
        graph = KnowledgeGraph(ROOT)
        self.assertEqual(graph.resolve("탄소크레딧"), "CARBON_CREDIT")
        self.assertEqual(graph.anchor_ids("ESG와 탄소크레딧의 관계"), ["CARBON_CREDIT", "ESG"])
        self.assertIn("OPERATIONAL_BOUNDARY", graph.anchor_ids("이 배출원은 Scope 몇인가요?"))
        path = graph.shortest_path("ESG", "탄소크레딧", bidirectional=True)
        self.assertEqual(path["status"], "PATH_FOUND")
        self.assertTrue(path["source_refs"])

    def test_imported_seven_node_contracts_compile(self) -> None:
        compiled = compile_skills(ROOT)
        self.assertEqual(compiled["status"], "PASS")
        self.assertEqual(compiled["skill_count"], 6)
        self.assertEqual(len({item["handler"] for item in compiled["chains"]}), 6)
        catalog = {item["name"]: item for item in skill_catalog(ROOT)}
        self.assertIn("MARKET", catalog["esg-carbon-action-path"]["input_guidance"]["focus"])
        self.assertTrue(catalog["scope-activity-classification"]["method_rules"])

    def test_all_six_atomic_skills_execute_against_contracts(self) -> None:
        cases = {
            "esg-carbon-action-path": {
                "question": "ESG 탄소 측정 경로를 알려주세요", "userRole": "LEARNER",
                "asOfDate": "2026-08-27", "focus": "MEASUREMENT",
            },
            "scope-activity-classification": {
                "activity_description": "소유 사업장 보일러 도시가스 연소", "organization_boundary": "본사 사업장",
                "source_ownership_or_control": "OWNED_CONTROLLED", "purchased_energy_type": "NONE",
                "value_chain_relation": "NONE",
            },
            "carbon-market-unit-comparison": {
                "question": "탄소크레딧의 의미", "purpose": "LEARNING", "asOfDate": "2026-08-27",
            },
            "forest-esg-impact-mapping": {
                "projectSummary": "산림탄소사업", "asOfDate": "2026-08-27",
                "environmentEvidence": ["모니터링"], "socialEvidence": ["권리 확인"],
                "governanceEvidence": ["검증 기록"],
            },
            "forest-carbon-procedure-guidance": {
                "projectType": "FOREST", "currentStage": "PLANNING", "intendedUse": "LEARNING",
                "asOfDate": "2026-08-27", "availableDocuments": [],
            },
            "forest-carbon-transaction-readiness": {
                "gates": {f"G{number}": {"state": "PRESENT"} for number in range(1, 12)},
            },
        }
        for name, payload in cases.items():
            with self.subTest(skill=name):
                result = execute_skill(name, payload, root=ROOT)
                self.assertEqual(result["status"], "EXECUTED")
                output = result["skill_run"]["output"]
                self.assertIn(output["verdict"], {"PROCEED", "REVIEW", "STOP"})
                self.assertTrue(output["evidence_refs"])

    def test_scope_sum_guard_does_not_trigger_on_unrelated_scope_3_text(self) -> None:
        result = execute_skill("scope-activity-classification", {
            "activity_description": "Scope 1, Scope 2, Scope 3의 정의를 각각 검토",
            "organization_boundary": "본사",
            "source_ownership_or_control": "OWNED_CONTROLLED",
            "purchased_energy_type": "NONE",
            "value_chain_relation": "NONE",
        }, root=ROOT)
        output = result["skill_run"]["output"]
        self.assertNotIn("SCOPE3_NOT_SUM", output["rule_trace"])

    def test_owned_gas_boiler_cannot_be_mislabeled_as_purchased_steam(self) -> None:
        result = execute_skill("scope-activity-classification", {
            "activity_description": "소유 사업장 보일러에서 도시가스 연소",
            "organization_boundary": "본사 사업장",
            "source_ownership_or_control": "OWNED_CONTROLLED",
            "purchased_energy_type": "STEAM",
            "value_chain_relation": "NONE",
        }, root=ROOT)
        output = result["skill_run"]["output"]
        self.assertEqual(output["verdict"], "REVIEW")
        self.assertIsNone(output["candidate_scope"])
        self.assertIn("OWNED_FUEL_COMBUSTION_CONFLICTS_WITH_PURCHASED_ENERGY_TYPE", output["rule_trace"])

    def test_owned_fuel_combustion_does_not_require_value_chain_relation(self) -> None:
        result = execute_skill("scope-activity-classification", {
            "activity_description": "소유 사업장 보일러에서 도시가스 연소",
            "organization_boundary": "본사 사업장",
            "source_ownership_or_control": "OWNED_CONTROLLED",
            "purchased_energy_type": "NONE",
            "value_chain_relation": "UNKNOWN",
        }, root=ROOT)
        output = result["skill_run"]["output"]
        self.assertEqual(output["verdict"], "PROCEED")
        self.assertEqual(output["candidate_scope"], "SCOPE_1")

    def test_agent_loop_requires_observation_relation_skill_and_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            result = run_agent(
                json.loads((ROOT / "tests/fixtures/esg_carbon_credit.json").read_text(encoding="utf-8")),
                root=ROOT,
                runs_root=temporary / "runs",
                db_path=temporary / "state.sqlite3",
                client=ScriptedLocalQwen(),
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["verification"]["verdict"], "PASS")
            self.assertEqual(result["skills_invoked"], ["carbon-market-unit-comparison"])
            self.assertTrue(result["local_llm_verified"])
            self.assertFalse(result["internet_used"])
            self.assertFalse(result["question_specific_route_map_used"])
            self.assertFalse(result["full_path_precomputed_for_agent"])
            self.assertEqual(result["pathfinder_role"], "POST_HOC_VALIDATION_ONLY")
            self.assertEqual(result["relation_traversal"]["status"], "COMPLETED")
            self.assertEqual(result["agent_selected_relation_path"]["active_path"]["edge_ids"], [
                "edge:esg:sdgs",
                "edge:sdgs:13",
                "edge:forest:sdg13",
                "edge:forest:credit",
            ])
            manifest = Path(result["run_directory"]) / "run_manifest.json"
            self.assertTrue(manifest.exists())
            traversal_file = Path(result["run_directory"]) / "relation_traversal.json"
            self.assertTrue(traversal_file.exists())
            skill_files = list((Path(result["run_directory"]) / "skills").glob("*.json"))
            self.assertTrue(skill_files)
            skill_run = json.loads(skill_files[0].read_text(encoding="utf-8"))
            self.assertEqual(
                skill_run["traversal_hash"],
                result["relation_traversal"]["skill_provenance_hash"],
            )
            events = json.loads((Path(result["run_directory"]) / "events.json").read_text(encoding="utf-8"))
            self.assertEqual(
                len([event for event in events if event["event_type"] == "relation_step_selected"]),
                4,
            )

    def test_registered_tools_exclude_full_path_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(root=ROOT, run_dir=Path(directory))
            tool_names = {
                definition["function"]["name"]
                for definition in environment.definitions()
            }
            self.assertNotIn("expand_relations", tool_names)
            self.assertTrue({
                "observe_neighbors",
                "select_relation_step",
                "backtrack_relation_step",
                "stop_relation_traversal",
            }.issubset(tool_names))

    def test_natural_language_fallback_is_structured_by_same_local_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            client = StructuredFallbackQwen()
            result = run_agent(
                json.loads((ROOT / "tests/fixtures/esg_carbon_credit.json").read_text(encoding="utf-8")),
                root=ROOT,
                runs_root=temporary / "runs",
                db_path=temporary / "state.sqlite3",
                client=client,
            )
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(client.structured)
            self.assertEqual(result["stop_reason"], "STRUCTURED_ANSWER_CANDIDATE_VERIFIED")
            self.assertNotEqual(result["answer"], "검증 전 초안")

    def test_natural_language_before_skill_recovers_to_model_selected_action(self) -> None:
        request = {
            "question": "외부 전력회사에서 구매한 전기를 사용합니다. 이 활동은 Scope 몇인가요?",
            "userRole": "LEARNER",
            "asOfDate": "2026-08-28",
        }
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            client = ActionRecoveryQwen()
            result = run_agent(
                request,
                root=ROOT,
                runs_root=temporary / "runs",
                db_path=temporary / "state.sqlite3",
                client=client,
            )
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(client.recovered)
            self.assertEqual(client.asserted_tool_names, ["invoke_kac_skill"])
            self.assertEqual(result["skills_invoked"], ["scope-activity-classification"])
            events = json.loads((Path(result["run_directory"]) / "events.json").read_text(encoding="utf-8"))
            self.assertTrue(any(item["event_type"] == "action_structured" for item in events))
            repairs = [item for item in events if item["event_type"] == "candidate_evidence_repaired"]
            self.assertEqual(repairs[0]["added_evidence_ids"], ["concept:OPERATIONAL_BOUNDARY"])


if __name__ == "__main__":
    unittest.main()
