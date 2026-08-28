from __future__ import annotations

import argparse
import json
import mimetypes
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .agent import run_agent
from .graph import KnowledgeGraph
from .ollama_client import OllamaClient
from .policy import load_policy, project_root
from .skill_compiler import skill_catalog
from .validation_bank import public_question_bank


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class SupestarServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], root: Path) -> None:
        self.root = root.resolve()
        super().__init__(address, SupestarHandler)


class SupestarHandler(BaseHTTPRequestHandler):
    server: SupestarServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[web] {self.address_string()} {format % args}")

    def _send_json(self, status: int, value: Any) -> None:
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 65536:
            raise ValueError("request body must be between 1 and 65536 bytes")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("request JSON must be an object")
        return value

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/api/health":
            self._health()
            return
        if path == "/api/graph":
            graph = KnowledgeGraph(self.server.root)
            self._send_json(200, {"fingerprint":graph.fingerprint, **graph.data})
            return
        if path == "/api/validation-questions":
            self._send_json(200, public_question_bank(self.server.root))
            return
        if path == "/api/runs":
            manifests = []
            for manifest in sorted((self.server.root / "runs").glob("*/run_manifest.json"), reverse=True)[:20]:
                try:
                    value = json.loads(manifest.read_text(encoding="utf-8"))
                    manifests.append({
                        key:value.get(key)
                        for key in ("run_id", "status", "question", "stop_reason", "skills_invoked", "event_count")
                    })
                except (OSError, json.JSONDecodeError):
                    continue
            self._send_json(200, {"runs":manifests})
            return
        self._serve_static(path)

    def do_HEAD(self) -> None:
        path = unquote(urlparse(self.path).path)
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        allowed = {"index.html", "assets/style.css", "assets/app.js"}
        target = self.server.root / "web" / relative
        if relative not in allowed or not target.is_file():
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path != "/api/chat/stream":
            self._send_json(404, {"error":"NOT_FOUND"})
            return
        try:
            payload = self._read_json()
            question = str(payload.get("question", "")).strip()
            if not question:
                raise ValueError("question is required")
            request = {
                "question":question,
                "userRole":str(payload.get("userRole", "LEARNER")).upper(),
                "asOfDate":str(payload.get("asOfDate", date.today().isoformat())),
            }
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json(400, {"error":"INVALID_REQUEST", "message":str(error)})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()

        def send_line(kind: str, value: Any) -> None:
            self.wfile.write(_json_bytes({"kind":kind, kind:value}))
            self.wfile.flush()

        try:
            policy = load_policy(self.server.root)
            client = OllamaClient(
                policy["local_llm"]["default_endpoint"],
                policy["local_llm"]["default_model"],
            )
            result = run_agent(
                request,
                root=self.server.root,
                client=client,
                event_sink=lambda event: send_line("event", event),
            )
            send_line("result", result)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:
            send_line("error", {"type":type(error).__name__, "message":str(error)})
        finally:
            self.close_connection = True

    def _health(self) -> None:
        policy = load_policy(self.server.root)
        graph = KnowledgeGraph(self.server.root)
        client = OllamaClient(
            policy["local_llm"]["default_endpoint"],
            policy["local_llm"]["default_model"],
            timeout=10,
        )
        try:
            model = client.identity()
            model_status = "READY"
        except Exception as error:
            model = {"error_type":type(error).__name__, "error_message":str(error)}
            model_status = "UNAVAILABLE"
        self._send_json(200, {
            "status":"READY" if model_status == "READY" else "DEGRADED",
            "model_status":model_status,
            "model":model,
            "agent_mode":policy["agent_mode"],
            "question_specific_route_maps_allowed":policy["autonomy"]["question_specific_route_maps_allowed"],
            "graph":{"node_count":len(graph.nodes), "edge_count":len(graph.edges), "fingerprint":graph.fingerprint},
            "skills":[item["name"] for item in skill_catalog(self.server.root)],
        })

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        allowed = {"index.html", "assets/style.css", "assets/app.js"}
        if relative not in allowed:
            self._send_json(404, {"error":"NOT_FOUND"})
            return
        target = self.server.root / "web" / relative
        if not target.is_file():
            self._send_json(404, {"error":"STATIC_FILE_MISSING"})
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = "127.0.0.1", port: int = 4177, root: Path | None = None) -> None:
    resolved_root = (root or project_root()).resolve()
    httpd = SupestarServer((host, port), resolved_root)
    print(f"Supestar KAC Agent: http://{host}:{port}")
    print("Press Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="supestar-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4177)
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
