from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from task_run import TaskRunStore


TASK_EXECUTION_LEDGER_SCHEMA = "task-execution-ledger@1"
ATTEMPT_STATUSES = frozenset({"PENDING", "RUNNING", "PASS", "FAIL", "SKIPPED", "BLOCKED"})
_LOCAL_GATE_ORDER = ("targeted", "module", "static", "quick", "review", "scope")


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


def _terminal_attempt(
    *,
    sequence: int,
    stage_id: str,
    label: str,
    attempt: int,
    status: str,
    evidence_refs: Iterable[object],
    detail: str | None = None,
    recoverable: bool = True,
    human_required: bool = False,
) -> dict[str, Any]:
    refs = [str(ref).strip() for ref in evidence_refs if str(ref).strip()]
    evidence_ref = refs[0] if refs else f"task-checkpoint:{sequence}"
    return {
        "sequence": sequence,
        "stage_id": stage_id,
        "label": label,
        "attempt": attempt,
        "status": _status(status),
        "detail": _text(detail) or None,
        "evidence_ref": evidence_ref,
        "evidence_refs": refs or [evidence_ref],
        "recoverable": bool(recoverable),
        "human_required": bool(human_required),
        "authority_effect": False,
        "production_closed": False,
    }


def _local_first_projection_inputs(
    task: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Reconstruct durable local-first history from existing TaskRun checkpoints.

    Local-first governance already persisted every gate result and CI terminal
    checkpoint before this execution ledger existed. Reconstructing those records is
    read-only and prevents historical REDs from disappearing merely because an older
    TaskRun lacks `task_execution_ledger` metadata.
    """

    metadata = _metadata(task)
    local = metadata.get("local_first")
    if not isinstance(local, Mapping):
        return None

    labels = {
        "targeted": "Local targeted validation",
        "module": "Local module validation",
        "static": "Local static validation",
        "quick": "Local quick validation",
        "review": "Local independent review",
        "scope": "Local scope validation",
    }
    planned = [
        {
            "id": f"local-{gate}",
            "label": labels[gate],
            "status": "PENDING",
            "detail": "",
            "evidence_ref": "",
        }
        for gate in _LOCAL_GATE_ORDER
    ]
    planned.append(
        {
            "id": "ci-certification",
            "label": "Exact candidate CI certification",
            "status": "PENDING",
            "detail": "",
            "evidence_ref": "",
        }
    )

    attempts: list[dict[str, Any]] = []
    attempt_counts: dict[str, int] = {}
    checkpoints = task.get("checkpoints") if isinstance(task.get("checkpoints"), list) else []
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, Mapping):
            continue
        sequence = int(checkpoint.get("sequence") or len(attempts) + 1)
        phase = _text(checkpoint.get("phase"))
        row_metadata = checkpoint.get("metadata") if isinstance(checkpoint.get("metadata"), Mapping) else {}
        refs = checkpoint.get("evidence_refs") if isinstance(checkpoint.get("evidence_refs"), list) else []
        gate = _text(row_metadata.get("gate")).lower()
        result = _text(row_metadata.get("result")).upper()
        if gate in _LOCAL_GATE_ORDER and result in {"PASS", "FAIL"}:
            stage = f"local-{gate}"
            attempt_counts[stage] = attempt_counts.get(stage, 0) + 1
            attempts.append(
                _terminal_attempt(
                    sequence=sequence,
                    stage_id=stage,
                    label=labels[gate],
                    attempt=attempt_counts[stage],
                    status=result,
                    evidence_refs=refs,
                    detail=phase,
                    recoverable=result == "FAIL",
                    human_required=False,
                )
            )
            continue

        ci_status: str | None = None
        if phase == "CI_CERTIFICATION_GREEN":
            ci_status = "PASS"
        elif phase in {
            "CI_FAILURE_RETURNED_TO_PATCH_OWNER",
            "CI_RELIABILITY_RETRY_PENDING",
            "CI_PLATFORM_OR_AUTHORITY_BLOCKED",
            "CI_TRANSPORT_FAILURE_RECONCILED",
        }:
            ci_status = "FAIL"
        if ci_status:
            stage = "ci-certification"
            attempt_counts[stage] = attempt_counts.get(stage, 0) + 1
            decision_kind = _text(row_metadata.get("kind"))
            reason = _text(row_metadata.get("reason"))
            attempts.append(
                _terminal_attempt(
                    sequence=sequence,
                    stage_id=stage,
                    label="Exact candidate CI certification",
                    attempt=attempt_counts[stage],
                    status=ci_status,
                    evidence_refs=refs,
                    detail=reason or decision_kind or phase,
                    recoverable=phase != "CI_PLATFORM_OR_AUTHORITY_BLOCKED",
                    human_required=phase == "CI_PLATFORM_OR_AUTHORITY_BLOCKED",
                )
            )

    attempts.sort(key=lambda row: int(row.get("sequence") or 0))
    latest_by_stage: dict[str, Mapping[str, Any]] = {}
    for row in attempts:
        latest_by_stage[str(row.get("stage_id"))] = row
    for stage in planned:
        latest = latest_by_stage.get(stage["id"])
        if latest:
            stage["status"] = str(latest.get("status") or "PENDING")
            stage["detail"] = str(latest.get("detail") or "")
            stage["evidence_ref"] = str(latest.get("evidence_ref") or "")
    return planned, attempts


def projection_inputs(task: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return execution-progress compatible plan and attempt rows.

    Prefer the explicit durable ledger. For older/local-first TaskRuns that predate
    it, reconstruct the same read-only view from their durable checkpoints so status
    queries remain lossless without rewriting history.
    """

    ledger = read_execution_ledger(task)
    if not ledger["stages"] and not ledger["attempts"]:
        fallback = _local_first_projection_inputs(task)
        if fallback is not None:
            return fallback

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
