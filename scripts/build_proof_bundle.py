#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("proof/latest_verified_run.json"))
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "PASS" or manifest.get("verification", {}).get("verdict") != "PASS":
        raise SystemExit("Only a verifier-PASS run can become a proof snapshot")
    proof = {
        "object_type":"VerifiedRunProofSnapshot",
        "note":"This is a non-secret evidence snapshot. Full transient runs and SQLite state remain gitignored.",
        "run_id":manifest["run_id"],
        "question":manifest["question"],
        "answer":manifest["answer"],
        "stop_reason":manifest["stop_reason"],
        "model_identity":manifest["model_identity"],
        "skills_invoked":manifest["skills_invoked"],
        "skill_run_ids":manifest["skill_run_ids"],
        "observed_evidence_ids":manifest["observed_evidence_ids"],
        "verification":manifest["verification"],
        "llm_called":manifest["llm_called"],
        "local_llm_verified":manifest["local_llm_verified"],
        "internet_used":manifest["internet_used"],
        "question_specific_route_map_used":manifest["question_specific_route_map_used"],
        "event_count":manifest["event_count"],
        "source_run_manifest_sha256":hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }
    target = args.output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(proof, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
