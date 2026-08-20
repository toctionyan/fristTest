from __future__ import annotations

from typing import Any, Iterable, Mapping

from langgraph_workflow_runtime import (
    RUNTIME_STATUS_BLOCKED,
    RUNTIME_STATUS_END,
    RUNTIME_STATUS_HUMAN_GATE,
    RUNTIME_STATUS_RUNNING,
    RUNTIME_STATUS_WAITING_EXTERNAL,
)
from task_run import TaskRunStore


class WorkflowTaskRunBridgeError(ValueError):
    """Raised when runtime state cannot be projected into the authoritative TaskRun safely."""


def _refs(values: Iterable[object]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def checkpoint_workflow_start(
    store: TaskRunStore,
    *,
    workflow_id: str,
    workspace_fingerprint: str | None,
    evidence_refs: Iterable[str] = (),
) -> dict[str, Any]:
    """Record that a Workflow runtime started without changing completion authority."""

    return store.checkpoint(
        status="RUNNING",
        phase="WORKFLOW_RUNTIME_STARTED",
        workspace_fingerprint=workspace_fingerprint,
        evidence_refs=_refs(evidence_refs),
        metadata={
            "workflow_id": str(workflow_id),
            "authority_effect": False,
            "completion_authority": "TaskRun",
        },
    )


def checkpoint_workflow_resume(
    store: TaskRunStore,
    *,
    workflow_id: str,
    resume_kind: str,
    workspace_fingerprint: str | None,
    evidence_refs: Iterable[str] = (),
    correlation_ref: str | None = None,
) -> dict[str, Any]:
    """Move a durable TaskRun back to RUNNING for one authorized resume event.

    This bridge does not decide whether an external event or human decision is
    semantically acceptable. The caller supplies the evidence that authorized the
    resume; the TaskRun only records the lifecycle transition.
    """

    current = str(store.payload.get("status") or "")
    kind = str(resume_kind or "").strip().upper()
    allowed = {
        "EXTERNAL_EVENT": "WAITING_EXTERNAL_RESULT",
        "HUMAN_DECISION": "BLOCKED",
    }
    expected = allowed.get(kind)
    if expected is None:
        raise WorkflowTaskRunBridgeError(f"unsupported workflow resume_kind: {resume_kind!r}")
    if current != expected:
        raise WorkflowTaskRunBridgeError(
            f"workflow resume {kind} requires TaskRun status {expected}, got {current}"
        )
    refs = _refs(evidence_refs)
    if not refs:
        raise WorkflowTaskRunBridgeError("workflow resume requires durable evidence_refs")
    return store.checkpoint(
        status="RUNNING",
        phase="WORKFLOW_RUNTIME_RESUMED",
        workspace_fingerprint=workspace_fingerprint,
        evidence_refs=refs,
        metadata={
            "workflow_id": str(workflow_id),
            "resume_kind": kind,
            "correlation_ref": str(correlation_ref or "") or None,
            "authority_effect": False,
            "graph_can_complete_task": False,
            "completion_authority_changed": False,
        },
    )


def checkpoint_workflow_state(
    store: TaskRunStore,
    *,
    state: Mapping[str, Any],
    workspace_fingerprint: str | None,
) -> dict[str, Any]:
    """Persist a LangGraph runtime checkpoint into TaskRun lifecycle evidence.

    A graph reaching END is deliberately projected to VALIDATING, never COMPLETED.
    Completion still requires TaskRun required conditions and a separate completion
    policy decision.
    """

    runtime_status = str(state.get("runtime_status") or "").strip()
    workflow_id = str(state.get("workflow_id") or "").strip()
    if not workflow_id:
        raise WorkflowTaskRunBridgeError("workflow runtime state requires workflow_id")
    refs = _refs(state.get("evidence_refs") if isinstance(state.get("evidence_refs"), list) else [])
    metadata: dict[str, Any] = {
        "workflow_id": workflow_id,
        "current_stage": str(state.get("current_stage") or ""),
        "next_action": str(state.get("next_action") or ""),
        "problem_ledger_ref": str(state.get("problem_ledger_ref") or "") or None,
        "authority_effect": False,
        "graph_can_complete_task": False,
        "quality_authority_changed": False,
        "completion_authority_changed": False,
    }

    if runtime_status == RUNTIME_STATUS_RUNNING:
        status = "RUNNING"
        phase = "WORKFLOW_RUNTIME_RUNNING"
    elif runtime_status == RUNTIME_STATUS_WAITING_EXTERNAL:
        wait_handle = state.get("external_wait")
        if not isinstance(wait_handle, Mapping) or not wait_handle:
            raise WorkflowTaskRunBridgeError("WAITING_EXTERNAL runtime state requires external_wait handle")
        status = "WAITING_EXTERNAL_RESULT"
        phase = "WORKFLOW_WAITING_EXTERNAL"
        metadata["external_wait"] = dict(wait_handle)
        metadata["resume_stage"] = str(state.get("resume_stage") or "") or None
    elif runtime_status == RUNTIME_STATUS_HUMAN_GATE:
        gate = state.get("human_gate")
        if not isinstance(gate, Mapping) or not gate:
            raise WorkflowTaskRunBridgeError("HUMAN_GATE runtime state requires human_gate contract")
        status = "BLOCKED"
        phase = "WORKFLOW_HUMAN_GATE"
        metadata["human_required"] = True
        metadata["human_gate"] = dict(gate)
        metadata["resume_stage"] = str(state.get("resume_stage") or "") or None
    elif runtime_status == RUNTIME_STATUS_BLOCKED:
        status = "BLOCKED"
        phase = "WORKFLOW_BLOCKED_UNRECOVERABLE"
        metadata["runtime_error"] = str(state.get("runtime_error") or "") or None
    elif runtime_status == RUNTIME_STATUS_END:
        status = "VALIDATING"
        phase = "WORKFLOW_GRAPH_ENDED_AWAITING_COMPLETION_POLICY"
        metadata["next_action"] = "EVALUATE_COMPLETION_POLICY"
    else:
        raise WorkflowTaskRunBridgeError(f"unsupported workflow runtime_status: {runtime_status!r}")

    checkpoint = store.checkpoint(
        status=status,
        phase=phase,
        workspace_fingerprint=workspace_fingerprint,
        evidence_refs=refs,
        metadata=metadata,
    )
    if store.payload.get("status") == "COMPLETED":
        raise WorkflowTaskRunBridgeError("workflow runtime bridge must never mark TaskRun COMPLETED")
    return checkpoint


__all__ = [
    "WorkflowTaskRunBridgeError",
    "checkpoint_workflow_resume",
    "checkpoint_workflow_start",
    "checkpoint_workflow_state",
]
