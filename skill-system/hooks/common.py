
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

CONTROLLER = Path(__file__).resolve().parents[1]/"controller"
if str(CONTROLLER) not in sys.path: sys.path.insert(0,str(CONTROLLER))
from contract import load_contract  # type: ignore
from scope_guard import bootstrap_command_allowed, command_decision, extract_paths, parse_hook_input, path_decision  # type: ignore


def workspace_from(payload: dict[str, Any]) -> Path:
    raw=payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    start=Path(str(raw)).resolve()
    for candidate in (start,*start.parents):
        if (candidate/"skill-system").is_dir(): return candidate
    return start


def tool_info(payload: dict[str, Any]) -> tuple[str,dict[str,Any]]:
    name=str(payload.get("tool_name") or payload.get("tool") or "")
    data=payload.get("tool_input") or payload.get("input") or {}
    return name, data if isinstance(data,dict) else {}


def command_from(tool_input: dict[str, Any]) -> str:
    return str(tool_input.get("command") or tool_input.get("cmd") or "")


def deny(reason: str) -> int:
    event="PreToolUse"
    print(json.dumps({"hookSpecificOutput":{"hookEventName":event,"permissionDecision":"deny","permissionDecisionReason":reason}},ensure_ascii=False))
    return 0


def allow(context: str|None=None) -> int:
    if context:
        print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","additionalContext":context}},ensure_ascii=False))
    return 0
