from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_TOOLS = {
    "observe_concept",
    "observe_neighbors",
    "select_relation_step",
    "backtrack_relation_step",
    "stop_relation_traversal",
    "invoke_kac_skill",
    "request_missing_evidence",
    "submit_answer_candidate",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_policy(root: Path | None = None) -> dict[str, Any]:
    root = (root or project_root()).resolve()
    policy = json.loads((root / "config" / "agent_policy.json").read_text(encoding="utf-8"))
    autonomy = policy.get("autonomy", {})
    tools = set(policy.get("allowed_tools", []))
    if policy.get("agent_mode") != "LOCAL_LLM_AGENTIC_KAC_TRAVERSAL_LOOP":
        raise ValueError("agent mode is not the agentic KAC traversal loop")
    if autonomy.get("question_specific_route_maps_allowed") is not False:
        raise ValueError("question-specific route maps must be forbidden")
    if autonomy.get("model_selects_next_action") is not True:
        raise ValueError("the local model must select the next action")
    if tools != REQUIRED_TOOLS:
        raise ValueError("allowed tool registry does not match the v3 foundation contract")
    if int(autonomy.get("max_steps", 0)) < 1 or int(autonomy.get("max_tool_calls", 0)) < 1:
        raise ValueError("agent budgets must be positive")
    if int(autonomy.get("max_traversal_depth", 0)) < 1:
        raise ValueError("relation traversal depth budget must be positive")
    return policy
