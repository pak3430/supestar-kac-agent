from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


ACTIVE_SKILLS = (
    "esg-carbon-action-path",
    "scope-activity-classification",
    "carbon-market-unit-comparison",
    "forest-esg-impact-mapping",
    "forest-carbon-procedure-guidance",
    "forest-carbon-transaction-readiness",
)
CHAIN_FILES = (
    "manifest.json",
    "identity.json",
    "goal.json",
    "task.json",
    "knowledge.json",
    "method.json",
    "skill.json",
    "runtime.json",
)
EVIDENCE_FILES = (
    "provenance/stage_v1_import/derivation_manifest.json",
    "provenance/stage_v1_import/snapshots/stage4_005_esg_management_closure.md",
    "provenance/stage_v1_import/snapshots/stage4_007_organizational_boundary_closure.md",
    "provenance/stage_v1_import/snapshots/stage4_031_climate_claim_closure.md",
    "provenance/stage_v1_import/snapshots/stage4_034_forest_carbon_project_closure.md",
    "provenance/stage_v1_import/snapshots/stage4_061_transaction_evidence_pack_closure.md",
    "provenance/stage_v1_import/snapshots/stage_1_to_5_post_execution_verification.json",
    "provenance/stage_v1_import/snapshots/transaction_evidence_grounding_approval.md",
    "sources/registry/source_registry.json",
    "sources/registry/official_source_claims_2026-08-27.json",
    "sources/registry/forest_carbon_transaction_gate_policy.json",
    "domain_context/versions/0.1.0/domain_context.json",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def copy_one(source_root: Path, target_root: Path, source_path: str, destination_path: str) -> dict[str, str]:
    source = source_root / source_path
    destination = target_root / destination_path
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    source_hash = sha256(source)
    destination_hash = sha256(destination)
    if source_hash != destination_hash:
        raise RuntimeError(f"byte-identical import failed: {source_path}")
    return {
        "source_path": source_path,
        "destination_path": destination_path,
        "source_sha256": source_hash,
        "destination_sha256": destination_hash,
        "transformation": "BYTE_IDENTICAL",
    }


def run(source_root: Path, target_root: Path) -> dict[str, object]:
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    if git(source_root, "status", "--porcelain"):
        raise RuntimeError("source repository must be clean before import")
    source_commit = git(source_root, "rev-parse", "HEAD")
    source_remote = git(source_root, "remote", "get-url", "origin")
    files: list[dict[str, str]] = []
    for skill_name in ACTIVE_SKILLS:
        for filename in CHAIN_FILES:
            source_path = f"kac/chains/{skill_name}/{filename}"
            destination_path = f"skills/atomic/{skill_name}/{filename}"
            files.append(copy_one(source_root, target_root, source_path, destination_path))
    for source_path in EVIDENCE_FILES:
        destination_path = f"provenance/imported/v2/{source_path}"
        files.append(copy_one(source_root, target_root, source_path, destination_path))
    manifest: dict[str, object] = {
        "schema_version": "1.0.0",
        "status": "SEALED_IMPORT",
        "source_repository": source_remote,
        "source_commit": source_commit,
        "active_skill_names": list(ACTIVE_SKILLS),
        "excluded_skill_names": [
            {
                "name": "supestar-question-routing",
                "reason": "Replaced by Local Qwen next-action selection; fixed question routing is forbidden in v3.",
            }
        ],
        "files": files,
    }
    destination = target_root / "provenance" / "import_manifest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--target-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = run(args.source_root, args.target_root)
    print(json.dumps({
        "status": result["status"],
        "source_commit": result["source_commit"],
        "active_skill_count": len(result["active_skill_names"]),
        "verified_file_count": len(result["files"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
