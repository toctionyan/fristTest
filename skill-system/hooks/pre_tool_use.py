#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from common import allow, command_from, deny, tool_info, workspace_from

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from contract import load_contract  # type: ignore
from multi_agent_governance import current_agent_identity, validate_multi_agent_begin_ready  # type: ignore
from repair_governance import permit_path_decision  # type: ignore
from scope_guard import (  # type: ignore
    bootstrap_command_allowed,
    command_decision,
    command_requires_contract,
    extract_paths,
    parse_hook_input,
    path_decision,
)

REVIEW_IMPORTER_COMMANDS = (
    "agent-review-import",
    "repair-permit",
    "repair-diff-review",
    "repair-closure-record",
    "contract-verify",
    "contract-close",
)
IMPLEMENTER_REGISTRATION_COMMANDS = (
    "agent-implementer-register",
    "contract-begin",
)
CANDIDATE_FREEZE_COMMANDS = ("candidate-freeze",)


def _agent_role(payload: dict[str, object]) -> str | None:
    for key in ("agent_name", "subagent_name", "agent", "role"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("_", "-")
    return current_agent_identity(payload).get("role")


def _contains(command: str, names: tuple[str, ...]) -> bool:
    return any(name in command for name in names)


def _bootstrap_role_decision(command: str, role: str | None) -> tuple[bool, str]:
    if _contains(command, REVIEW_IMPORTER_COMMANDS):
        if role != "review-importer":
            return False, "review artifact import and governance record creation require review-importer"
    if _contains(command, IMPLEMENTER_REGISTRATION_COMMANDS):
        if role != "product-implementer":
            return False, "implementer registration requires product-implementer"
    if _contains(command, CANDIDATE_FREEZE_COMMANDS):
        if role not in {"product-implementer", "review-importer"}:
            return False, "candidate freeze requires product-implementer or review-importer"
    return True, "trusted portable control CLI allowed"


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = parse_hook_input(raw)
    except Exception as exc:
        return deny(f"invalid hook input: {exc}")
    workspace = workspace_from(payload)
    tool_name, tool_input = tool_info(payload)
    command = command_from(tool_input)
    role = _agent_role(payload)

    if command:
        ok, reason = command_decision(command)
        if not ok:
            return deny(reason)
        if bootstrap_command_allowed(command):
            ok, reason = _bootstrap_role_decision(command, role)
            return allow(reason) if ok else deny(reason)
        if not command_requires_contract(command):
            return allow("read-only command allowed without a writable Change Contract")

    try:
        contract = load_contract(workspace)
    except ValueError as exc:
        return deny(str(exc))

    expected = str(contract.payload.get("writer_role") or "")
    if role and expected not in {"", "none"} and role != expected:
        return deny(f"agent role {role!r} is not the contract writer {expected!r}")
    if expected == "none":
        return deny(f"target {contract.target_kind.value} is read-only")

    if contract.target_kind.requires_candidate_change:
        if contract.status != "implementing":
            return deny(f"writable transition requires implementing status, not {contract.status}")
        try:
            validate_multi_agent_begin_ready(
                workspace,
                contract.payload,
                identity=current_agent_identity(payload),
            )
        except ValueError as exc:
            return deny(f"active Codex multi-agent ChangePermit is invalid: {exc}")

    for path in extract_paths(tool_name, tool_input, workspace):
        ok, reason = path_decision(contract, path)
        if not ok:
            return deny(reason)
        if contract.target_kind.requires_candidate_change:
            ok, reason = permit_path_decision(workspace, contract.payload, path)
            if not ok:
                return deny(reason)

    return allow(f"active change: {contract.change_id}; writer: {expected}; profile: {contract.profile}")


if __name__ == "__main__":
    raise SystemExit(main())
