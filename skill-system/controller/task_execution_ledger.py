from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from task_run import TaskRunStore


TASK_EXECUTION_LEDGER_SCHEMA = "task-execution-ledger@1"
ATTEMPT_STATUSES = frozenset({"PENDING", "RUNNING", "PASS", "FAIL", "SKIPPED", "BLOCKED"})


class TaskExecutionLedgerError(ValueError):
    """Raised when task execution-history evidence is malformed."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _stage_id(value: object) -> str:
    stage = _text(value).lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", stage):
        raise TaskExecutionLedgerError("stage_id must be a stable lowercase identifier")
    return stage


def _status(value: object) -> str:
    status = _text(value).upper()
    if status not in ATTEMPT_STATUSES:
        raise TaskExecutionLedgerError(f"unsupported attempt status: {status}")
    return status


def _metadata(task: Mapping[str, Any]) -> Mapping[str, Any]:
    value = task.get("metadata")
    return value if isinstance(value, Mapping) else {}


def read_execution_ledger(task: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _metadata(task)
    raw = metadata.get("task_execution_ledger")
    if raw is None:
        return {
            "schema": TASK_EXECUTION_LEDGER_SCHEMA,
            "stages": [],
            "attempts": [],
            "authority_effect": False,
            "production_closed": False,
        }
    if not isinstance(raw, Mapping) or raw.get("schema") != TASK_EXECUTION_LEDGER_SCHEMA:
        raise TaskExecutionLedgerError("TaskRun execution ledger is malformed")
    stages = raw.get("stages")
    attempts = raw.get("attempts")
    if not isinstance(stages, list) or not isinstance(attempts, list):
        raise TaskExecutionLedgerError("TaskRun execution ledger stages/attempts must be arrays")
    if raw.get("authority_effect") is not False or raw.get("production_closed") is not False:
        raise TaskExecutionLedgerError("execution ledger cannot acquire authority or production closure")
    return {
        "schema": TASK_EXECUTION_LEDGER_SCHEMA,
        "stages": [dict(row) for row in stages if isinstance(row, Mapping)],
        "attempts": [dict(row) for row in attempts if isinstance(row, Mapping)],
        "authority_effect": False,
        "production_closed": False,
    }


def set_execution_plan(
    store: TaskRunStore,
    *,
    stages: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Persist the visible task plan without changing TaskRun lifecycle authority."""

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(stages, start=1):
        stage = _stage_id(raw.get("id"))
        if stage in seen:
            raise TaskExecutionLedgerError(f"duplicate stage_id: {stage}")
        seen.add(stage)
        label = _text(raw.get("label")) or stage
        normalized.append(
            {
                "id": stage,
                "label": label,
                "order": index,
                "required": raw.get("required") is not False,
            }
        )
    if not normalized:
        raise TaskExecutionLedgerError("execution plan requires at least one stage")

    ledger = read_execution_ledger(store.payload)
    prior_ids = {row.get("id") for row in ledger["stages"]}
    if prior_ids and prior_ids != {row["id"] for row in normalized}:
        raise TaskExecutionLedgerError(
            "execution plan stage identity cannot be silently replaced after execution has started"
        )
    ledger["stages"] = normalized
    store.set_metadata(task_execution_ledger=ledger)
    return ledger


def record_execution_attempt(
    store: TaskRunStore,
    *,
    stage_id: str,
    status: str,
    evidence_refs: Iterable[str],
    detail: str | None = None,
    recoverable: bool = True,
    human_required: bool = False,
    attempt: int | None = None,
) -> dict[str, Any]:
    """Append one immutable execution attempt to the TaskRun metadata ledger.

    Failed attempts are never deleted when a later attempt succeeds. This record is
    evidence only: it cannot mark a TaskRun completion condition, grant writes,
    merge, deploy, or production authority.
    """

    ledger = read_execution_ledger(store.payload)
    stage = _stage_id(stage_id)
    planned = {str(row.get("id")): row for row in ledger["stages"]}
    if planned and stage not in planned:
        raise TaskExecutionLedgerError(f"attempt stage is not in execution plan: {stage}")
    refs = [str(ref).strip() for ref in evidence_refs if str(ref).strip()]
    if not refs:
        raise TaskExecutionLedgerError("execution attempt requires durable evidence_refs")
    existing = [row for row in ledger["attempts"] if row.get("stage_id") == stage]
    expected_attempt = len(existing) + 1
    resolved_attempt = int(attempt) if attempt is not None else expected_attempt
    if resolved_attempt != expected_attempt:
        raise TaskExecutionLedgerError(
            f"execution attempt sequence mismatch for {stage}: expected={expected_attempt} actual={resolved_attempt}"
        )
    row = {
        "sequence": len(ledger["attempts"]) + 1,
        "stage_id": stage,
        "label": _text(planned.get(stage, {}).get("label")) or stage,
        "attempt": resolved_attempt,
        "status": _status(status),
        "detail": _text(detail) or None,
        "evidence_ref": refs[0],
        "evidence_refs": refs,
        "recoverable": bool(recoverable),
        "human_required": bool(human_required),
        "authority_effect": False,
        "production_closed": False,
    }
    ledger["attempts"].append(row)
    store.set_metadata(task_execution_ledger=ledger)
    return row


def projection_inputs(task: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return execution-progress compatible plan and attempt rows."""

    ledger = read_execution_ledger(task)
    attempts = ledger["attempts"]
    latest_by_stage: dict[str, Mapping[str, Any]] = {}
    for row in attempts:
        latest_by_stage[str(row.get("stage_id"))] = row
    planned: list[dict[str, Any]] = []
    for row in sorted(ledger["stages"], key=lambda item: int(item.get("order") or 0)):
        stage = str(row.get("id") or "")
        latest = latest_by_stage.get(stage)
        planned.append(
            {
                "id": stage,
                "label": str(row.get("label") or stage),
                "status": str(latest.get("status") if latest else "PENDING"),
                "detail": str(latest.get("detail") or "") if latest else "",
                "evidence_ref": str(latest.get("evidence_ref") or "") if latest else "",
            }
        )
    return planned, [dict(row) for row in attempts]
