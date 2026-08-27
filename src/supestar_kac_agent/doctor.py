from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _local_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError("Ollama endpoint must be an explicit local HTTP loopback address")
    return endpoint.rstrip("/")


def _request_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if data is None else {"Content-Type": "application/json"}
    request = Request(url, data=data, headers=headers)
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def model_summary(show: dict[str, Any]) -> dict[str, Any]:
    details = show.get("details", {})
    return {
        "family": details.get("family"),
        "parameter_size": details.get("parameter_size"),
        "quantization_level": details.get("quantization_level"),
        "capabilities": sorted(show.get("capabilities", [])),
        "tool_capable": "tools" in show.get("capabilities", []),
    }


def run_doctor(endpoint: str, model: str) -> dict[str, Any]:
    endpoint = _local_endpoint(endpoint)
    version = _request_json(f"{endpoint}/api/version")
    show = _request_json(f"{endpoint}/api/show", {"model": model})
    summary = model_summary(show)
    return {
        "status": "READY" if summary["tool_capable"] else "STOP",
        "endpoint": endpoint,
        "endpoint_scope": "LOOPBACK_ONLY",
        "ollama_version": version.get("version"),
        "model": model,
        **summary,
        "inference_executed": False,
        "note": "Doctor validates the local model boundary and tool capability; it does not claim that the Agent loop is implemented.",
    }
