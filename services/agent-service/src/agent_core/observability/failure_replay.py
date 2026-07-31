from __future__ import annotations

"""Deterministic, redacted runtime failure envelopes for regression promotion."""

from hashlib import sha256
import json
from typing import Any

from agent_core.kernel.plan_projection_contract import read_plan_projection
from agent_core.model_calls.gateway import classify_model_failure
from agent_core.observability.redaction import redact_for_persistence


def _digest(value: str, *, domain: str) -> str:
    return sha256(f"failure-replay:{domain}:{value}".encode("utf-8")).hexdigest()


def _safe_trace_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in list(state.get("tool_trace") or [])[-12:]:
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        rows.append({
            "name": str(item.get("name") or ""),
            "ok": bool(result.get("ok")),
            "code": str(result.get("code") or "") or None,
            "result_keys": sorted(str(key) for key in data),
            "runtime_outcome_type": str((result.get("runtime_outcome") or {}).get("outcome_type") or "")
            if isinstance(result.get("runtime_outcome"), dict) else None,
        })
    return rows


def build_failure_replay(
    *, state: dict[str, Any], stage: str, error_type: str, error_message: str
) -> dict[str, Any]:
    """Build a replayable shape without persisting raw text, identity or secrets."""
    scope_source = "|".join((
        str(state.get("current_tenant_id") or ""),
        str(state.get("current_user_id") or ""),
        str(state.get("current_thread_id") or ""),
    ))
    workflow = read_plan_projection(state) or {}
    messages = [
        message for message in list(state.get("messages") or [])
        if message.__class__.__name__ in {"HumanMessage", "AIMessage", "ToolMessage"}
    ]
    input_text = str(state.get("current_user_input") or "")
    core = {
        "schema_version": 1,
        "stage": str(stage or "unknown"),
        "error": {
            "type": str(error_type or "UnknownError"),
            "category": classify_model_failure(error_message, error_type=error_type),
            "message_digest": _digest(str(error_message or ""), domain="error-message"),
        },
        "scope_fingerprint": _digest(scope_source, domain="scope"),
        "turn_index": int(state.get("turn_index") or 0),
        "input_shape": {
            "character_count": len(input_text),
            "line_count": max(1, input_text.count("\n") + 1) if input_text else 0,
            "present": bool(input_text),
        },
        "message_shape": {
            "count": len(messages),
            "types": [message.__class__.__name__ for message in messages[-24:]],
        },
        "workflow": {
            "level": str(workflow.get("level") or "") or None,
            "status": str(workflow.get("status") or "") or None,
            "goal_count": len(workflow.get("goals") or []),
            "step_statuses": [
                str(step.get("status") or "")
                for step in list(workflow.get("steps") or [])
                if isinstance(step, dict)
            ],
        },
        "tool_trace": _safe_trace_rows(state),
    }
    # Defense in depth for future fields: persistence redaction is applied even
    # though the envelope intentionally contains no raw personal values.
    safe_core = redact_for_persistence(core)
    canonical = json.dumps(safe_core, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return {**safe_core, "fingerprint": sha256(canonical.encode("utf-8")).hexdigest()}


__all__ = ["build_failure_replay"]
