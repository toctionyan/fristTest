from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from capability_registry import CapabilityBinding
from workflow_activation import WorkflowActivation
from workflow_graph_contract import TERMINAL_TARGETS, WorkflowStepSpec
from workflow_registry import WorkflowSpec

RUNTIME_STATUS_RUNNING = "RUNNING"
RUNTIME_STATUS_END = "WORKFLOW_END"
RUNTIME_STATUS_WAITING_EXTERNAL = "WAITING_EXTERNAL"
RUNTIME_STATUS_HUMAN_GATE = "HUMAN_GATE"
RUNTIME_STATUS_BLOCKED = "BLOCKED_UNRECOVERABLE"

_TERMINAL_NODE = {
    "END": "__workflow_end__",
    "WAITING_EXTERNAL": "__waiting_external__",
    "HUMAN_GATE": "__human_gate__",
    "BLOCKED_UNRECOVERABLE": "__blocked_unrecoverable__",
}


class WorkflowRuntimeError(RuntimeError):
    """Raised when a declarative Workflow cannot be executed safely."""


class WorkflowRuntimeState(TypedDict, total=False):
    task_id: str
    target_ref: dict[str, Any]
    workflow_id: str
    current_stage: str
    last_outcome: str
    runtime_status: str
    next_action: str
    step_attempts: dict[str, int]
    step_results: dict[str, dict[str, Any]]
    evidence_refs: list[str]
    problem_ledger_ref: str
    external_wait: dict[str, Any]
    human_gate: dict[str, Any]
    runtime_error: str


@dataclass(frozen=True)
class StepDispatchResult:
    outcome: str
    evidence_refs: tuple[str, ...]
    payload: Mapping[str, Any] | None = None
    problem_ledger_ref: str | None = None
    external_wait: Mapping[str, Any] | None = None
    human_gate: Mapping[str, Any] | None = None


class WorkflowStepDispatcher(Protocol):
    def run(
        self,
        *,
        step: WorkflowStepSpec,
        state: WorkflowRuntimeState,
        capability_binding: CapabilityBinding | None,
    ) -> StepDispatchResult:
        ...


def initial_workflow_state(
    *,
    workflow_id: str,
    task_id: str,
    target_ref: Mapping[str, Any],
    problem_ledger_ref: str | None = None,
) -> WorkflowRuntimeState:
    state: WorkflowRuntimeState = {
        "workflow_id": str(workflow_id),
        "task_id": str(task_id),
        "target_ref": dict(target_ref),
        "runtime_status": RUNTIME_STATUS_RUNNING,
        "next_action": "START",
        "step_attempts": {},
        "step_results": {},
        "evidence_refs": [],
    }
    if problem_ledger_ref:
        state["problem_ledger_ref"] = str(problem_ledger_ref)
    return state


def _binding_index(activation: WorkflowActivation) -> dict[str, CapabilityBinding]:
    preflight = activation.capability_preflight
    bindings = (*preflight.required_bindings, *preflight.optional_bindings)
    return {binding.capability_id: binding for binding in bindings}


def _append_unique(existing: list[str], additions: tuple[str, ...]) -> list[str]:
    result = list(existing)
    seen = set(result)
    for value in additions:
        ref = str(value).strip()
        if ref and ref not in seen:
            result.append(ref)
            seen.add(ref)
    return result


def _blocked_update(
    state: WorkflowRuntimeState,
    *,
    step: WorkflowStepSpec,
    reason: str,
    attempts: dict[str, int] | None = None,
) -> WorkflowRuntimeState:
    return {
        "current_stage": step.step_id,
        "runtime_status": RUNTIME_STATUS_BLOCKED,
        "next_action": "INSPECT_BLOCKER",
        "runtime_error": reason,
        "step_attempts": attempts or dict(state.get("step_attempts") or {}),
    }


