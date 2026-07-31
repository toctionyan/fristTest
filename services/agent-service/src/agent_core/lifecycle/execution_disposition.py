from __future__ import annotations

"""Finite runtime path classification after every formal tool execution."""

from typing import Any

from agent_core.lifecycle.candidate_repair import is_candidate_repairable_result

DISPOSITIONS = {
    "continue",
    "needs_clarification",
    "business_conclusion",
    "unsupported",
    "retry_infrastructure",
    "reconcile_submission",
}


def _handles(result: dict[str, Any]) -> list[str]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    values: list[str] = []
    for key, value in data.items():
        if key.endswith("_handle") and value:
            values.append(str(value))
        elif key.endswith("_handles") and isinstance(value, list):
            values.extend(str(item) for item in value if item)
    return list(dict.fromkeys(values))


def _budget_snapshot(state: dict[str, Any], tool_signature: str) -> dict[str, Any]:
    """Snapshot guard state for this exact call signature, not a fake focus metric."""
    seen = [str(item) for item in (state.get("agent_loop_seen_calls") or [])]
    return {
        "model_tool_cycles_used": int(state.get("agent_loop_step") or 0),
        "max_model_tool_cycles": int(state.get("agent_loop_max_steps") or 0),
        "same_signature_executions": seen.count(str(tool_signature)),
    }


def _make(
    disposition: str,
    *,
    reason_code: str,
    state: dict[str, Any],
    tool_signature: str,
    result: dict[str, Any],
    allowed_model_modes: list[str],
    may_execute_user_effect: bool,
    runtime_action: str,
    invalidated_result_refs: list[str] | None = None,
) -> dict[str, Any]:
    if disposition not in DISPOSITIONS:
        raise ValueError(f"unknown execution disposition: {disposition}")
    handles = _handles(result)
    return {
        "disposition": disposition,
        "reason_code": str(reason_code or "unclassified"),
        "result_ref": handles[0] if handles else None,
        "invalidated_result_refs": list(invalidated_result_refs or []),
        "allowed_model_modes": list(allowed_model_modes),
        "may_execute_user_effect": bool(may_execute_user_effect),
        "auto_target_switch": False,
        "runtime_action": str(runtime_action),
        "tool_signature": tool_signature,
        "loop_budget_snapshot": _budget_snapshot(state, tool_signature),
    }


def classify_execution_disposition(
    *, state: dict[str, Any], tool_name: str, tool_signature: str, result: dict[str, Any]
) -> dict[str, Any]:
    """Classify execution facts only; never reinterpret user semantics."""
    code = str(result.get("code") or "")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    outcome = result.get("runtime_outcome") if isinstance(result.get("runtime_outcome"), dict) else {}
    outcome_type = str(outcome.get("outcome_type") or "")

    # Schema/span/goal-binding failures reject only the model's candidate.
    # No business effect was authorized, so keep the bounded observation loop
    # open and let the model repair the exact contract error from ToolMessage.
    if is_candidate_repairable_result(result):
        return _make(
            "continue", reason_code=code, state=state,
            tool_signature=tool_signature, result=result,
            allowed_model_modes=["respond", "observe", "prepare"],
            may_execute_user_effect=False, runtime_action="repair_rejected_candidate",
        )

    if code in {"SUBMISSION_UNKNOWN", "RECONCILIATION_REQUIRED"} or outcome_type == "submission_unknown":
        return _make(
            "reconcile_submission", reason_code=code or "submission_unknown", state=state,
            tool_signature=tool_signature, result=result, allowed_model_modes=["explain"],
            may_execute_user_effect=False, runtime_action="reconcile_same_idempotency_key",
        )
    if code in {"UNKNOWN_OR_UNSUPPORTED_TOOL", "UNSUPPORTED_CAPABILITY", "UNSUPPORTED_TARGET_CARDINALITY"} or outcome_type in {"unsupported_capability", "unsupported_cardinality"}:
        return _make(
            "unsupported", reason_code=code or outcome_type or "unsupported", state=state,
            tool_signature=tool_signature, result=result, allowed_model_modes=["respond", "handoff"],
            may_execute_user_effect=False, runtime_action="finalize_or_handoff",
        )
    if code in {
        "CAPABILITY_PARAMETERIZATION_INCOMPLETE", "CAPABILITY_SEMANTIC_CLARIFICATION_REQUIRED",
        "CONTEXT_TARGET_NOT_UNIQUE", "NEED_TRANSACTION_SELECTION", "DUPLICATE_OBSERVATION_SUPPRESSED",
    } or outcome_type == "clarification":
        invalidated = _handles(result) if "REF" in code else []
        return _make(
            "needs_clarification", reason_code=code or outcome_type or "clarification_required", state=state,
            tool_signature=tool_signature, result=result, allowed_model_modes=["clarify", "safe_grounding", "respond"],
            may_execute_user_effect=False, runtime_action="return_limited_observation",
            invalidated_result_refs=invalidated,
        )
    if code in {"TRANSPORT_RETRY_EXHAUSTED", "BUSINESS_SERVICE_UNAVAILABLE"} or outcome_type == "system_unavailable":
        return _make(
            "retry_infrastructure", reason_code=code or outcome_type or "transport_failure", state=state,
            tool_signature=tool_signature, result=result, allowed_model_modes=["explain"],
            may_execute_user_effect=False, runtime_action="adapter_retry_exhausted",
        )
    if outcome_type in {"preview_rejected", "transaction_status", "commit", "failure"} or data.get("business_conclusion") is True:
        return _make(
            "business_conclusion", reason_code=code or outcome_type or "business_conclusion", state=state,
            tool_signature=tool_signature, result=result, allowed_model_modes=["respond"],
            may_execute_user_effect=False, runtime_action="explain_business_conclusion",
        )
    return _make(
        "continue", reason_code=code or "observation_available", state=state,
        tool_signature=tool_signature, result=result, allowed_model_modes=["respond", "observe", "prepare"],
        may_execute_user_effect=bool(result.get("ok")), runtime_action="return_observation_to_model",
    )


def latest_disposition(state: dict[str, Any]) -> dict[str, Any] | None:
    value = state.get("latest_execution_disposition")
    return dict(value) if isinstance(value, dict) else None
