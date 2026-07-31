from __future__ import annotations

from agent_core.lifecycle.task_board import apply_task_operation, normalize_task_board, pause_interrupted_confirmation, task_board_cards


def _scope(thread_id: str = "thread-a") -> dict[str, str]:
    return {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": thread_id}


def test_same_thread_task_can_pause_for_interrupt_and_resume_later():
    result, board, ids = apply_task_operation(
        tasks=[],
        args={"operation": "create", "task_id": "task-refund", "title": "鼠标退款", "evidence_span": "申请鼠标退款", "status_note": "等待原因"},
        user_text="申请鼠标退款",
        ledger=[],
        scope=_scope(),
        turn=1,
    )
    assert result["ok"] is True
    assert ids == ["task-refund"]
    board[0]["action_handles"] = ["draft:refund:10003"]

    board = pause_interrupted_confirmation(board, handle="draft:refund:10003", turn=2)
    assert board[0]["status"] == "paused"

    result, board, _ = apply_task_operation(
        tasks=board,
        args={"operation": "create", "task_id": "task-logistics", "title": "耳机物流", "evidence_span": "查一下耳机物流"},
        user_text="先查一下耳机物流",
        ledger=[],
        scope=_scope(),
        turn=2,
    )
    assert result["ok"] is True
    assert {row["status"] for row in board} == {"paused", "active"}

    result, board, _ = apply_task_operation(
        tasks=board,
        args={"operation": "resume", "task_id": "task-refund", "evidence_span": "继续刚才那个", "status_note": "用户要求恢复退款任务"},
        user_text="继续刚才那个",
        ledger=[],
        scope=_scope(),
        turn=3,
    )
    assert result["ok"] is True
    resumed = next(row for row in board if row["task_id"] == "task-refund")
    assert resumed["status"] == "active"
    assert resumed["updated_turn"] == 3


def test_task_board_cards_remain_soft_non_authoritative_state():
    board = normalize_task_board([
        {"task_id": "a", "title": "退款", "status": "active", "target_handles": ["result:1", "result:1"], "updated_turn": 2},
        {"task_id": "b", "title": "物流", "status": "unknown", "target_handles": [], "updated_turn": 1},
    ])
    cards = task_board_cards(board)
    assert cards[0]["soft_state"] is True
    assert cards[0]["target_handles"] == ["result:1"]
    assert cards[1]["status"] == "active"


def test_task_operation_cannot_reference_invisible_or_cross_scope_handle():
    result, board, ids = apply_task_operation(
        tasks=[],
        args={"operation": "create", "task_id": "bad", "title": "越权任务", "target_handles": ["result:other-thread"], "evidence_span": "处理这个"},
        user_text="处理这个",
        ledger=[],
        scope=_scope("thread-a"),
        turn=1,
    )
    assert result["ok"] is False
    assert result["code"] == "TASK_TARGET_HANDLE_INVALID"
    assert board == []
    assert ids == []

