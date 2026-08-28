from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class OllamaClient:
    supports_primary_structured_actions = True

    def __init__(self, endpoint: str, model: str, *, timeout: int = 300) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS:
            raise ValueError("Local Qwen endpoint must be an explicit HTTP loopback address")
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _request(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {} if data is None else {"Content-Type": "application/json"}
        request = Request(f"{self.endpoint}{path}", data=data, headers=headers)
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(f"Ollama {path} returned HTTP {error.code}") from error
        except (TimeoutError, URLError) as error:
            raise RuntimeError(f"Ollama {path} request failed: {error.reason if isinstance(error, URLError) else error}") from error

    def identity(self) -> dict[str, Any]:
        version = self._request("/api/version")
        tags = self._request("/api/tags").get("models", [])
        selected = next((item for item in tags if item.get("name") == self.model or item.get("model") == self.model), None)
        if not selected:
            raise RuntimeError(f"local model is not installed: {self.model}")
        show = self._request("/api/show", {"model": self.model})
        details = show.get("details", {})
        capabilities = sorted(show.get("capabilities", []))
        if "tools" not in capabilities:
            raise RuntimeError(f"local model does not advertise tool capability: {self.model}")
        return {
            "provider": "OLLAMA_LOCAL",
            "endpoint": self.endpoint,
            "endpoint_scope": "LOOPBACK_ONLY",
            "ollama_version": version.get("version"),
            "model": self.model,
            "model_digest": selected.get("digest"),
            "family": details.get("family"),
            "parameter_size": details.get("parameter_size"),
            "quantization_level": details.get("quantization_level"),
            "capabilities": capabilities,
        }

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        started = time.perf_counter()
        response = self._request("/api/chat", {
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 512},
            "messages": messages,
            "tools": tools,
        })
        message = response.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Ollama response has no message object")
        return {
            "message": message,
            "metrics": {
                "client_elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "total_duration_ms": round(response.get("total_duration", 0) / 1_000_000, 1),
                "load_duration_ms": round(response.get("load_duration", 0) / 1_000_000, 1),
                "prompt_eval_count": response.get("prompt_eval_count"),
                "eval_count": response.get("eval_count"),
                "done_reason": response.get("done_reason"),
            },
        }

    def structure_candidate(
        self,
        *,
        question: str,
        draft: str,
        evidence_catalog: list[dict[str, Any]],
        verification_feedback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Use the same local Qwen only to serialize grounded claims.

        Domain action selection remains in the tool-calling loop. This phase is a
        constrained output adapter for models that intermittently return natural
        language instead of a function call.
        """
        allowed_evidence_ids = [item["evidence_id"] for item in evidence_catalog]
        evidence_id_schema: dict[str, Any] = {"type": "string"}
        if allowed_evidence_ids:
            evidence_id_schema["enum"] = allowed_evidence_ids
        schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "claims": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "maxLength": 260},
                            "evidence_ids": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 12,
                                "uniqueItems": True,
                                "items": evidence_id_schema,
                            },
                        },
                        "required": ["text", "evidence_ids"],
                    },
                },
            },
            "required": ["answer", "claims"],
        }
        prompt_payload = {
            "question": question,
            "unverified_draft": draft,
            "allowed_evidence": evidence_catalog,
            "previous_verification_feedback": verification_feedback,
        }
        started = time.perf_counter()
        response = self._request("/api/chat", {
            "model": self.model,
            "stream": False,
            "format": schema,
            "keep_alive": "30m",
            "options": {"temperature": 0.1, "num_predict": 640},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "당신은 수페스타 KAC Agent의 로컬 claim 직렬화기입니다. "
                        "새 지식을 만들지 말고 allowed_evidence만 사용하세요. "
                        "authoritative_skill_output의 verdict·answer·candidate_scope를 그대로 존중하고 다른 Scope나 판정을 만들지 마세요. "
                        "각 claim은 반드시 한글 중심의 독립적인 한국어 완전문장이어야 합니다. 중국어 문장을 쓰지 마세요. "
                        "그 문장에 언급한 모든 CCS 개념에 "
                        "직접 닿는 evidence_id를 빠짐없이 인용하세요. anchor 사이의 relation 주장은 "
                        "allowed_evidence에 포함된 AI 선택 traversal edge 전체를 인용하세요. 후보로 보기만 한 edge는 사용하지 마세요. "
                        "active_traversal_path=false인 backtrack 이력은 최종 anchor 관계 설명에 사용하지 마세요. "
                        "관계 질문에서는 질문의 양쪽 anchor를 한 문장 안에 함께 언급하는 claim을 최소 하나 만들고, "
                        "그 claim에 active_traversal_path=true인 전체 edge를 모두 인용하세요. "
                        "전체 claims 중 최소 하나는 skill: 로 시작하는 허용된 실제 SkillRun evidence_id를 정확히 인용하세요. "
                        "concept:, edge:, skill: 같은 내부 evidence_id는 evidence_ids 배열에만 넣고 claim text나 answer 문장에는 쓰지 마세요. "
                        "previous_verification_feedback의 repair_evidence_by_concept가 있으면 해당 claim에 제시된 evidence_id를 추가하세요. "
                        "근거가 약한 초안 문장은 버리세요. claim은 1~3개로 간결하게 만들고 answer는 claim text들을 최대 5문장으로 자연스럽게 연결하세요."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
            ],
        }, timeout=min(self.timeout, 120))
        content = str((response.get("message") or {}).get("content", ""))
        try:
            candidate = json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeError("Local Qwen structured candidate was not valid JSON") from error
        if not isinstance(candidate, dict):
            raise RuntimeError("Local Qwen structured candidate was not an object")
        return {
            "candidate": candidate,
            "metrics": {
                "phase": "candidate_structuring",
                "client_elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "total_duration_ms": round(response.get("total_duration", 0) / 1_000_000, 1),
                "prompt_eval_count": response.get("prompt_eval_count"),
                "eval_count": response.get("eval_count"),
                "done_reason": response.get("done_reason"),
            },
        }

    def select_tool_action(
        self,
        *,
        question: str,
        rejected_draft: str,
        anchors: list[str],
        evidence_catalog: list[dict[str, Any]],
        skill_catalog: list[dict[str, Any]],
        allowed_tools: list[dict[str, Any]],
        traversal_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Recover one model-selected tool action after a natural-language turn.

        This is a constrained Local Qwen action adapter, not a question router.
        The runtime selects only the current lifecycle gate; Qwen still selects
        the concrete concept, current one-hop edge, backtrack, skill, and inputs
        from live observations.
        """
        tool_names = [item["function"]["name"] for item in allowed_tools]
        if not tool_names:
            raise ValueError("at least one recovery tool is required")
        arguments_schema: dict[str, Any] = {"type":"object"}
        if len(allowed_tools) == 1:
            arguments_schema = allowed_tools[0]["function"]["parameters"]
        schema = {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "enum": tool_names},
                "arguments": arguments_schema,
            },
            "required": ["tool_name", "arguments"],
        }
        prompt_payload = {
            "question": question,
            "rejected_natural_language_draft": rejected_draft,
            "anchor_candidates": anchors,
            "observed_evidence": evidence_catalog,
            "registered_skills": skill_catalog,
            "allowed_tools_at_current_lifecycle_gate": allowed_tools,
            "relation_traversal_context": traversal_context or {},
        }
        started = time.perf_counter()
        response = self._request("/api/chat", {
            "model": self.model,
            "stream": False,
            "format": schema,
            "keep_alive": "30m",
            "options": {"temperature": 0.1, "num_predict": 640},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "당신은 수페스타 KAC Agent의 로컬 행동 선택기입니다. 자연어 답변을 만들지 마세요. "
                        "현재 lifecycle gate에서 허용된 도구 중 하나를 선택하고 arguments를 완성하세요. "
                        "질문별 고정 경로는 없으며, 질문·anchor·실제 Observation·등록된 Skill 계약만 사용합니다. "
                        "존재하지 않는 개념을 만들지 말고 CCS의 정확한 concept id 또는 alias를 사용하세요. "
                        "관계 탐색 중에는 전체 경로를 추측하지 말고 직전 1-hop Observation의 edge_id 하나만 선택하거나, "
                        "막힌 경우 backtrack_relation_step을 선택하세요. "
                        "Skill을 실행할 때 질문에 명시된 사실만 입력하고 확인되지 않은 필드는 UNKNOWN, NONE, "
                        "빈 배열 또는 생략으로 표현하여 REVIEW가 필요하면 그대로 관찰하세요."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
            ],
        })
        content = str((response.get("message") or {}).get("content", ""))
        try:
            action = json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeError("Local Qwen recovery action was not valid JSON") from error
        if not isinstance(action, dict) or action.get("tool_name") not in tool_names or not isinstance(action.get("arguments"), dict):
            raise RuntimeError("Local Qwen recovery action violated the allowed tool schema")
        return {
            "action": action,
            "metrics": {
                "phase": "tool_action_recovery",
                "client_elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "total_duration_ms": round(response.get("total_duration", 0) / 1_000_000, 1),
                "prompt_eval_count": response.get("prompt_eval_count"),
                "eval_count": response.get("eval_count"),
                "done_reason": response.get("done_reason"),
            },
        }
