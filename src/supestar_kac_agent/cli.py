from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import run_agent
from .doctor import run_doctor
from .policy import load_policy


def main() -> None:
    parser = argparse.ArgumentParser(prog="supestar-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="Validate the local Ollama and model boundary")
    doctor.add_argument("--endpoint", default="http://127.0.0.1:11434")
    doctor.add_argument("--model", default="qwen2.5:14b-instruct-q4_K_M")
    subparsers.add_parser("validate-policy", help="Validate the v3 agent policy")
    run = subparsers.add_parser("run", help="Run the autonomous Local-Qwen KAC agent")
    run.add_argument("--input", required=True, type=Path)
    run.add_argument("--model", default="qwen2.5:14b-instruct-q4_K_M")
    run.add_argument("--endpoint", default="http://127.0.0.1:11434")
    serve = subparsers.add_parser("serve", help="Run the local trace UI and API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=4177)
    args = parser.parse_args()
    if args.command == "doctor":
        result = run_doctor(args.endpoint, args.model)
    elif args.command == "run":
        from .ollama_client import OllamaClient
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = run_agent(payload, client=OllamaClient(args.endpoint, args.model))
    elif args.command == "serve":
        from .server import serve as serve_web
        serve_web(args.host, args.port)
        return
    else:
        policy = load_policy()
        result = {
            "status": "PASS",
            "agent_mode": policy["agent_mode"],
            "allowed_tools": policy["allowed_tools"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
