from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .policy import project_root


NODE_FILES = (
    "identity.json",
    "goal.json",
    "task.json",
    "knowledge.json",
    "method.json",
    "skill.json",
    "runtime.json",
)
NODE_TYPES = ("Identity", "Goal", "Task", "Knowledge", "Method", "Skill", "SkillRuntime")
ADMITTED_SOURCE_STATUSES = {"VERIFIED", "VERIFIED_LOCAL"}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compile_skills(root: Path | None = None) -> dict[str, Any]:
    root = (root or project_root()).resolve()
    import_manifest = _read(root / "provenance" / "import_manifest.json")
    sources = {item["id"]: item for item in _read(root / "knowledge" / "source_registry.json")["sources"]}
    imported_files = {item["destination_path"]: item for item in import_manifest["files"]}
    chains = []
    for skill_name in import_manifest["active_skill_names"]:
        chain_root = root / "skills" / "atomic" / skill_name
        manifest = _read(chain_root / "manifest.json")
        if tuple(manifest.get("nodes", [])) != NODE_FILES:
            raise ValueError(f"{skill_name}: canonical seven-node order is broken")
        nodes = [_read(chain_root / name) for name in NODE_FILES]
        for index, (node, expected_type) in enumerate(zip(nodes, NODE_TYPES)):
            if node.get("type") != expected_type:
                raise ValueError(f"{skill_name}: invalid node type at {NODE_FILES[index]}")
            if index and node.get("derived_from") != nodes[index - 1].get("id"):
                raise ValueError(f"{skill_name}: broken derivation at {NODE_FILES[index]}")
            path = f"skills/atomic/{skill_name}/{NODE_FILES[index]}"
            imported = imported_files.get(path)
            if not imported:
                raise ValueError(f"{skill_name}: provenance record missing for {path}")
            actual = hashlib.sha256((root / path).read_bytes()).hexdigest()
            if actual != imported["destination_sha256"]:
                raise ValueError(f"{skill_name}: imported bytes changed at {path}")
        identity, goal, task, knowledge, method, skill, runtime = nodes
        if manifest.get("chain_id") != skill_name or skill.get("name") != skill_name:
            raise ValueError(f"{skill_name}: chain identity mismatch")
        handler = manifest.get("handler")
        if method.get("runtime_handler") != handler or runtime.get("handler") != handler:
            raise ValueError(f"{skill_name}: Method/Runtime handler mismatch")
        for source_ref in knowledge.get("source_refs", []):
            source = sources.get(source_ref)
            if not source or source.get("status") not in ADMITTED_SOURCE_STATUSES:
                raise ValueError(f"{skill_name}: unadmitted source {source_ref}")
        chains.append({
            "skill_name": skill_name,
            "chain_id": manifest["chain_id"],
            "version": manifest["version"],
            "handler": handler,
            "identity": identity,
            "goal": goal,
            "task": task,
            "knowledge": knowledge,
            "method": method,
            "skill": skill,
            "runtime": runtime,
            "source_refs": knowledge["source_refs"],
            "fingerprint": _hash({"manifest": manifest, "nodes": nodes, "import_commit": import_manifest["source_commit"]}),
        })
    return {
        "status": "PASS",
        "source_commit": import_manifest["source_commit"],
        "skill_count": len(chains),
        "chains": chains,
    }


def skill_catalog(root: Path | None = None) -> list[dict[str, Any]]:
    resolved_root = (root or project_root()).resolve()
    compiled = compile_skills(resolved_root)
    guidance = _read(resolved_root / "config" / "skill_input_guidance.json")["skills"]
    return [
        {
            "name": chain["skill_name"],
            "objective": chain["goal"]["objective"],
            "required_inputs": chain["runtime"]["input_contract"].get("required", []),
            "optional_inputs": chain["runtime"]["input_contract"].get("optional", []),
            "input_guidance": guidance.get(chain["skill_name"], {}),
            "method_rules": chain["method"].get("rules", []),
            "guardrails": chain["skill"].get("guardrails", []),
            "fingerprint": chain["fingerprint"],
        }
        for chain in compiled["chains"]
    ]
