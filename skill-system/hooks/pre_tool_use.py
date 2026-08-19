#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from common import allow, command_from, deny, tool_info, workspace_from
CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))
from contract import load_contract  # type: ignore
from scope_guard import bootstrap_command_allowed, command_decision, command_requires_contract, extract_paths, parse_hook_input, path_decision  # type: ignore
from repair_governance import permit_path_decision, validate_begin_ready  # type: ignore
from skill_invocation import SkillInvocationError, require_change_scope_invocation  # type: ignore


def _agent_role(payload: dict[str, object]) -> str | None:
    for key in ("agent_name", "subagent_name", "agent", "role"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = parse_hook_input(raw)
    except Exception as exc:
        return deny(f"invalid hook input: {exc}")
    workspace = workspace_from(payload)
    tool_name, tool_input = tool_info(payload)
    command = command_from(tool_input)
    if command:
        ok, reason = command_decision(command)
        if not ok:
            return deny(reason)
        if bootstrap_command_allowed(command):
            return allow("trusted portable control CLI allowed")
        if not command_requires_contract(command):
            return allow("read-only command allowed without a writable Change Contract")
    try:
        contract = load_contract(workspace)
    except ValueError as exc:
        return deny(str(exc))
    if contract.target_kind.requires_candidate_change:
        try:
            require_change_scope_invocation(workspace, change_id=contract.change_id)
        except SkillInvocationError as exc:
            return deny(f"required change-scope Skill invocation evidence is invalid: {exc}")
    role = _agent_role(payload)
    expected = str(contract.payload.get("writer_role") or "")
    if role and expected not in {"", "none"} and role != expected:
        return deny(f"agent role {role!r} is not the contract writer {expected!r}")
    if expected == "none":
        return deny(f"target {contract.target_kind.value} is read-only")
    if contract.target_kind.requires_candidate_change:
        if contract.status != "implementing":
            return deny(f"writable transition requires implementing status, not {contract.status}")
        try:
            validate_begin_ready(workspace, contract.payload)
        except ValueError as exc:
            return deny(f"active ChangePermit is invalid: {exc}")
    for path in extract_paths(tool_name, tool_input, workspace):
        ok, reason = path_decision(contract, path)
        if not ok:
            return deny(reason)
        if contract.target_kind.requires_candidate_change:
            ok, reason = permit_path_decision(workspace, contract.payload, path)
            if not ok:
                return deny(reason)
    return allow(f"active change: {contract.change_id}; writer: {expected}; profile: {contract.profile}; change-scope invocation: PASS")


if __name__ == "__main__":
    raise SystemExit(main())
