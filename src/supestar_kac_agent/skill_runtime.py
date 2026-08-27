from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .graph import KnowledgeGraph
from .policy import project_root
from .skill_compiler import compile_skills
from .skill_handlers import HANDLERS


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def execute_skill(
    skill_name: str,
    inputs: dict[str, Any],
    *,
    root: Path | None = None,
    agent_run_dir: Path | None = None,
) -> dict[str, Any]:
    root = (root or project_root()).resolve()
    compiled = compile_skills(root)
    chains = {chain["skill_name"]: chain for chain in compiled["chains"]}
    chain = chains.get(skill_name)
    if not chain:
        return {"status":"STOP","error":"UNREGISTERED_SKILL","skill_name":skill_name,"allowed_skills":sorted(chains)}
    contract = chain["runtime"]["input_contract"]
    missing = [field for field in contract.get("required", []) if field not in inputs]
    if missing:
        return {
            "status":"REVIEW",
            "error":"MISSING_SKILL_INPUTS",
            "skill_name":skill_name,
            "missing_inputs":missing,
            "required_inputs":contract.get("required", []),
            "optional_inputs":contract.get("optional", []),
        }
    handler = HANDLERS.get(chain["handler"])
    if not handler:
        return {"status":"STOP","error":"HANDLER_NOT_INSTALLED","skill_name":skill_name}
    result = handler(dict(inputs), KnowledgeGraph(root))
    required_output = chain["runtime"]["output_contract"].get("required", [])
    result["evidence_refs"] = chain["source_refs"]
    absent = [field for field in required_output if field not in result]
    if absent:
        raise RuntimeError(f"{skill_name}: output contract missing {absent}")
    if result.get("verdict") not in {"PROCEED","REVIEW","STOP"}:
        raise RuntimeError(f"{skill_name}: invalid verdict")
    skill_run_id = f"skill-run-{uuid.uuid4()}"
    record = {
        "object_type":"KACSkillRun",
        "skill_run_id":skill_run_id,
        "created_at":datetime.now(timezone.utc).isoformat(),
        "skill_name":skill_name,
        "chain_id":chain["chain_id"],
        "chain_version":chain["version"],
        "chain_fingerprint":chain["fingerprint"],
        "input_snapshot":inputs,
        "input_hash":_hash(inputs),
        "output":result,
        "output_hash":_hash(result),
        "implementation_origin":"REIMPLEMENTED_FROM_IMPORTED_METHOD_CONTRACT",
        "external_actions":[],
    }
    if agent_run_dir:
        target = agent_run_dir / "skills" / f"{skill_run_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"status":"EXECUTED","skill_run":record}
