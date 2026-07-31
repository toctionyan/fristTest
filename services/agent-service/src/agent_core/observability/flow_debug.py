from __future__ import annotations

"""Non-authoritative observability wrapper for Lifecycle graph nodes."""

from datetime import datetime, timezone
import os
import time
from typing import Any, Callable

from agent_core.storage.repositories.base import TraceRepository

from agent_core.utils.llm_debug import message_to_debug

STATE_KEYS = [
    "current_thread_id", "current_user_id", "current_role", "current_tenant_id",
    "turn_index", "current_user_input", "ledger_schema_version", "artifact_ledger", "ledger_snapshot",
    "current_turn_plan", "loop_plans", "pretool_shadow_plan", "pretool_shadow_comparisons", "execution_permits", "turn_match_proofs", "agent_loop_step", "agent_loop_max_steps", "agent_loop_seen_calls", "answer_protocol_retry", "deferred_terminal_calls", "execution_dispositions", "latest_execution_disposition", "model_mode_restriction", "model_call_budget", "model_call_trace",
    "task_board", "current_turn_task_ids", "action_queue", "action_gateway_result",
    "tool_trace", "tool_error", "active_draft_id", "transaction_reconciliation", "context_bundle", "context_health", "pending_confirmation_id", "pending_confirmation_version", "response_contract", "commit_authority", "approval_result", "offer_execution_result",
    "phase", "status", "current_final_answer", "current_ask_message", "sources", "summary", "answer_evidence_handles",
    "debug_current_run_id", "debug_llm_calls", "decision_chain", "state_contract_violations",
]


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        try:
            return _safe(value.model_dump())
        except Exception:
            pass
    return str(value)


def _messages_to_debug(messages: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages or []:
        try:
            out.append(message_to_debug(message))
        except Exception:
            out.append({"type": message.__class__.__name__, "content": str(message)})
    return out


def build_node_flow_step(node_name: str, before_state: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": output.get("debug_current_run_id") or before_state.get("debug_current_run_id") or "unknown",
        "node": node_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "thread_id": before_state.get("current_thread_id"),
        "user_id": before_state.get("current_user_id"),
        "input_state": {key: _safe(before_state.get(key)) for key in STATE_KEYS if key in before_state},
        "output_state": {key: _safe(value) for key, value in output.items() if key != "messages"},
        "added_messages": _messages_to_debug(output.get("messages") or []),
    }


def _persist(step: dict[str, Any], latency_ms: int, trace_repository: TraceRepository | None) -> None:
    if os.getenv("DEBUG_NODE_TRACE_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return
    if not step.get("thread_id") or trace_repository is None:
        return
    try:
        trace_repository.log_event(
            thread_id=str(step.get("thread_id")), user_id=str(step.get("user_id") or "") or None,
            event_type="graph_node", node=str(step.get("node")),
            input_data={"run_id": step.get("run_id"), "state": step.get("input_state")},
            output_data={"run_id": step.get("run_id"), "state": step.get("output_state"), "messages": step.get("added_messages")},
            latency_ms=latency_ms, trace_id=str(step.get("run_id")),
        )
    except Exception:
        return

def debug_node(
    node_name: str,
    node: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    state_validator: Callable[[str, dict[str, Any]], list[dict[str, str]]],
    trace_repository: TraceRepository | None,
):
    def wrapped(state: dict[str, Any]):
        start = time.monotonic()
        output = node(state)
        if not isinstance(output, dict):
            return output
        violations = state_validator(node_name, output)
        if violations and os.getenv("STATE_CONTRACT_MODE", "audit").lower() == "strict":
            raise RuntimeError(f"State contract violation in {node_name}: {violations}")
        if violations:
            output = {**output, "state_contract_violations": [*(state.get("state_contract_violations") or []), *violations]}
        _persist(build_node_flow_step(node_name, state, output), int((time.monotonic() - start) * 1000), trace_repository)
        return output
    wrapped.__name__ = f"debug_{node_name}_node"
    return wrapped
