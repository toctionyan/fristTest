from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from composition_bootstrap import CompositionBootstrap
from full_development_workflow import FullDevelopmentStep, load_full_development_workflow
from langgraph_workflow_runtime import (
    RUNTIME_STATUS_BLOCKED,
    RUNTIME_STATUS_END,
    RUNTIME_STATUS_HUMAN_GATE,
    RUNTIME_STATUS_WAITING_EXTERNAL,
    WorkflowRuntimeState,
    build_langgraph_workflow,
    initial_workflow_state,
    is_durable_checkpointer,
    resume_workflow_state,
)
from runtime import HarnessRuntimeEngine, HarnessRuntimeState, HarnessRuntimeStatus
from task_run import TERMINAL_STATUSES, TaskRunStore
from workflow_activation import activate_workflow
from workflow_taskrun_bridge import (
    checkpoint_workflow_resume,
    checkpoint_workflow_start,
    checkpoint_workflow_state,
)

CHILD_EXECUTION_SCHEMA = "full-development-child-execution@1"


class FullDevelopmentChildRuntimeError(RuntimeError):
    """Raised when a child Workflow cannot be delegated through the canonical runtime."""


@dataclass(frozen=True)
class FullDevelopmentChildExecution:
    parent_workflow_id: str
    parent_step_id: str
    child_workflow_id: str
    thread_id: str
    child_state: Mapping[str, Any]
    parent_state: HarnessRuntimeState
    taskrun_status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": CHILD_EXECUTION_SCHEMA,
            "parent_workflow_id": self.parent_workflow_id,
            "parent_step_id": self.parent_step_id,
            "child_workflow_id": self.child_workflow_id,
            "thread_id": self.thread_id,
            "child_state": dict(self.child_state),
            "parent_state": self.parent_state.model_dump(mode="json"),
            "taskrun_status": self.taskrun_status,
            "policy": {
                "delegates_to_existing_langgraph_runtime": True,
                "dispatcher_is_injected": True,
                "durable_checkpointer_is_required": True,
                "child_end_completes_parent": False,
                "child_end_completes_taskrun": False,
                "completion_authority": "TaskRun",
                "authority_effect": False,
            },
        }


