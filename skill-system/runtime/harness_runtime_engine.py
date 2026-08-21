from __future__ import annotations

from dataclasses import dataclass

from .harness_runtime_state import HarnessRuntimeState, HarnessTaskStatus


@dataclass(frozen=True)
class WorkflowExecutionResult:
    state: HarnessRuntimeState
    next_action: str


class HarnessRuntimeEngine:
    """Small canonical runtime boundary.

    The engine coordinates workflow execution context only.
    Individual Workflow, Skill, Provider and TaskRun authorities remain separated.
    """

    def start(self, *, task_id: str, workflow_id: str) -> HarnessRuntimeState:
        return HarnessRuntimeState(
            task_id=task_id,
            workflow_id=workflow_id,
            status=HarnessTaskStatus.RUNNING,
        )

    def move(self, state: HarnessRuntimeState, *, step: str) -> HarnessRuntimeState:
        state.current_step = step
        return state

    def wait_external(self, state: HarnessRuntimeState, *, evidence_ref: str) -> HarnessRuntimeState:
        state.status = HarnessTaskStatus.WAITING_EXTERNAL
        state.evidence_refs.append(evidence_ref)
        return state

    def block(self, state: HarnessRuntimeState, *, receipt: str) -> HarnessRuntimeState:
        state.status = HarnessTaskStatus.BLOCKED
        state.receipts.append(receipt)
        return state


__all__ = ["HarnessRuntimeEngine", "WorkflowExecutionResult"]
