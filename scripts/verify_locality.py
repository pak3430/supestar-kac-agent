#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.project_root.resolve()
    manifest = read(args.run_dir.resolve() / "run_manifest.json")
    policy = read(root / "config" / "agent_policy.json")
    endpoint = urlparse(policy["local_llm"]["default_endpoint"])
    web_files = [root / "web/index.html", root / "web/assets/style.css", root / "web/assets/app.js"]
    web_text = "\n".join(path.read_text(encoding="utf-8") for path in web_files)
    external_web_urls = sorted(set(re.findall(r"https?://[^\s\"')]+", web_text)))
    failures = []
    if endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}:
        failures.append("configured model endpoint is not loopback")
    if manifest.get("model_identity", {}).get("endpoint_scope") != "LOOPBACK_ONLY":
        failures.append("verified run model scope is not loopback")
    if manifest.get("internet_used") is not False:
        failures.append("verified run does not state internet_used=false")
    if manifest.get("full_path_precomputed_for_agent") is not False:
        failures.append("verified run does not prove full_path_precomputed_for_agent=false")
    if manifest.get("pathfinder_role") != "POST_HOC_VALIDATION_ONLY":
        failures.append("deterministic pathfinder was not limited to post-hoc validation")
    if external_web_urls:
        failures.append(f"web UI contains external asset URLs: {external_web_urls}")
    if "127.0.0.1" not in (root / "src/supestar_kac_agent/server.py").read_text(encoding="utf-8"):
        failures.append("server has no explicit loopback default")
    report = {
        "status":"PASS" if not failures else "FAIL",
        "run_id":manifest.get("run_id"),
        "configured_model_endpoint":policy["local_llm"]["default_endpoint"],
        "observed_model_endpoint":manifest.get("model_identity", {}).get("endpoint"),
        "model_digest":manifest.get("model_identity", {}).get("model_digest"),
        "server_default":"127.0.0.1:4177",
        "web_external_asset_urls":external_web_urls,
        "internet_used":manifest.get("internet_used"),
        "full_path_precomputed_for_agent":manifest.get("full_path_precomputed_for_agent"),
        "pathfinder_role":manifest.get("pathfinder_role"),
        "failures":failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not failures else 1)


if __name__ == "__main__":
    main()
