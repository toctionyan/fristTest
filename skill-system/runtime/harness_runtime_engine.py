from __future__ import annotations

from .harness_runtime_state import HarnessRuntimeState, HarnessRuntimeStatus


class HarnessRuntimeEngine:
    """Small canonical runtime boundary.

    The engine coordinates workflow execution context only.
    Individual Workflow, Skill, Provider and TaskRun authorities remain separated.
    """

    def start(
        self,
        *,
        task_id: str,
        workflow_id: str,
        start_step: str | None = None,
    ) -> HarnessRuntimeState:
        return HarnessRuntimeState(
            task_id=task_id,
            workflow_id=workflow_id,
            current_step=str(start_step or "").strip() or None,
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

    def resume_external(self, state: HarnessRuntimeState, *, evidence_ref: str) -> HarnessRuntimeState:
        if state.status != HarnessRuntimeStatus.WAITING_EXTERNAL:
            raise ValueError("Harness runtime can resume only from WAITING_EXTERNAL")
        return state.model_copy(
            update={
                "status": HarnessRuntimeStatus.RUNNING,
                "evidence_refs": _append_unique(state.evidence_refs, evidence_ref),
            }
        )

    def resume_blocked(self, state: HarnessRuntimeState, *, evidence_ref: str) -> HarnessRuntimeState:
        if state.status != HarnessRuntimeStatus.BLOCKED:
            raise ValueError("Harness runtime can resume a gate only from BLOCKED")
        return state.model_copy(
            update={
                "status": HarnessRuntimeStatus.RUNNING,
                "evidence_refs": _append_unique(state.evidence_refs, evidence_ref),
            }
        )

    def advance(
        self,
        state: HarnessRuntimeState,
        *,
        completed_step: str,
        next_step: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> HarnessRuntimeState:
        completed = str(completed_step or "").strip()
        target = str(next_step or "").strip()
        if state.status != HarnessRuntimeStatus.RUNNING:
            raise ValueError("Harness runtime can advance only while RUNNING")
        if not completed or state.current_step != completed:
            raise ValueError("Harness runtime advance must match the exact current_step")
        if not target:
            raise ValueError("Harness runtime advance requires next_step")
        refs = _append_many(state.evidence_refs, evidence_refs)
        if target == "END":
            return state.model_copy(
                update={
                    "status": HarnessRuntimeStatus.FLOW_ENDED,
                    "evidence_refs": refs,
                }
            )
        return state.model_copy(
            update={
                "current_step": target,
                "status": HarnessRuntimeStatus.RUNNING,
                "evidence_refs": refs,
            }
        )

    def block_with_evidence(
        self,
        state: HarnessRuntimeState,
        *,
        evidence_refs: tuple[str, ...],
    ) -> HarnessRuntimeState:
        refs = _append_many(state.evidence_refs, evidence_refs)
        if refs == state.evidence_refs:
            raise ValueError("Harness runtime blocker requires new durable evidence")
        return state.model_copy(
            update={
                "status": HarnessRuntimeStatus.BLOCKED,
                "evidence_refs": refs,
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


def _append_many(existing: tuple[str, ...], values: tuple[str, ...]) -> tuple[str, ...]:
    result = existing
    for value in values:
        result = _append_unique(result, value)
    return result


__all__ = ["HarnessRuntimeEngine"]
