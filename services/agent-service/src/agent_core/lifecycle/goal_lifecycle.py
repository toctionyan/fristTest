from __future__ import annotations

"""Durable, semantic goal lifecycle independent from business transaction state."""

from copy import deepcopy
from typing import Any, Iterable

from agent_core.context.state_projection import goal_records_context_projection
from agent_core.lifecycle.semantic_contract import assert_semantic_contract_integrity
from agent_core.lifecycle.semantic_state_changes import record_revision

ACTIVE_LIFECYCLES = {"OPEN", "ACTIVE", "BLOCKED", "PAUSED"}
TERMINAL_LIFECYCLES = {"COMPLETED", "CANCELLED", "SUPERSEDED"}
ALL_LIFECYCLES = ACTIVE_LIFECYCLES | TERMINAL_LIFECYCLES

_ALLOWED_TRANSITIONS = {
    "OPEN": ALL_LIFECYCLES,
    "ACTIVE": {"ACTIVE", "BLOCKED", "PAUSED", "COMPLETED", "CANCELLED", "SUPERSEDED"},
    "BLOCKED": {"BLOCKED", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED", "SUPERSEDED"},
    "PAUSED": {"PAUSED", "ACTIVE", "CANCELLED", "SUPERSEDED"},
    "COMPLETED": {"COMPLETED", "SUPERSEDED"},
    "CANCELLED": {"CANCELLED", "SUPERSEDED"},
    "SUPERSEDED": {"SUPERSEDED"},
}


def active_goal_records(state_or_records: dict[str, Any] | Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = state_or_records.get("goal_records") or [] if isinstance(state_or_records, dict) else state_or_records
    return [
        deepcopy(row)
        for row in list(rows or [])
        if isinstance(row, dict) and str(row.get("lifecycle") or "OPEN").upper() in ACTIVE_LIFECYCLES
    ]


def _transition(
    record: dict[str, Any],
    lifecycle: str,
    *,
    turn: int,
    operation: str,
    evidence_span: str | None = None,
) -> None:
    before = str(record.get("lifecycle") or "OPEN").upper()
    after = str(lifecycle or "").upper()
    if after not in ALL_LIFECYCLES:
        raise ValueError(f"invalid_goal_lifecycle:{after}")
    if after not in _ALLOWED_TRANSITIONS.get(before, set()):
        raise ValueError(f"invalid_goal_transition:{record.get('goal_id')}:{before}->{after}")
    record["lifecycle"] = after
    record["revision"] = record_revision(record) + 1
    record["updated_turn"] = int(turn)
    record["last_lifecycle_operation"] = operation
    if evidence_span:
        record["last_change_evidence_span"] = str(evidence_span)


def _assert_validated_change(record: dict[str, Any], change: dict[str, Any]) -> None:
    goal_id = str(record.get("goal_id") or "")
    actual = record_revision(record)
    validated = int(change.get("validated_against_revision") or -1)
    expected = int(change.get("expected_revision") or -1)
    if validated != actual or expected != actual:
        raise ValueError(f"goal_revision_conflict:{goal_id}:expected={expected}:actual={actual}")
    if int(change.get("next_revision") or -1) != actual + 1:
        raise ValueError(f"goal_next_revision_invalid:{goal_id}")
    if not str(change.get("evidence_span") or "").strip():
        raise ValueError(f"goal_change_evidence_required:{goal_id}")


def apply_semantic_contract_to_goal_records(
    records: Iterable[dict[str, Any]],
    contract: dict[str, Any],
    *,
    turn: int,
) -> list[dict[str, Any]]:
    """Apply concrete goal operations; never infer them from user keywords."""
    assert_semantic_contract_integrity(contract)
    output = [deepcopy(row) for row in list(records or []) if isinstance(row, dict)]
    for row in output:
        row.setdefault("revision", record_revision(row))
    by_id = {str(row.get("goal_id") or ""): row for row in output if str(row.get("goal_id") or "")}

    for change in list(contract.get("goal_changes") or []):
        if not isinstance(change, dict):
            continue
        operation = str(change.get("operation") or "").upper()
        goal_id = str(change.get("goal_id") or "")
        record = by_id.get(goal_id)
        if record is None:
            raise ValueError(f"unknown_goal_for_change:{goal_id}")
        _assert_validated_change(record, change)
        evidence_span = str(change.get("evidence_span") or "").strip()
        if operation == "SET_GOAL_LIFECYCLE":
            declared_from = str(change.get("from") or "").upper()
            actual_from = str(record.get("lifecycle") or "OPEN").upper()
            if declared_from != actual_from:
                raise ValueError(
                    f"goal_lifecycle_from_mismatch:{goal_id}:expected={declared_from}:actual={actual_from}"
                )
            _transition(
                record,
                str(change.get("to") or ""),
                turn=turn,
                operation=operation,
                evidence_span=evidence_span,
            )
        elif operation == "SUPERSEDE_GOAL":
            _transition(
                record,
                "SUPERSEDED",
                turn=turn,
                operation=operation,
                evidence_span=evidence_span,
            )
            record["superseded_by"] = str(change.get("superseded_by") or "") or None
        elif operation == "PATCH_GOAL":
            patch = change.get("patch") if isinstance(change.get("patch"), dict) else {}
            for key in ("target_candidate", "input_candidates", "condition", "depends_on"):
                if key in patch:
                    record[key] = deepcopy(patch[key])
            record["revision"] = record_revision(record) + 1
            record["updated_turn"] = int(turn)
            record["last_lifecycle_operation"] = operation
            record["last_change_evidence_span"] = evidence_span
        else:
            raise ValueError(f"unsupported_goal_change_operation:{operation}")

    contract_id = str(contract.get("semantic_contract_id") or "")
    for goal in list(contract.get("goals") or []):
        if not isinstance(goal, dict):
            continue
        goal_id = str(goal.get("goal_id") or "")
        if not goal_id:
            continue
        continuation_of = str(goal.get("continuation_of") or "")
        existing = by_id.get(goal_id)
        if existing is None:
            existing = {
                "goal_id": goal_id,
                "created_turn": int(turn),
                "lifecycle": "ACTIVE",
                "revision": 1,
            }
            output.append(existing)
            by_id[goal_id] = existing
        existing.setdefault("revision", record_revision(existing))
        existing.update({
            "description": str(goal.get("description") or ""),
            "requested_effect": deepcopy(goal.get("requested_effect")) if isinstance(goal.get("requested_effect"), dict) else None,
            "target_candidate": deepcopy(goal.get("target_candidate")) if goal.get("target_candidate") is not None else existing.get("target_candidate"),
            "input_candidates": deepcopy(goal.get("input_candidates")) if goal.get("input_candidates") is not None else existing.get("input_candidates"),
            "condition": deepcopy(goal.get("condition")) if goal.get("condition") is not None else existing.get("condition"),
            "depends_on": list(goal.get("depends_on") or []),
            "required": bool(goal.get("required", True)),
            "source_semantic_contract_id": contract_id,
            "updated_turn": int(turn),
            "continuation_of": continuation_of or None,
        })
        if str(existing.get("lifecycle") or "") in {"OPEN", "BLOCKED", "PAUSED"}:
            _transition(existing, "ACTIVE", turn=turn, operation="SEMANTIC_CONTRACT_ACTIVATED")
    return output


def update_goal_records_from_execution_plan(
    records: Iterable[dict[str, Any]],
    plan: dict[str, Any] | None,
    *,
    turn: int,
) -> list[dict[str, Any]]:
    output = [deepcopy(row) for row in list(records or []) if isinstance(row, dict)]
    by_id = {str(row.get("goal_id") or ""): row for row in output if str(row.get("goal_id") or "")}
    if not isinstance(plan, dict):
        return output
    for goal in list(plan.get("goals") or []):
        if not isinstance(goal, dict):
            continue
        record = by_id.get(str(goal.get("goal_id") or ""))
        if record is None or str(record.get("lifecycle") or "") in TERMINAL_LIFECYCLES:
            continue
        completion_tools = [
            str(name) for name in list(goal.get("completion_tool_names") or []) if str(name)
        ]
        if completion_tools:
            record["completion_tool_names"] = list(dict.fromkeys(completion_tools))
        coverage = str(goal.get("coverage_status") or "")
        if coverage == "COVERED":
            _transition(record, "COMPLETED", turn=turn, operation="EXECUTION_PLAN_COVERED")
        elif coverage == "BLOCKED":
            _transition(record, "BLOCKED", turn=turn, operation="EXECUTION_PLAN_BLOCKED")
        elif coverage == "FAILED":
            record["last_execution_failure_turn"] = int(turn)
            record["updated_turn"] = int(turn)
        else:
            _transition(record, "ACTIVE", turn=turn, operation="EXECUTION_PLAN_PENDING")
    return output

