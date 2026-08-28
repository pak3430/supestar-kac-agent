from __future__ import annotations

import unittest
from unittest.mock import patch

from supestar_kac_agent.contracts import Observation, ToolAction, VerificationDecision
from supestar_kac_agent.doctor import _local_endpoint, model_summary
from supestar_kac_agent.ollama_client import OllamaClient
from supestar_kac_agent.policy import REQUIRED_TOOLS, load_policy, project_root
from supestar_kac_agent.server import STATIC_ALLOWLIST


class FoundationTests(unittest.TestCase):
    def test_policy_forbids_question_specific_routes(self) -> None:
        policy = load_policy()
        self.assertFalse(policy["autonomy"]["question_specific_route_maps_allowed"])
        self.assertTrue(policy["autonomy"]["model_selects_next_action"])
        self.assertEqual(set(policy["allowed_tools"]), REQUIRED_TOOLS)

    def test_action_and_observation_are_hashable_evidence(self) -> None:
        action = ToolAction(1, "observe_concept", {"concept": "ESG"})
        observation = Observation(1, "observe_concept", {"concept": "ESG"}, ("source:esg",))
        self.assertEqual(len(action.action_hash), 64)
        self.assertEqual(len(observation.observation_hash), 64)
        self.assertNotEqual(action.action_hash, observation.observation_hash)

    def test_verification_verdict_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            VerificationDecision("MAYBE", (), ())

    def test_doctor_accepts_only_loopback(self) -> None:
        self.assertEqual(_local_endpoint("http://127.0.0.1:11434/"), "http://127.0.0.1:11434")
        with self.assertRaises(ValueError):
            _local_endpoint("https://example.com")

    def test_model_summary_requires_tools_capability(self) -> None:
        summary = model_summary({
            "details": {"family": "qwen2", "parameter_size": "14.8B", "quantization_level": "Q4_K_M"},
            "capabilities": ["completion", "tools"],
        })
        self.assertTrue(summary["tool_capable"])
        self.assertEqual(summary["family"], "qwen2")

    def test_single_anchor_candidate_contract_is_compact_and_answer_is_assembled(self) -> None:
        client = OllamaClient("http://127.0.0.1:11434", "qwen2.5:test")
        response = {
            "message": {"content": '{"claims":[{"text":"ESG는 환경·사회·거버넌스를 함께 살피는 관점입니다.","evidence_ids":["concept:ESG","skill:esg-definition:run-1"]}]}'},
            "eval_count": 92,
            "done_reason": "stop",
        }
        with patch.object(client, "_request", return_value=response) as request:
            result = client.structure_candidate(
                question="ESG란 무엇인가요?",
                draft="{}",
                evidence_catalog=[
                    {"evidence_id":"concept:ESG"},
                    {"evidence_id":"skill:esg-definition:run-1"},
                ],
                required_anchor_ids=["ESG"],
            )

        payload = request.call_args.args[1]
        self.assertEqual(payload["format"]["properties"]["claims"]["maxItems"], 1)
        self.assertNotIn("answer", payload["format"]["properties"])
        self.assertEqual(payload["options"]["num_predict"], 384)
        self.assertEqual(result["candidate"]["answer"], result["candidate"]["claims"][0]["text"])
        self.assertTrue(result["metrics"]["single_anchor_contract"])

    def test_candidate_structuring_retries_once_after_truncated_json(self) -> None:
        client = OllamaClient("http://127.0.0.1:11434", "qwen2.5:test")
        truncated = {
            "message": {"content": '{"claims":[{"text":"잘린 문장"'},
            "eval_count": 384,
            "done_reason": "length",
        }
        complete = {
            "message": {"content": '{"claims":[{"text":"ESG는 조직의 지속가능성 관점입니다.","evidence_ids":["concept:ESG","skill:esg-definition:run-1"]}]}'},
            "eval_count": 105,
            "done_reason": "stop",
        }
        with patch.object(client, "_request", side_effect=[truncated, complete]) as request:
            result = client.structure_candidate(
                question="ESG란 무엇인가요?",
                draft="{}",
                evidence_catalog=[
                    {"evidence_id":"concept:ESG"},
                    {"evidence_id":"skill:esg-definition:run-1"},
                ],
                required_anchor_ids=["ESG"],
            )

        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["metrics"]["serialization_attempts"], 2)
        self.assertEqual(result["metrics"]["attempt_metrics"][0]["done_reason"], "length")

    def test_every_allowlisted_static_file_exists(self) -> None:
        web_root = project_root() / "web"
        self.assertIn("kac-principle-diagram.html", STATIC_ALLOWLIST)
        self.assertFalse([
            relative for relative in STATIC_ALLOWLIST
            if not (web_root / relative).is_file()
        ])


if __name__ == "__main__":
    unittest.main()
