from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--target-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    target_root = args.target_root.resolve()
    manifest = json.loads((target_root / "provenance" / "import_manifest.json").read_text(encoding="utf-8"))
    source_commit = subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True
    ).strip()
    if source_commit != manifest["source_commit"]:
        raise SystemExit("STOP: source commit changed")
    for record in manifest["files"]:
        source = source_root / record["source_path"]
        destination = target_root / record["destination_path"]
        if not source.is_file() or not destination.is_file():
            raise SystemExit(f"STOP: imported file missing: {record['destination_path']}")
        if sha256(source) != record["source_sha256"]:
            raise SystemExit(f"STOP: source hash changed: {record['source_path']}")
        if sha256(destination) != record["destination_sha256"]:
            raise SystemExit(f"STOP: destination hash changed: {record['destination_path']}")
        if record["source_sha256"] != record["destination_sha256"]:
            raise SystemExit(f"STOP: import was not byte-identical: {record['destination_path']}")
    print(json.dumps({
        "status": "PASS",
        "source_commit": source_commit,
        "active_skill_count": len(manifest["active_skill_names"]),
        "verified_file_count": len(manifest["files"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
