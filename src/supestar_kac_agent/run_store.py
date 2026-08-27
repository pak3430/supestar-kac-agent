from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class AgentRunStore:
    def __init__(self, run_dir: Path, db_path: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.events: list[dict[str, Any]] = []
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(db_path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS agent_run (id TEXT PRIMARY KEY, status TEXT NOT NULL, payload_json TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS agent_event (run_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(run_id, sequence))"
        )
        self.connection.commit()

    def write(self, name: str, value: Any) -> None:
        _write_json(self.run_dir / name, value)

    def append_event(self, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        record = {"sequence": len(self.events) + 1, **event}
        self.events.append(record)
        _write_json(self.run_dir / "events.json", self.events)
        self.connection.execute(
            "INSERT INTO agent_event(run_id, sequence, event_type, payload_json) VALUES (?, ?, ?, ?)",
            (run_id, record["sequence"], record["event_type"], json.dumps(record, ensure_ascii=False, sort_keys=True)),
        )
        self.connection.commit()
        return record

    def finalize(self, run_id: str, status: str, package: dict[str, Any]) -> None:
        _write_json(self.run_dir / "run_manifest.json", package)
        self.connection.execute(
            "INSERT OR REPLACE INTO agent_run(id, status, payload_json) VALUES (?, ?, ?)",
            (run_id, status, json.dumps(package, ensure_ascii=False, sort_keys=True)),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
