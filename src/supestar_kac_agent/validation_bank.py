from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .graph import KnowledgeGraph
from .policy import project_root
from .skill_compiler import skill_catalog
from .skill_runtime import execute_skill


def load_question_bank(root: Path | None = None) -> dict[str, Any]:
    resolved_root = (root or project_root()).resolve()
    value = json.loads((resolved_root / "validation" / "question_bank.json").read_text(encoding="utf-8"))
    if not isinstance(value.get("questions"), list):
        raise ValueError("validation question bank must contain a questions array")
    return value


def public_question_bank(root: Path | None = None) -> dict[str, Any]:
    bank = load_question_bank(root)
    visible_fields = {
        "id", "category", "label", "question", "verifies",
        "expected_skill", "expected_skill_verdict",
    }
    questions = [
        {key: value for key, value in item.items() if key in visible_fields}
        for item in bank["questions"]
    ]
    return {
        "schema_version": bank["schema_version"],
        "purpose": bank["purpose"],
        "question_count": len(questions),
        "category_counts": dict(Counter(item["category"] for item in questions)),
        "questions": questions,
    }


def _dotted_value(value: dict[str, Any], dotted_key: str) -> Any:
    current: Any = value
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(dotted_key)
        current = current[part]
    return current


def validate_question_bank(root: Path | None = None) -> dict[str, Any]:
    resolved_root = (root or project_root()).resolve()
    bank = load_question_bank(resolved_root)
    graph = KnowledgeGraph(resolved_root)
    registered_skills = {item["name"] for item in skill_catalog(resolved_root)}
    seen_ids: set[str] = set()
    issues: list[dict[str, str]] = []
    results: list[dict[str, Any]] = []

    for item in bank["questions"]:
        question_id = str(item.get("id", ""))
        item_issues: list[str] = []
        if not question_id or question_id in seen_ids:
            item_issues.append("id must be non-empty and unique")
        seen_ids.add(question_id)
        for required in ("category", "label", "question", "verifies", "expected_skill", "expected_skill_verdict", "skill_inputs"):
            if required not in item or item[required] in ("", None):
                item_issues.append(f"missing {required}")

        anchors = graph.anchor_ids(str(item.get("question", "")))
        missing_anchors = sorted(set(item.get("expected_anchors_all", [])) - set(anchors))
        if missing_anchors:
            item_issues.append(f"missing anchors: {', '.join(missing_anchors)}")

        skill_name = str(item.get("expected_skill", ""))
        actual_verdict = None
        if skill_name not in registered_skills:
            item_issues.append(f"unregistered skill: {skill_name}")
        elif isinstance(item.get("skill_inputs"), dict):
            execution = execute_skill(skill_name, item["skill_inputs"], root=resolved_root)
            if execution.get("status") != "EXECUTED":
                item_issues.append(f"skill did not execute: {execution.get('status')}")
            else:
                output = execution["skill_run"]["output"]
                actual_verdict = output.get("verdict")
                if actual_verdict != item.get("expected_skill_verdict"):
                    item_issues.append(
                        f"verdict expected {item.get('expected_skill_verdict')} but got {actual_verdict}"
                    )
                for dotted_key, expected_value in item.get("expected_output", {}).items():
                    try:
                        actual_value = _dotted_value(output, dotted_key)
                    except KeyError:
                        item_issues.append(f"output field missing: {dotted_key}")
                        continue
                    if actual_value != expected_value:
                        item_issues.append(
                            f"output {dotted_key} expected {expected_value!r} but got {actual_value!r}"
                        )

        for issue in item_issues:
            issues.append({"question_id": question_id, "issue": issue})
        results.append({
            "id": question_id,
            "category": item.get("category"),
            "anchors": anchors,
            "skill": skill_name,
            "expected_skill_verdict": item.get("expected_skill_verdict"),
            "actual_skill_verdict": actual_verdict,
            "status": "PASS" if not item_issues else "FAIL",
        })

    return {
        "status": "PASS" if not issues else "FAIL",
        "purpose": bank.get("purpose"),
        "question_count": len(bank["questions"]),
        "category_counts": dict(Counter(item.get("category") for item in bank["questions"])),
        "passed": sum(item["status"] == "PASS" for item in results),
        "failed": sum(item["status"] == "FAIL" for item in results),
        "issues": issues,
        "results": results,
    }
