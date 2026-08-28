from __future__ import annotations

import unittest
from pathlib import Path

from supestar_kac_agent.validation_bank import public_question_bank, validate_question_bank


ROOT = Path(__file__).resolve().parents[1]


class ValidationQuestionBankTests(unittest.TestCase):
    def test_question_bank_is_diverse_and_public_view_omits_skill_inputs(self) -> None:
        bank = public_question_bank(ROOT)
        self.assertEqual(bank["purpose"], "VALIDATION_ONLY_NOT_AGENT_INPUT")
        self.assertGreaterEqual(bank["question_count"], 20)
        self.assertEqual(set(bank["category_counts"]), {
            "개념·관계", "Scope 1·2·3", "탄소시장", "산림탄소", "거래·안전",
        })
        self.assertTrue(all("skill_inputs" not in item for item in bank["questions"]))

    def test_every_question_resolves_expected_anchors_and_skill_contract(self) -> None:
        result = validate_question_bank(ROOT)
        self.assertEqual(result["status"], "PASS", result["issues"])
        self.assertEqual(result["passed"], result["question_count"])
        self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
