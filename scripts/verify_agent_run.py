#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    root = args.project_root.resolve()
    manifest = read(run_dir / "run_manifest.json")
    request = read(run_dir / "request.json")
    identity = read(run_dir / "model_identity.json")
    events = read(run_dir / "events.json")
    failures = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    require(manifest.get("status") == "PASS", "run status is not PASS")
    require(manifest.get("output_state") == "OUTPUT_VERIFIED", "output is not verified")
    require(manifest.get("llm_called") is True, "LLM call is not recorded")
    require(manifest.get("local_llm_verified") is True, "local LLM is not verified")
    require(manifest.get("internet_used") is False, "run claims Internet use")
    require(manifest.get("question_specific_route_map_used") is False, "question route map was used")
    require(bool(manifest.get("answer")), "verified answer is empty")
    require(bool(manifest.get("skills_invoked")), "no KAC skill was invoked")
    require(bool(manifest.get("source_refs")), "verified answer has no source refs")
    require(manifest.get("verification", {}).get("verdict") == "PASS", "verifier verdict is not PASS")
    require(not manifest.get("verification", {}).get("missing_requirements"), "verifier still has missing requirements")
    require(not manifest.get("verification", {}).get("unsupported_evidence_ids"), "unsupported evidence remains")
    require(identity == manifest.get("model_identity"), "model identity files disagree")
    endpoint = urlparse(str(identity.get("endpoint", "")))
    require(endpoint.scheme == "http" and endpoint.hostname in {"127.0.0.1", "localhost", "::1"}, "model endpoint is not loopback")
    require(bool(identity.get("model_digest")), "model digest is missing")
    require("tools" in identity.get("capabilities", []), "model did not advertise tools")
    require(request.get("run_id") == manifest.get("run_id"), "request and manifest run IDs disagree")
    require(len(events) == manifest.get("event_count"), "event count mismatch")
    require([event.get("sequence") for event in events] == list(range(1, len(events) + 1)), "event sequence is not contiguous")
    require(any(event.get("event_type") == "tool_action" and event.get("tool_name") == "invoke_kac_skill" for event in events), "Skill invocation action is absent")
    require(any(event.get("event_type") == "verification" and event.get("verdict") == "PASS" for event in events), "PASS verification event is absent")
    require(events[-1].get("event_type") == "agent_completed" and events[-1].get("status") == "PASS", "completion event is not PASS")
    skill_files = sorted((run_dir / "skills").glob("*.json"))
    skill_runs = [read(path) for path in skill_files]
    require(len(skill_runs) == len(manifest.get("skill_run_ids", [])), "SkillRun file count mismatch")
    require({item.get("skill_run_id") for item in skill_runs} == set(manifest.get("skill_run_ids", [])), "SkillRun IDs mismatch")
    require(all(item.get("implementation_origin") == "REIMPLEMENTED_FROM_IMPORTED_METHOD_CONTRACT" for item in skill_runs), "unexpected Skill implementation origin")
    require(all(not item.get("external_actions") for item in skill_runs), "a SkillRun recorded external action")

    db_path = root / ".state" / "supestar_agent.sqlite3"
    if db_path.exists():
        connection = sqlite3.connect(db_path)
        row = connection.execute("SELECT status, payload_json FROM agent_run WHERE id=?", (manifest["run_id"],)).fetchone()
        event_rows = connection.execute("SELECT COUNT(*) FROM agent_event WHERE run_id=?", (manifest["run_id"],)).fetchone()[0]
        connection.close()
        require(row is not None and row[0] == "PASS", "SQLite agent_run record is absent or not PASS")
        require(event_rows == len(events), "SQLite event count mismatch")

    report = {
        "status":"PASS" if not failures else "FAIL",
        "run_id":manifest.get("run_id"),
        "question":manifest.get("question"),
        "answer":manifest.get("answer"),
        "model":identity,
        "skills_invoked":manifest.get("skills_invoked"),
        "source_refs":manifest.get("source_refs"),
        "event_count":len(events),
        "skill_run_file_count":len(skill_files),
        "artifact_hashes":{
            "request.json":sha256(run_dir / "request.json"),
            "model_identity.json":sha256(run_dir / "model_identity.json"),
            "events.json":sha256(run_dir / "events.json"),
            "run_manifest.json":sha256(run_dir / "run_manifest.json"),
            **{f"skills/{path.name}":sha256(path) for path in skill_files},
        },
        "failures":failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not failures else 1)


if __name__ == "__main__":
    main()
