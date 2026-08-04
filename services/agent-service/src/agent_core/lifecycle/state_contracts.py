from __future__ import annotations

from typing import Any
from agent_core.lifecycle.state import State
from agent_core.lifecycle.state_schema import RETIRED_TOP_LEVEL_FIELDS

GROUP_TO_KEYS: dict[str, set[str]] = {
    "identity": {"current_thread_id", "current_user_id", "current_role", "current_tenant_id", "current_subject"},
    "messages": {"messages", "summary"},
    "ledger": {"state_schema_version", "state_migration", "legacy_compatibility_metrics", "transaction_contract_version", "turn_index", "current_user_input", "ledger_schema_version", "artifact_ledger", "ledger_snapshot"},
    "context": {"context_bundle", "context_health", "transaction_context_hint", "transaction_context_blocked"},
    # RuntimeOutcome and canonical Presentation are terminal decision products,
    # not ContextBundle data. Query, transaction and finalizer nodes may write
    # them after producing a verified customer-visible conclusion.
    "outcome": {"runtime_outcome", "presentation"},
    "loop": {"current_turn_plan", "loop_plans", "capability_surface", "execution_permits", "turn_match_proofs", "agent_loop_step", "agent_loop_max_steps", "agent_loop_seen_calls", "answer_protocol_retry", "goal_declaration_retry", "clarification_scope_retry", "history_recall_evidence_binding", "deferred_terminal_calls", "execution_dispositions", "latest_execution_disposition", "model_mode_restriction", "model_call_budget", "model_call_trace"},
    "workflow": {"semantic_proposal", "frozen_semantic_contract", "frozen_plan_definition", "plan_run", "grounded_execution_plan", "pretool_shadow_plan", "pretool_execution_policy", "pretool_shadow_comparisons", "goal_blockers", "goal_records", "goal_output_refs", "focus_state"},
    "task_board": {"task_board", "current_turn_task_ids"},
    "transaction": {"action_queue", "action_gateway_result", "focused_draft_id", "active_draft_id", "pending_confirmation_id", "pending_confirmation_version", "response_contract", "commit_authority", "approval_result", "offer_execution_result", "transaction_reconciliation"},
    "audit": {"conversation_event_log", "audit_snapshot"},
    "tool_runtime": {"tool_trace", "tool_error", "sources", "answer_evidence_handles"},
    "answer": {"phase", "status", "current_final_answer", "current_ask_message"},
    "debug": {"debug_current_run_id", "debug_llm_calls", "decision_chain", "state_contract_violations"},
}
KEY_TO_GROUP = {key: group for group, keys in GROUP_TO_KEYS.items() for key in keys}
NODE_ALLOWED_GROUPS: dict[str, set[str]] = {
    "prepare_turn": {"ledger", "context", "outcome", "loop", "workflow", "task_board", "transaction", "tool_runtime", "answer", "debug"},
    "build_context_bundle": {"ledger", "context", "outcome", "task_board", "audit", "answer", "debug"},
    "agent_loop": {"messages", "ledger", "outcome", "loop", "workflow", "task_board", "transaction", "answer", "tool_runtime", "debug"},
    "validate_and_execute": {"messages", "ledger", "context", "outcome", "loop", "workflow", "task_board", "transaction", "tool_runtime", "answer", "debug"},
    "classify_execution_disposition": {"loop", "answer", "debug"},
    "reconcile_submission": {"ledger", "transaction", "outcome", "loop", "tool_runtime", "answer", "debug"},
    "action_gateway": {"ledger", "transaction", "outcome", "loop", "tool_runtime", "answer", "debug"},
    "action_confirmation": {"ledger", "transaction", "outcome", "answer", "debug"},
    "commit_action": {"ledger", "transaction", "outcome", "loop", "tool_runtime", "answer", "debug"},
    "finalize_turn": {"messages", "ledger", "audit", "outcome", "workflow", "answer", "debug"},
    "trim_raw_messages": {"messages"},
    "persist_and_stream": {"debug"},
}


def validate_state_update(node_name: str, update: dict[str, Any]) -> list[dict[str, str]]:
    allowed = NODE_ALLOWED_GROUPS.get(node_name)
    if allowed is None:
        return [{"type": "unknown_node", "node": node_name, "key": key} for key in update]
    violations: list[dict[str, str]] = []
    for key in update:
        if key in RETIRED_TOP_LEVEL_FIELDS:
            violations.append({"type": "retired_state_key", "node": node_name, "key": key})
            continue
        group = KEY_TO_GROUP.get(key)
        if group is None:
            violations.append({"type": "unclassified_state_key", "node": node_name, "key": key})
        elif group not in allowed:
            violations.append({"type": "group_not_allowed", "node": node_name, "key": key, "group": group})
    return violations


def validate_state_schema_coverage() -> list[str]:
    return sorted(set(State.__annotations__) - set(KEY_TO_GROUP))