def _make_step_node(
    *,
    step: WorkflowStepSpec,
    dispatcher: WorkflowStepDispatcher,
    bindings: Mapping[str, CapabilityBinding],
):
    def node(state: WorkflowRuntimeState) -> WorkflowRuntimeState:
        attempts = dict(state.get("step_attempts") or {})
        attempt = int(attempts.get(step.step_id) or 0) + 1
        attempts[step.step_id] = attempt
        if attempt > step.max_attempts:
            return _blocked_update(
                state,
                step=step,
                attempts=attempts,
                reason=(
                    f"workflow step {step.step_id!r} exceeded max_attempts={step.max_attempts}; "
                    "runtime cannot continue looping"
                ),
            )

        binding: CapabilityBinding | None = None
        if step.step_type in {"executor", "gate", "external_wait"}:
            binding = bindings.get(str(step.use))
            if binding is None:
                return _blocked_update(
                    state,
                    step=step,
                    attempts=attempts,
                    reason=f"required capability {step.use!r} is not bound at activation",
                )

        try:
            result = dispatcher.run(
                step=step,
                state=state,
                capability_binding=binding,
            )
        except Exception as exc:  # dispatcher failures are evidence-bearing runtime blockers, never success
            return _blocked_update(
                state,
                step=step,
                attempts=attempts,
                reason=f"dispatcher failed for {step.step_id!r}: {type(exc).__name__}: {exc}",
            )

        outcome = str(result.outcome or "").strip()
        if outcome not in step.routes:
            return _blocked_update(
                state,
                step=step,
                attempts=attempts,
                reason=f"workflow step {step.step_id!r} returned undeclared outcome {outcome!r}",
            )
        refs = tuple(str(ref).strip() for ref in result.evidence_refs if str(ref).strip())
        if not refs:
            return _blocked_update(
                state,
                step=step,
                attempts=attempts,
                reason=f"workflow step {step.step_id!r} returned no durable evidence_refs",
            )
        target = step.routes[outcome]
        if step.step_type == "external_wait" and not result.external_wait:
            return _blocked_update(
                state,
                step=step,
                attempts=attempts,
                reason=f"external_wait step {step.step_id!r} returned no external_wait handle",
            )
        if target == "WAITING_EXTERNAL" and not result.external_wait:
            return _blocked_update(
                state,
                step=step,
                attempts=attempts,
                reason=f"workflow step {step.step_id!r} routes to WAITING_EXTERNAL without a wait handle",
            )
        if step.step_type == "human_gate" and not result.human_gate:
            return _blocked_update(
                state,
                step=step,
                attempts=attempts,
                reason=f"human_gate step {step.step_id!r} returned no human_gate contract",
            )
        if target == "HUMAN_GATE" and not result.human_gate:
            return _blocked_update(
                state,
                step=step,
                attempts=attempts,
                reason=f"workflow step {step.step_id!r} routes to HUMAN_GATE without a gate contract",
            )

        step_results = dict(state.get("step_results") or {})
        step_results[step.step_id] = {
            "attempt": attempt,
            "outcome": outcome,
            "payload": dict(result.payload or {}),
            "evidence_refs": list(refs),
        }
        update: WorkflowRuntimeState = {
            "current_stage": step.step_id,
            "last_outcome": outcome,
            "runtime_status": RUNTIME_STATUS_RUNNING,
            "next_action": target,
            "step_attempts": attempts,
            "step_results": step_results,
            "evidence_refs": _append_unique(list(state.get("evidence_refs") or []), refs),
        }
        if result.problem_ledger_ref:
            update["problem_ledger_ref"] = str(result.problem_ledger_ref)
        if result.external_wait:
            update["external_wait"] = dict(result.external_wait)
        if result.human_gate:
            update["human_gate"] = dict(result.human_gate)
        return update

    return node


def _make_route(step: WorkflowStepSpec):
    def route(state: WorkflowRuntimeState) -> str:
        if state.get("runtime_status") == RUNTIME_STATUS_BLOCKED:
            return "__BLOCKED__"
        outcome = str(state.get("last_outcome") or "")
        target = step.routes.get(outcome)
        if target is None:
            return "__BLOCKED__"
        return target

    return route


def _workflow_end_node(state: WorkflowRuntimeState) -> WorkflowRuntimeState:
    return {
        "runtime_status": RUNTIME_STATUS_END,
        "next_action": "EVALUATE_COMPLETION_POLICY",
    }


