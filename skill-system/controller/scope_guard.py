
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any, Iterable

try:
    from .models import ChangeContract
except ImportError:  # direct script/test loading
    from models import ChangeContract  # type: ignore

PROTECTED_GOVERNANCE = (
    "governance/quality-loop-policy.json",
    "governance/active-change.json",
    "governance/evidence/**",
    ".quality/**",
    "skill-system/trusted-judge/**",
)
CONTRACT_BOOTSTRAP_COMMANDS = ("skill-system/controller/change_contract_cli.py", "skillctl.py")
DESTRUCTIVE_COMMANDS = (
    r"\brm\s+-rf\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-zA-Z]*f",
    r"\bgit\s+(checkout|restore|apply|am)\b",
    r"\bsudo\b",
)

# Repository writes must use the host's structured Write/Edit/apply_patch tools,
# where the Hook can check exact paths. Shell mutation is intentionally denied
# because redirects, here-docs and inline interpreters can bypass path guards.
SHELL_MUTATION_PATTERNS = (
    r"(^|[;&|]\s*)(touch|mkdir|cp|mv|rm|install|chmod|chown)\b",
    r"\bsed\s+[^\n;]*-[^\s]*i\b",
    r"\bperl\s+[^\n;]*-p?i\b",
    r"(^|[;&|]\s*)tee\b",
    r"(?<![0-9])>{1,2}(?![&])\s*(?!/dev/null)",
    r"\bpython(?:3)?\s+(-c|-\s*<<)",
    r"\b(npm|pnpm|yarn|pip|uv)\s+(install|add|remove|uninstall|sync)\b",
)

READ_ONLY_COMMAND_PATTERNS = (
    r"^\s*(git\s+(status|diff|log|show|branch)|rg|grep|find|ls|pwd|cat|head|tail|wc|stat)\b",
    r"^\s*sed\s+-n\b",
    r"^\s*python(?:3)?\s+-B\s+(architecture-skill/scripts/verify_|skill-system/controller/(host_conformance|registry|project_compatibility|profile_runner)|-m\s+(unittest|pytest))",
)


def normalize_path(raw: str, workspace: Path) -> str:
    value = raw.strip().strip('"\'').replace("\\", "/")
    if not value:
        return value
    path = Path(value)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(workspace.resolve()).as_posix()
        except ValueError:
            return "__OUTSIDE_WORKSPACE__/" + path.name
    normalized = Path(value).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def path_decision(contract: ChangeContract, path: str) -> tuple[bool, str]:
    if not contract.target_kind.requires_candidate_change:
        return False, f"target {contract.target_kind.value} is read-only"
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("__OUTSIDE_WORKSPACE__/"):
        return False, "path escapes workspace"
    if matches_any(normalized, contract.forbidden_paths):
        return False, f"path is forbidden by change contract: {normalized}"
    if matches_any(normalized, PROTECTED_GOVERNANCE):
        return False, f"path is protected Judge or evidence input: {normalized}"
    if not matches_any(normalized, contract.allowed_paths):
        return False, f"path is outside allowed_paths: {normalized}"
    return True, "allowed"


def extract_paths(tool_name: str, tool_input: dict[str, Any], workspace: Path) -> list[str]:
    paths: list[str] = []
    for key in ("file_path", "path", "filename", "target_file"):
        value = tool_input.get(key)
        if isinstance(value, str):
            paths.append(normalize_path(value, workspace))
    if tool_name in {"apply_patch", "Edit", "Write"}:
        patch = str(tool_input.get("patch") or tool_input.get("diff") or tool_input.get("content") or "")
        for match in re.finditer(r"(?:\*\*\* (?:Update|Add|Delete) File:|\+\+\+ b/|--- a/)\s*([^\n]+)", patch):
            paths.append(normalize_path(match.group(1), workspace))
    return sorted(set(path for path in paths if path))


def command_decision(command: str) -> tuple[bool, str]:
    for pattern in DESTRUCTIVE_COMMANDS:
        if re.search(pattern, command):
            return False, f"destructive command blocked: {pattern}"
    if not bootstrap_command_allowed(command):
        for pattern in SHELL_MUTATION_PATTERNS:
            if re.search(pattern, command):
                return False, "shell-based repository mutation is blocked; use structured Write/Edit/apply_patch"
    return True, "allowed"


def command_requires_contract(command: str) -> bool:
    stripped = command.strip()
    if not stripped:
        return False
    if bootstrap_command_allowed(stripped):
        return False
    return not any(re.search(pattern, stripped) for pattern in READ_ONLY_COMMAND_PATTERNS)


def bootstrap_command_allowed(command: str) -> bool:
    if not any(value in command for value in CONTRACT_BOOTSTRAP_COMMANDS):
        return False
    return re.search(
        r"\b(init|product-init|product-baseline|product-verify|contract-validate|contract-show|contract-approve|contract-configure|contract-begin|attest-review|contract-verify|contract-close|validate|show|approve|configure|begin|verify|close|status|profiles)\b",
        command,
    ) is not None


def parse_hook_input(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else {}
