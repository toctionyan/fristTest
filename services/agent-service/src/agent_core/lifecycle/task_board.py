from __future__ import annotations

"""Soft task-board operations for the continuous Agent loop.

Task board entries are deliberately non-authoritative organization state.  The
module accepts verified Ledger/scope inputs from the node and never obtains
business data itself, keeping it isolated from transaction or planner state.
"""

from copy import deepcopy
from typing import Any
from uuid import uuid4

from agent_core.lifecycle.protocol import MAX_WORK_ITEMS
from agent_core.ledger import find_handle

VALID_TASK_STATUSES = {"active", "paused", "completed", "superseded"}


def task_board_cards(tasks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        rows.append({
            "task_id": task.get("task_id"),
            "title": task.get("title"),
            "status": task.get("status"),
            "target_handles": list(task.get("target_handles") or []),
            "updated_turn": task.get("updated_turn"),
            "soft_state": True,
        })
    return rows[-MAX_WORK_ITEMS:]


def normalize_task_board(tasks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for raw in tasks or []:
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("task_id") or "")
        if not task_id:
            continue
        row = deepcopy(raw)
        if str(row.get("status") or "active") not in VALID_TASK_STATUSES:
            row["status"] = "active"
        row["target_handles"] = list(dict.fromkeys(str(v) for v in row.get("target_handles") or [] if str(v)))
        row["soft_state"] = True
        previous = by_id.get(task_id)
        if previous is None or int(row.get("updated_turn") or 0) >= int(previous.get("updated_turn") or 0):
            by_id[task_id] = row
    return list(by_id.values())[-MAX_WORK_ITEMS:]


def pause_interrupted_confirmation(tasks: list[dict[str, Any]] | None, *, handle: str, turn: int) -> list[dict[str, Any]]:
    board = normalize_task_board(tasks)
    for task in board:
        if handle in set(task.get("action_handles") or []) and task.get("status") == "active":
            task["status"] = "paused"
            task["status_note"] = "新的自由输入到达，旧确认不再自动继续。"
            task["updated_turn"] = turn
    return board


def complete_tasks_from_terminal(tasks: list[dict[str, Any]] | None, *, call: dict[str, Any], turn: int) -> list[dict[str, Any]]:
    board = normalize_task_board(tasks)
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    for task_id in args.get("task_ids") or []:
        for row in board:
            if row.get("task_id") == str(task_id) and row.get("status") == "active":
                row["status"] = "completed"
                row["updated_turn"] = turn
                row["status_note"] = "已由本轮最终回答完成。"
    return board


def apply_task_operation(
    *,
    tasks: list[dict[str, Any]] | None,
    args: dict[str, Any],
    user_text: str,
    ledger: list[dict[str, Any]],
    scope: dict[str, str],
    turn: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Apply one soft task operation using only already verified target handles."""
    operation = str(args.get("operation") or "")
    evidence = str(args.get("evidence_span") or "")
    board = normalize_task_board(tasks)
    if not evidence or evidence not in user_text:
        return {"ok": False, "code": "TASK_EVIDENCE_NOT_IN_CURRENT_TURN", "message": "软工作项变化必须引用当前用户原话。"}, board, []
    targets = [str(v) for v in args.get("target_handles") or [] if str(v)]
    for handle in targets:
        if find_handle(ledger, handle, scope=scope, allowed_kinds={"artifact", "view", "result", "offer", "eligibility"}, active_only=False) is None:
            return {"ok": False, "code": "TASK_TARGET_HANDLE_INVALID", "message": "软工作项不能引用不存在或越权的句柄。"}, board, []
    task_id = str(args.get("task_id") or "")
    title = str(args.get("title") or "").strip()
    note = str(args.get("status_note") or "").strip()
    if operation == "create":
        if not title:
            return {"ok": False, "code": "TASK_TITLE_REQUIRED", "message": "创建工作项需要标题。"}, board, []
        task_id = task_id or f"task_{uuid4().hex[:12]}"
        board.append({
            "task_id": task_id,
            "title": title,
            "status": "active",
            "target_handles": targets,
            "created_turn": turn,
            "updated_turn": turn,
            "status_note": note,
            "soft_state": True,
        })
        return {"ok": True, "data": {"task_id": task_id, "status": "active", "soft_state": True}}, normalize_task_board(board), [task_id]
    existing = next((row for row in board if str(row.get("task_id") or "") == task_id), None)
    if existing is None:
        return {"ok": False, "code": "TASK_NOT_FOUND", "message": "软工作项不存在或已被清理。"}, board, []
    if operation == "update":
        if title:
            existing["title"] = title
        if targets:
            existing["target_handles"] = targets
        if note:
            existing["status_note"] = note
    elif operation in {"complete", "pause", "resume", "supersede"}:
        existing["status"] = {"complete": "completed", "pause": "paused", "resume": "active", "supersede": "superseded"}[operation]
        if note:
            existing["status_note"] = note
    else:
        return {"ok": False, "code": "TASK_OPERATION_INVALID", "message": "未知软工作项操作。"}, board, []
    existing["updated_turn"] = turn
    return {"ok": True, "data": {"task_id": task_id, "status": existing.get("status"), "soft_state": True}}, normalize_task_board(board), [task_id]
