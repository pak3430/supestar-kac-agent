from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from supestar_kac_agent.agent import run_agent
from supestar_kac_agent.graph import KnowledgeGraph
from supestar_kac_agent.skill_compiler import compile_skills, skill_catalog
from supestar_kac_agent.skill_runtime import execute_skill


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
                tool_call("expand_relations", {"concept": "ESG", "toward_concept": "탄소크레딧", "purpose": "관계 관찰"}),
            ],
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

class KACRuntimeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
