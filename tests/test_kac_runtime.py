from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from supestar_kac_agent.agent import (
    _lifecycle_gate,
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
            [tool_call("expand_relations", {
                "concept": "ESG",
                "toward_concept": "탄소크레딧",
                "purpose": "두 anchor 사이의 근거 있는 연결 관찰",
            })],
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
            [tool_call("expand_relations", {"concept": "ESG", "toward_concept": "탄소크레딧", "purpose": "관계 관찰"})],
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

    def test_lifecycle_gate_advances_without_reopening_completed_stages(self) -> None:
        graph = KnowledgeGraph(ROOT)
        anchors = ["OPERATIONAL_BOUNDARY", "ACTIVITY_DATA"]
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(root=ROOT, run_dir=Path(directory))
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
            self.assertEqual(gate, "CONNECT_OBSERVED_ANCHORS")
            self.assertEqual(definitions[0]["function"]["name"], "expand_relations")
            environment.execute("expand_relations", {
                "concept":"OPERATIONAL_BOUNDARY",
                "toward_concept":"ACTIVITY_DATA",
                "purpose":"필수 anchor 연결",
            })
            gate, definitions, state = _lifecycle_gate(environment, anchors, graph)
            self.assertEqual(gate, "EXECUTE_KAC_SKILL")
            self.assertEqual(definitions[0]["function"]["name"], "invoke_kac_skill")

    def test_duplicate_concept_and_relation_observations_are_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(root=ROOT, run_dir=Path(directory))
            first_concept = environment.execute("observe_concept", {"concept":"ESG"})
            second_concept = environment.execute("observe_concept", {"concept":"ESG"})
            self.assertEqual(first_concept["status"], "OBSERVED")
            self.assertEqual(second_concept["status"], "REUSED_EXISTING_CONCEPT_OBSERVATION")
            arguments = {"concept":"ESG", "toward_concept":"CARBON_CREDIT", "purpose":"관계 관찰"}
            first_relation = environment.execute("expand_relations", arguments)
            evidence_after_first = set(environment.evidence)
            second_relation = environment.execute("expand_relations", arguments)
            self.assertEqual(first_relation["status"], "EXPANDED")
            self.assertEqual(second_relation["status"], "REUSED_EXISTING_RELATION_EXPANSION")
            self.assertEqual(set(environment.evidence), evidence_after_first)

    def test_natural_korean_scope_claim_is_grounded_by_skill_input_and_observed_concept(self) -> None:
        graph = KnowledgeGraph(ROOT)
        anchors = ["OPERATIONAL_BOUNDARY", "ACTIVITY_DATA"]
        with tempfile.TemporaryDirectory() as directory:
            environment = ToolEnvironment(root=ROOT, run_dir=Path(directory))
            for concept in anchors:
                environment.execute("observe_concept", {"concept":concept})
            environment.execute("expand_relations", {
                "concept":"OPERATIONAL_BOUNDARY",
                "toward_concept":"ACTIVITY_DATA",
                "purpose":"Scope 판정 근거 연결",
            })
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
                    "text":"배출원이 우리 회사의 소유와 통제하에 있으며 직접 연소한 도시가스이므로 Scope 1으로 분류됩니다.",
                    "evidence_ids":[
                        "skill:scope-activity-classification:latest",
                        "concept:OPERATIONAL_BOUNDARY",
                    ],
                }],
            }
            verification = verify_candidate(
                candidate,
                anchors=anchors,
                evidence=environment.evidence,
                skill_runs=environment.skill_runs,
                graph=graph,
            )
            self.assertEqual(verification["verdict"], "PASS")

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
            manifest = Path(result["run_directory"]) / "run_manifest.json"
            self.assertTrue(manifest.exists())
            self.assertTrue(list((Path(result["run_directory"]) / "skills").glob("*.json")))

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
