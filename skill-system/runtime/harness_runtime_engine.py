from __future__ import annotations

from .harness_runtime_state import HarnessRuntimeState, HarnessRuntimeStatus


class HarnessRuntimeEngine:
    """Small canonical runtime boundary.

    The engine coordinates workflow execution context only.
    Individual Workflow, Skill, Provider and TaskRun authorities remain separated.
    """

    def start(self, *, task_id: str, workflow_id: str) -> HarnessRuntimeState:
        return HarnessRuntimeState(
            task_id=task_id,
            workflow_id=workflow_id,
            status=HarnessRuntimeStatus.RUNNING,
        )

    def move(self, state: HarnessRuntimeState, *, step: str) -> HarnessRuntimeState:
        return state.model_copy(update={"current_step": step})

    def wait_external(self, state: HarnessRuntimeState, *, evidence_ref: str) -> HarnessRuntimeState:
        return state.model_copy(
            update={
                "status": HarnessRuntimeStatus.WAITING_EXTERNAL,
                "evidence_refs": _append_unique(state.evidence_refs, evidence_ref),
            }
        )

    def block(self, state: HarnessRuntimeState, *, receipt: str) -> HarnessRuntimeState:
        return state.model_copy(
            update={
                "status": HarnessRuntimeStatus.BLOCKED,
                "receipts": _append_unique(state.receipts, receipt),
            }
        )

    def end_flow(self, state: HarnessRuntimeState, *, evidence_ref: str) -> HarnessRuntimeState:
        """End orchestration only; TaskRun still evaluates whole-task completion."""

        return state.model_copy(
            update={
                "status": HarnessRuntimeStatus.FLOW_ENDED,
                "evidence_refs": _append_unique(state.evidence_refs, evidence_ref),
            }
        )


def _append_unique(existing: tuple[str, ...], value: str) -> tuple[str, ...]:
    ref = str(value or "").strip()
    if not ref or ref in existing:
        return existing
    return (*existing, ref)


__all__ = ["HarnessRuntimeEngine"]