def _waiting_external_node(state: WorkflowRuntimeState) -> WorkflowRuntimeState:
    if not isinstance(state.get("external_wait"), dict) or not state.get("external_wait"):
        return {
            "runtime_status": RUNTIME_STATUS_BLOCKED,
            "next_action": "INSPECT_BLOCKER",
            "runtime_error": "WAITING_EXTERNAL requires a durable external_wait handle",
        }
    return {
        "runtime_status": RUNTIME_STATUS_WAITING_EXTERNAL,
        "next_action": "RESUME_ON_EXTERNAL_EVENT",
    }


def _human_gate_node(state: WorkflowRuntimeState) -> WorkflowRuntimeState:
    if not isinstance(state.get("human_gate"), dict) or not state.get("human_gate"):
        return {
            "runtime_status": RUNTIME_STATUS_BLOCKED,
            "next_action": "INSPECT_BLOCKER",
            "runtime_error": "HUMAN_GATE requires a durable human_gate contract",
        }
    return {
        "runtime_status": RUNTIME_STATUS_HUMAN_GATE,
        "next_action": "AWAIT_HUMAN_DECISION",
    }


def _blocked_node(state: WorkflowRuntimeState) -> WorkflowRuntimeState:
    return {
        "runtime_status": RUNTIME_STATUS_BLOCKED,
        "next_action": "INSPECT_BLOCKER",
    }


def build_langgraph_workflow(
    *,
    workflow: WorkflowSpec,
    activation: WorkflowActivation,
    dispatcher: WorkflowStepDispatcher,
    checkpointer: Any | None = None,
):
    """Compile a validated Workflow spec into a LangGraph runtime.

    LangGraph owns only sequence/branch/loop execution. The dispatcher owns step
    execution, CapabilityResolver owns provider binding, and TaskRun remains the
    lifecycle/completion authority outside this graph.
    """

    if activation.workflow.workflow_id != workflow.workflow_id:
        raise WorkflowRuntimeError("activation/workflow identity mismatch")
    if not activation.ready:
        raise WorkflowRuntimeError("workflow activation is not ready")
    if workflow.graph is None:
        raise WorkflowRuntimeError(f"workflow {workflow.workflow_id!r} has no declarative graph")

    bindings = _binding_index(activation)
    builder = StateGraph(WorkflowRuntimeState)
    for step_id, step in workflow.graph.steps.items():
        builder.add_node(
            step_id,
            _make_step_node(step=step, dispatcher=dispatcher, bindings=bindings),
        )

    builder.add_node(_TERMINAL_NODE["END"], _workflow_end_node)
    builder.add_node(_TERMINAL_NODE["WAITING_EXTERNAL"], _waiting_external_node)
    builder.add_node(_TERMINAL_NODE["HUMAN_GATE"], _human_gate_node)
    builder.add_node(_TERMINAL_NODE["BLOCKED_UNRECOVERABLE"], _blocked_node)

    builder.add_edge(START, workflow.graph.start)
    for step_id, step in workflow.graph.steps.items():
        path_map: dict[str, str] = {"__BLOCKED__": _TERMINAL_NODE["BLOCKED_UNRECOVERABLE"]}
        for target in set(step.routes.values()):
            path_map[target] = _TERMINAL_NODE[target] if target in TERMINAL_TARGETS else target
        builder.add_conditional_edges(step_id, _make_route(step), path_map)

    for terminal_node in _TERMINAL_NODE.values():
        builder.add_edge(terminal_node, END)
    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "RUNTIME_STATUS_BLOCKED",
    "RUNTIME_STATUS_END",
    "RUNTIME_STATUS_HUMAN_GATE",
    "RUNTIME_STATUS_RUNNING",
    "RUNTIME_STATUS_WAITING_EXTERNAL",
    "StepDispatchResult",
    "WorkflowRuntimeError",
    "WorkflowRuntimeState",
    "WorkflowStepDispatcher",
    "build_langgraph_workflow",
    "initial_workflow_state",
]
