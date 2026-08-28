"""Project a stage-acceptance preview into the existing TaskRun ledger.

This adapter is deliberately narrower than the reducer. The reducer remains a
pure decision function; this module is the only bridge that may record that
decision in an existing TaskRun. It never satisfies completion conditions,
marks a task completed, writes governance state, or dispatches work.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from stage_acceptance_reducer import (
    ACCEPTABLE_PREVIEW,
    BLOCKED,
    TRUSTED_STAGE_ACCEPTANCE_DECISION_SCHEMA,
    validate_trusted_stage_acceptance_decision,
)
from task_run import TERMINAL_STATUSES, TaskRunStore


STAGE_ACCEPTANCE_PREVIEW_PHASE = "STAGE_ACCEPTANCE_PREVIEW"
STAGE_ACCEPTANCE_BLOCKED_PHASE = "STAGE_ACCEPTANCE_BLOCKED"
_REQUIRED_BINDING_FIELDS = (
    "stage_id",
    "accepted_state_id",
    "product_source_ref",
    "protected_snapshot_digest",
    "control_plane_ref",
    "execution_repo_ref",
)
_PROJECTION_METADATA_FIELDS = (
    "stage_acceptance_decision_id",
    "stage_acceptance_input_digest",
    "stage_id",
    "accepted_state_id",
    "product_source_ref",
    "protected_snapshot_digest",
    "control_plane_ref",
    "execution_repo_ref",
    "receipt_refs",
    "completion_authority",
    "graph_can_complete_task",
    "completion_authority_changed",
    "stage_acceptance_write",
)


class StageAcceptanceTaskRunError(ValueError):
    """Raised when a reducer preview cannot be safely projected to a TaskRun."""


def _refs(values: Iterable[object]) -> list[str]:
    result = [value.strip() for value in values if isinstance(value, str) and value.strip()]
    if len(result) != len(set(result)):
        raise StageAcceptanceTaskRunError("stage acceptance evidence_refs must be unique")
    return result


def _binding_matches(store: TaskRunStore, expected: Mapping[str, str]) -> None:
    binding = store.payload.get("binding")
    if not isinstance(binding, Mapping):
        raise StageAcceptanceTaskRunError("TaskRun binding is missing")
    missing = [field for field in _REQUIRED_BINDING_FIELDS if field not in expected]
    if missing:
        raise StageAcceptanceTaskRunError(
            "stage acceptance binding is incomplete: " + ",".join(missing)
        )
    unknown = sorted(set(expected) - set(_REQUIRED_BINDING_FIELDS))
    if unknown:
        raise StageAcceptanceTaskRunError(
            "stage acceptance binding has unknown fields: " + ",".join(unknown)
        )
    mismatches = [
        field
        for field in _REQUIRED_BINDING_FIELDS
        if binding.get(field) != expected[field]
    ]
    if mismatches:
        raise StageAcceptanceTaskRunError(
            "TaskRun stage acceptance binding mismatch: " + ",".join(mismatches)
        )


def _projection_metadata(
    decision: Mapping[str, Any],
    expected_binding: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "stage_acceptance_decision_id": decision["decision_id"],
        "stage_acceptance_input_digest": decision["input_digest"],
        "stage_id": expected_binding["stage_id"],
        "accepted_state_id": expected_binding["accepted_state_id"],
        "product_source_ref": expected_binding["product_source_ref"],
        "protected_snapshot_digest": expected_binding["protected_snapshot_digest"],
        "control_plane_ref": expected_binding["control_plane_ref"],
        "execution_repo_ref": expected_binding["execution_repo_ref"],
        "receipt_refs": list(decision["receipt_refs"]),
        "completion_authority": "TaskRun",
        "graph_can_complete_task": False,
        "completion_authority_changed": False,
        "stage_acceptance_write": False,
    }


def _same_projection(
    checkpoint: Mapping[str, Any],
    *,
    status: str,
    phase: str,
    evidence_refs: list[str],
    metadata: Mapping[str, Any],
) -> bool:
    if checkpoint.get("status") != status or checkpoint.get("phase") != phase:
        return False
    if checkpoint.get("evidence_refs") != evidence_refs:
        return False
    current_metadata = checkpoint.get("metadata")
    if not isinstance(current_metadata, Mapping):
        return False
    return all(current_metadata.get(field) == metadata[field] for field in _PROJECTION_METADATA_FIELDS)


def project_stage_acceptance_to_taskrun(
    store: TaskRunStore,
    decision: Mapping[str, Any],
    *,
    expected_binding: Mapping[str, str],
    evidence_refs: Iterable[str],
    workspace_fingerprint: str | None,
) -> dict[str, Any]:
    """Record one validated reducer decision without completing the TaskRun.

    The caller supplies the TaskRun and every stage binding explicitly. No
    repository discovery, latest-receipt selection, governance write, or
    completion-condition mutation is performed here.
    """

    try:
        validated = validate_trusted_stage_acceptance_decision(decision)
    except (TypeError, ValueError) as exc:
        raise StageAcceptanceTaskRunError("stage acceptance decision is invalid") from exc
    if validated["status"] not in {ACCEPTABLE_PREVIEW, BLOCKED}:
        raise StageAcceptanceTaskRunError("unsupported stage acceptance decision status")
    if not isinstance(expected_binding, Mapping):
        raise StageAcceptanceTaskRunError("stage acceptance expected_binding must be an object")
    normalized_binding = {
        field: value.strip() if isinstance(value, str) else value
        for field, value in expected_binding.items()
    }
    if any(
        not isinstance(normalized_binding.get(field), str)
        or not normalized_binding[field]
        for field in _REQUIRED_BINDING_FIELDS
    ):
        raise StageAcceptanceTaskRunError("stage acceptance binding values are invalid")
    _binding_matches(store, normalized_binding)  # type: ignore[arg-type]

    refs = _refs(evidence_refs)
    if not refs:
        raise StageAcceptanceTaskRunError("stage acceptance projection requires evidence_refs")
    if store.payload.get("status") in TERMINAL_STATUSES:
        raise StageAcceptanceTaskRunError("terminal TaskRun cannot receive stage acceptance projection")

    metadata = _projection_metadata(validated, normalized_binding)  # type: ignore[arg-type]
    phase = (
        STAGE_ACCEPTANCE_PREVIEW_PHASE
        if validated["status"] == ACCEPTABLE_PREVIEW
        else STAGE_ACCEPTANCE_BLOCKED_PHASE
    )
    if validated["status"] == BLOCKED:
        status = "BLOCKED"
    elif store.payload.get("status") in {"CREATED", "PLANNED"}:
        # TaskRun intentionally does not permit CREATED/PLANNED -> VALIDATING.
        # The phase still records the acceptance preview; the next runtime
        # checkpoint may advance the already-running task to VALIDATING.
        status = "RUNNING"
    else:
        status = "VALIDATING"
    checkpoints = store.payload.get("checkpoints")
    if not isinstance(checkpoints, list):
        raise StageAcceptanceTaskRunError("TaskRun checkpoints are invalid")
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, Mapping):
            continue
        current_metadata = checkpoint.get("metadata")
        if not isinstance(current_metadata, Mapping):
            continue
        if current_metadata.get("stage_acceptance_decision_id") != validated["decision_id"]:
            continue
        allowed_statuses = {"BLOCKED"} if validated["status"] == BLOCKED else {"RUNNING", "VALIDATING"}
        if checkpoint.get("status") not in allowed_statuses or not _same_projection(
            checkpoint,
            status=str(checkpoint.get("status")),
            phase=phase,
            evidence_refs=refs,
            metadata=metadata,
        ):
            raise StageAcceptanceTaskRunError(
                "stage acceptance decision was previously projected with different bindings"
            )
        return deepcopy(dict(checkpoint))

    checkpoint = store.checkpoint(
        status=status,
        phase=phase,
        workspace_fingerprint=workspace_fingerprint,
        evidence_refs=refs,
        metadata=metadata,
    )
    if store.payload.get("status") == "COMPLETED":
        raise StageAcceptanceTaskRunError("stage acceptance projection marked TaskRun completed")
    return deepcopy(checkpoint)


__all__ = [
    "STAGE_ACCEPTANCE_BLOCKED_PHASE",
    "STAGE_ACCEPTANCE_PREVIEW_PHASE",
    "TRUSTED_STAGE_ACCEPTANCE_DECISION_SCHEMA",
    "StageAcceptanceTaskRunError",
    "project_stage_acceptance_to_taskrun",
    "validate_trusted_stage_acceptance_decision",
]
