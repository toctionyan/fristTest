#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from common import command_from, parse_hook_input, tool_info, workspace_from
CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))
from contract import load_contract  # type: ignore
from product_scope import PRODUCT_PROFILES  # type: ignore
from scope_guard import command_requires_contract  # type: ignore
from skill_invocation import SkillInvocationError, require_change_scope_invocation  # type: ignore


def main() -> int:
    try:
        payload = parse_hook_input(sys.stdin.read())
    except Exception:
        return 0
    workspace = workspace_from(payload)
    tool_name, tool_input = tool_info(payload)
    if tool_name == "Bash" and not command_requires_contract(command_from(tool_input)):
        return 0
    commands = [
        [sys.executable, "-B", str(workspace / "skill-system/controller/registry.py"), "--verify"],
        [sys.executable, "-B", str(workspace / "skill-system/controller/host_conformance.py")],
    ]
    failures: list[tuple[str, str]] = []
    try:
        contract = load_contract(workspace, require_approved=False)
        if contract.target_kind.requires_candidate_change:
            try:
                require_change_scope_invocation(workspace, change_id=contract.change_id)
            except SkillInvocationError as exc:
                failures.append(("skill-invocation", str(exc)))
        if contract.profile in PRODUCT_PROFILES:
            commands.append([sys.executable, "-B", str(workspace / "skill-system/controller/product_contract_gate.py")])
    except ValueError:
        pass
    for command in commands:
        completed = subprocess.run(command, cwd=workspace, text=True, capture_output=True, check=False, timeout=90)
        if completed.returncode:
            failures.append((command[-1], completed.stdout + completed.stderr))
    if failures:
        print(json.dumps({
            "continue": False,
            "stopReason": "post-write control-plane validation failed: " + " | ".join(name for name, _ in failures),
            "systemMessage": "\n".join(body[-2500:] for _, body in failures),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
