from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ToolAction:
    step: int
    tool_name: str
    arguments: dict[str, Any]

    @property
    def action_hash(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True)
class Observation:
    step: int
    tool_name: str
    payload: dict[str, Any]
    source_refs: tuple[str, ...]

    @property
    def observation_hash(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True)
class VerificationDecision:
    verdict: str
    checked_claims: tuple[str, ...]
    missing_evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.verdict not in {"PASS", "REVIEW", "STOP"}:
            raise ValueError("verdict must be PASS, REVIEW, or STOP")
