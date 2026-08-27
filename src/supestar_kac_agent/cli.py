from __future__ import annotations

import argparse
import json

from .doctor import run_doctor
from .policy import load_policy


def main() -> None:
    parser = argparse.ArgumentParser(prog="supestar-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="Validate the local Ollama and model boundary")
    doctor.add_argument("--endpoint", default="http://127.0.0.1:11434")
    doctor.add_argument("--model", default="qwen2.5:14b-instruct-q4_K_M")
    subparsers.add_parser("validate-policy", help="Validate the v3 agent policy")
    args = parser.parse_args()
    if args.command == "doctor":
        result = run_doctor(args.endpoint, args.model)
    else:
        policy = load_policy()
        result = {
            "status": "PASS",
            "agent_mode": policy["agent_mode"],
            "allowed_tools": policy["allowed_tools"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
