from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from agent_core.observability.audit_turn_trace import normalize_turn_trace
from agent_core.context import ContextBundleBuilder
from agent_core.ledger import LEDGER_SCHEMA_VERSION, append_entries, find_handle, ledger_cards, scope_for_state
from agent_core.lifecycle.task_board import normalize_task_board
from agent_core.runtime.node_support import (
    append_decision as _append_decision,
    latest_human_text as _latest_human_text,
    max_loop_steps as _max_loop_steps,
)
from agent_core.transaction import TRANSACTION_CONTRACT_VERSION
from agent_core.transaction.active_draft import active_draft_patch, get_active_draft_id
from agent_core.lifecycle.goal_blockers import active_goal_blockers
from agent_core.lifecycle.state_schema import CURRENT_STATE_SCHEMA_VERSION, migrate_checkpoint_state

def prepare_agent_loop_turn_node(state: dict[str, Any]) -> dict[str, Any]:
    """Migrate the checkpoint to State v2 and reset current-turn ephemera.

    The runtime intentionally does not expire or mutate a durable transaction merely
    because a user sends free-form chat.  Chat cannot submit/authorize/write
    transaction data; the current draft remains queryable and the interaction
    runtime owns its own explicit cancel/submit controls.
    """
    state, migration_report = migrate_checkpoint_state(state)
    current_turn = int(state.get("turn_index") or 0) + 1
    active_draft_id = get_active_draft_id(state)
    transaction_context_hint = bool(
        active_draft_id
        or state.get("action_queue")
        or state.get("response_contract")
        or state.get("commit_authority")
    )
    ledger = list(state.get("artifact_ledger") or [])
    return {
        "state_schema_version": CURRENT_STATE_SCHEMA_VERSION,
        "state_migration": migration_report,
        "legacy_compatibility_metrics": dict(state.get("legacy_compatibility_metrics") or {}),
        "transaction_contract_version": TRANSACTION_CONTRACT_VERSION,
        "turn_index": current_turn,
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "current_user_input": None,
        "current_turn_plan": {
            "plan_id": f"turn-plan:{uuid4().hex}",
            "architecture": "customer_agent.runtime",
            "turn": current_turn,
            "effects": [],
            "semantic_authority": "model_candidate_only_runtime_verified",
        "not_future_semantic_authority": True,
        },
        "loop_plans": [],
        # Candidate semantics are current-turn ephemera.  The frozen contract
        # is rebuilt from the newest user message and becomes the only formal
        # semantic input for execution.  Legacy plans are compatibility views.
        "semantic_proposal": None,
        "frozen_semantic_contract": None,
        "frozen_plan_definition": None,
        "plan_run": None,
        "grounded_execution_plan": None,
        "pretool_shadow_plan": None,
        "pretool_execution_policy": None,
        "pretool_shadow_comparisons": [],
        "goal_blockers": active_goal_blockers(state),
        # GoalRecord is the durable semantic lifecycle. Terminal records must
        # remain available for audit/revision checks across later turns; the
        # Context projection independently exposes only active Goals to the
        # model, so preserving history here cannot steal semantic focus.
        "goal_records": [
            deepcopy(row)
            for row in list(state.get("goal_records") or [])
            if isinstance(row, dict)
        ],
        # GoalOutputRef is scoped to one frozen turn semantic contract. A new
        # user turn must not inherit a prior contract's planning evidence.
        "goal_output_refs": [],
        "focus_state": dict(state.get("focus_state") or {}) or None,
        "execution_permits": [],
        "turn_match_proofs": [],
        "agent_loop_step": 0,
        "agent_loop_max_steps": _max_loop_steps(),
        "agent_loop_seen_calls": [],
        "answer_protocol_retry": 0,
        "goal_declaration_retry": 0,
        "clarification_scope_retry": 0,
        "history_recall_evidence_binding": None,
        "deferred_terminal_calls": [],
        "execution_dispositions": [],
        "latest_execution_disposition": None,
        "model_mode_restriction": None,
        "action_queue": [],
        "action_gateway_result": None,
        "transaction_context_hint": transaction_context_hint,
        "transaction_context_blocked": False,
        **active_draft_patch(active_draft_id),
        "pending_confirmation_id": None,
        "pending_confirmation_version": None,
        "response_contract": None,
        "commit_authority": None,
        "offer_execution_result": None,
        "runtime_outcome": None,
        "presentation": None,
        "current_final_answer": None,
        "current_ask_message": None,
        "tool_trace": [],
        "sources": [],
        "tool_error": None,
        "current_turn_task_ids": [],
        "task_board": normalize_task_board(state.get("task_board") or []),
        "artifact_ledger": ledger,
        "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
        "phase": "agent_loop",
        "status": "Started",
        "debug_current_run_id": str(uuid4()),
        "debug_llm_calls": [],
        "decision_chain": [],
    }

def build_context_bundle_node(state: dict[str, Any], *, context_bundle_builder: ContextBundleBuilder) -> dict[str, Any]:
    ledger = list(state.get("artifact_ledger") or [])
    audit_log = normalize_turn_trace(state.get("conversation_event_log") or [])
    current = _latest_human_text(state)
    working = {**state, "artifact_ledger": ledger, "conversation_event_log": audit_log}
    bundle = context_bundle_builder.build(working)
    transaction_context_blocked = (
        str((bundle.get("context_health") or {}).get("transactions") or "ok") != "ok"
        and bool(state.get("transaction_context_hint"))
    )
    return {
        "current_user_input": current,
        "artifact_ledger": ledger,
        "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
        "task_board": normalize_task_board(state.get("task_board") or []),
        "conversation_event_log": audit_log,
        # Audit metadata is intentionally separate from semantic context. It is
        # available only for explicit audit inspection, never as a target selector.
        "audit_snapshot": list((bundle.get("omitted_context_audit") or {}).get("audit_index") or []),
        "state_schema_version": CURRENT_STATE_SCHEMA_VERSION,
        "transaction_contract_version": TRANSACTION_CONTRACT_VERSION,
        "context_bundle": bundle,
        "context_health": dict(bundle.get("context_health") or {}),
        "transaction_context_blocked": transaction_context_blocked,
        "phase": "agent_loop",
        "decision_chain": _append_decision(
            state,
            stage="build_context_bundle",
            decision="context_bundle_built",
            details={
                "recent_message_count": len(bundle.get("recent_conversation_window") or []),
                "visible_result_ref_count": len(bundle.get("visible_result_refs") or []),
                "transaction_count": len(bundle.get("active_transaction_state") or []),
                "semantic_owner": bundle.get("semantic_owner"),
                "runtime_auto_select_target": bundle.get("runtime_auto_select_target"),
                "runtime_auto_switch_target": bundle.get("runtime_auto_switch_target"),
            },
        ),
    }
