from __future__ import annotations

import unittest

from supestar_kac_agent.contracts import Observation, ToolAction, VerificationDecision
from supestar_kac_agent.doctor import _local_endpoint, model_summary
from supestar_kac_agent.policy import REQUIRED_TOOLS, load_policy


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


if __name__ == "__main__":
    unittest.main()