class FullDevelopmentChildRuntime:
    """Delegate one manifest child Workflow to the existing execution stack.

    This class does not interpret or reimplement child topology. It activates the
    registered child Workflow, compiles it with ``build_langgraph_workflow``, uses
    the injected dispatcher/checkpointer, and projects the result through the
    existing TaskRun bridge. Parent ordering remains the full-development
    manifest's responsibility.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        composition_id: str,
        dispatcher: Any,
        checkpointer: Any,
        taskrun_store: TaskRunStore,
        workspace_fingerprint: str | None,
    ) -> None:
        if checkpointer is None or not is_durable_checkpointer(checkpointer):
            raise FullDevelopmentChildRuntimeError(
                "full-development child runtime requires a durable checkpointer"
            )
        self.workspace = workspace.resolve()
        self.plan = load_full_development_workflow(self.workspace)
        self.assembly = CompositionBootstrap(self.workspace).assemble(composition_id)
        if not self.assembly.ready:
            raise FullDevelopmentChildRuntimeError("full-development composition is not ready")
        if self.assembly.composition.workflow_id != self.plan.workflow_id:
            raise FullDevelopmentChildRuntimeError(
                "composition does not activate the full-development parent Workflow"
            )
        self.dispatcher = dispatcher
        self.checkpointer = checkpointer
        self.store = taskrun_store
        self.workspace_fingerprint = workspace_fingerprint
        self.engine = HarnessRuntimeEngine()

    def _assert_parent_state(self, state: HarnessRuntimeState, *, resume: bool) -> FullDevelopmentStep:
        if state.task_id != str(self.store.payload.get("task_id") or ""):
            raise FullDevelopmentChildRuntimeError("parent runtime task_id does not match TaskRun")
        if state.workflow_id != self.plan.workflow_id:
            raise FullDevelopmentChildRuntimeError("parent runtime workflow_id mismatch")
        expected_statuses = (
            {HarnessRuntimeStatus.WAITING_EXTERNAL, HarnessRuntimeStatus.BLOCKED}
            if resume
            else {HarnessRuntimeStatus.RUNNING}
        )
        if state.status not in expected_statuses:
            raise FullDevelopmentChildRuntimeError(
                "parent runtime is not in an allowed state for this operation"
            )
        current_step = str(state.current_step or "").strip()
        step = self.plan.steps.get(current_step)
        if step is None:
            raise FullDevelopmentChildRuntimeError(
                f"parent runtime current_step is not declared: {current_step!r}"
            )
        if step.step_type != "workflow":
            raise FullDevelopmentChildRuntimeError(
                f"parent step {current_step!r} is not a child Workflow step"
            )
        if str(self.store.payload.get("status") or "") in TERMINAL_STATUSES:
            raise FullDevelopmentChildRuntimeError("terminal TaskRun cannot execute a child Workflow")
        return step

    def _thread_id(self, step: FullDevelopmentStep) -> str:
        return ":".join(
            (
                "harness-child",
                str(self.store.payload["task_id"]),
                self.plan.workflow_id,
                step.step_id,
            )
        )

    def _graph(self, step: FullDevelopmentStep):
        composition = self.assembly.composition
        activation = activate_workflow(
            self.workspace,
            workflow_id=step.use,
            available_provider_ids=composition.available_provider_ids,
            provider_preferences=composition.provider_preferences,
        )
        if not activation.ready:
            raise FullDevelopmentChildRuntimeError(
                f"child Workflow activation is blocked: {step.use}"
            )
        return build_langgraph_workflow(
            workflow=activation.workflow,
            activation=activation,
            dispatcher=self.dispatcher,
            checkpointer=self.checkpointer,
        )

    def _config(self, step: FullDevelopmentStep) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": self._thread_id(step)},
            "recursion_limit": 80,
        }

    def _project(
        self,
        *,
        parent_state: HarnessRuntimeState,
        step: FullDevelopmentStep,
        child_state: Mapping[str, Any],
    ) -> FullDevelopmentChildExecution:
        checkpoint_workflow_state(
            self.store,
            state=child_state,
            workspace_fingerprint=self.workspace_fingerprint,
            parent_workflow_id=self.plan.workflow_id,
            parent_step_id=step.step_id,
            parent_next_action=step.next_step,
        )
        child_status = str(child_state.get("runtime_status") or "")
        raw_refs = child_state.get("evidence_refs")
        refs = tuple(
            str(ref).strip()
            for ref in (raw_refs if isinstance(raw_refs, (list, tuple)) else ())
            if str(ref).strip()
        )
        if child_status == RUNTIME_STATUS_END:
            updated_parent = self.engine.advance(
                parent_state,
                completed_step=step.step_id,
                next_step=step.next_step,
                evidence_refs=refs,
            )
            if updated_parent.status == HarnessRuntimeStatus.FLOW_ENDED:
                checkpoint_workflow_state(
                    self.store,
                    state={
                        "workflow_id": self.plan.workflow_id,
                        "runtime_status": RUNTIME_STATUS_END,
                        "current_stage": step.step_id,
                        "next_action": "EVALUATE_COMPLETION_POLICY",
                        "evidence_refs": list(updated_parent.evidence_refs),
                    },
                    workspace_fingerprint=self.workspace_fingerprint,
                )
        elif child_status == RUNTIME_STATUS_WAITING_EXTERNAL:
            if not refs:
                raise FullDevelopmentChildRuntimeError(
                    "waiting child Workflow returned no durable evidence"
                )
            updated_parent = self.engine.wait_external(parent_state, evidence_ref=refs[-1])
        elif child_status in {RUNTIME_STATUS_BLOCKED, RUNTIME_STATUS_HUMAN_GATE}:
            if not refs:
                raise FullDevelopmentChildRuntimeError(
                    "blocked child Workflow returned no durable evidence"
                )
            updated_parent = self.engine.block_with_evidence(parent_state, evidence_refs=refs)
        else:
            raise FullDevelopmentChildRuntimeError(
                f"child Workflow returned unsupported terminal state: {child_status!r}"
            )
        return FullDevelopmentChildExecution(
            parent_workflow_id=self.plan.workflow_id,
            parent_step_id=step.step_id,
            child_workflow_id=step.use,
            thread_id=self._thread_id(step),
            child_state=dict(child_state),
            parent_state=updated_parent,
            taskrun_status=str(self.store.payload.get("status") or ""),
        )

    def invoke(
        self,
        *,
        parent_state: HarnessRuntimeState,
        target_ref: Mapping[str, Any],
    ) -> FullDevelopmentChildExecution:
        step = self._assert_parent_state(parent_state, resume=False)
        if not target_ref:
            raise FullDevelopmentChildRuntimeError("child Workflow invocation requires target_ref")
        checkpoint_workflow_start(
            self.store,
            workflow_id=step.use,
            workspace_fingerprint=self.workspace_fingerprint,
        )
        child_state = self._graph(step).invoke(
            initial_workflow_state(
                workflow_id=step.use,
                task_id=parent_state.task_id,
                target_ref=target_ref,
            ),
            config=self._config(step),
        )
        return self._project(
            parent_state=parent_state,
            step=step,
            child_state=child_state,
        )

    def resume(
        self,
        *,
        parent_state: HarnessRuntimeState,
        child_state: Mapping[str, Any],
        external_event: Mapping[str, Any] | None = None,
        human_decision: Mapping[str, Any] | None = None,
        evidence_refs: tuple[str, ...],
        correlation_ref: str | None = None,
    ) -> FullDevelopmentChildExecution:
        step = self._assert_parent_state(parent_state, resume=True)
        if str(child_state.get("workflow_id") or "") != step.use:
            raise FullDevelopmentChildRuntimeError("resume child workflow_id mismatch")
        if str(child_state.get("task_id") or "") != parent_state.task_id:
            raise FullDevelopmentChildRuntimeError("resume child task_id mismatch")
        runtime_status = str(child_state.get("runtime_status") or "")
        if runtime_status == RUNTIME_STATUS_WAITING_EXTERNAL:
            resume_kind = "EXTERNAL_EVENT"
            if parent_state.status != HarnessRuntimeStatus.WAITING_EXTERNAL:
                raise FullDevelopmentChildRuntimeError(
                    "external child resume requires WAITING_EXTERNAL parent state"
                )
        elif runtime_status == RUNTIME_STATUS_HUMAN_GATE:
            resume_kind = "HUMAN_DECISION"
            if parent_state.status != HarnessRuntimeStatus.BLOCKED:
                raise FullDevelopmentChildRuntimeError(
                    "human child resume requires BLOCKED parent state"
                )
        else:
            raise FullDevelopmentChildRuntimeError(
                "child Workflow can resume only from WAITING_EXTERNAL or HUMAN_GATE"
            )
        checkpoint_workflow_resume(
            self.store,
            workflow_id=step.use,
            resume_kind=resume_kind,
            workspace_fingerprint=self.workspace_fingerprint,
            evidence_refs=evidence_refs,
            correlation_ref=correlation_ref,
        )
        if resume_kind == "EXTERNAL_EVENT":
            resumed_parent = self.engine.resume_external(
                parent_state,
                evidence_ref=evidence_refs[-1],
            )
        else:
            resumed_parent = self.engine.resume_blocked(
                parent_state,
                evidence_ref=evidence_refs[-1],
            )
        resumed_child: WorkflowRuntimeState = resume_workflow_state(
            child_state,
            external_event=external_event,
            human_decision=human_decision,
        )
        result = self._graph(step).invoke(resumed_child, config=self._config(step))
        return self._project(
            parent_state=resumed_parent,
            step=step,
            child_state=result,
        )


__all__ = [
    "CHILD_EXECUTION_SCHEMA",
    "FullDevelopmentChildExecution",
    "FullDevelopmentChildRuntime",
    "FullDevelopmentChildRuntimeError",
]
