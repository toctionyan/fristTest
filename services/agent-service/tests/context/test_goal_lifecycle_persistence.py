from __future__ import annotations

from agent_core.context.state_projection import goal_records_context_projection
from agent_core.kernel.state_schema_contract import CURRENT_STATE_SCHEMA_VERSION
from agent_core.lifecycle.context_runtime import prepare_agent_loop_turn_node


def test_new_turn_preserves_terminal_goal_history_without_reexposing_it_as_active_context() -> None:
    cancelled = {
        "goal_id": "cancel_goal",
        "description": "取消鼠标订单",
        "lifecycle": "CANCELLED",
        "revision": 4,
        "created_turn": 1,
        "updated_turn": 2,
        "last_lifecycle_operation": "SET_GOAL_LIFECYCLE",
        "last_change_evidence_span": "不取消了",
    }
    active = {
        "goal_id": "active_goal",
        "description": "查询物流",
        "lifecycle": "ACTIVE",
        "revision": 1,
        "created_turn": 2,
        "updated_turn": 2,
    }
    state = {
        "state_schema_version": CURRENT_STATE_SCHEMA_VERSION,
        "turn_index": 2,
        "current_thread_id": "thread-a",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_role": "customer",
        "artifact_ledger": [],
        "goal_blockers": [],
        "goal_records": [cancelled, active],
        "task_board": [],
        "focused_draft_id": None,
        "active_draft_id": None,
    }

    patch = prepare_agent_loop_turn_node(state)

    records = {
        str(row.get("goal_id") or ""): row
        for row in patch.get("goal_records") or []
        if isinstance(row, dict)
    }
    assert set(records) == {"cancel_goal", "active_goal"}
    assert records["cancel_goal"]["lifecycle"] == "CANCELLED"
    assert records["cancel_goal"]["last_change_evidence_span"] == "不取消了"
    assert records["cancel_goal"] is not cancelled

    projected = goal_records_context_projection({**state, **patch})
    assert [row["goal_id"] for row in projected] == ["active_goal"]
    assert all(row.get("lifecycle") != "CANCELLED" for row in projected)
