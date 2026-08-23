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

_ENTRY_NODE = "__workflow_entry__"
_TERMINAL_NODE = {
    "END": "__workflow_end__",
    "WAITING_EXTERNAL": "__waiting_external__",
    "HUMAN_GATE": "__human_gate__",
    "BLOCKED_UNRECOVERABLE": "__blocked_unrecoverable__",
}


class WorkflowRuntimeError(RuntimeError):
    """Raised when a declarative Workflow cannot be executed safely."""


def is_durable_checkpointer(checkpointer: Any) -> bool:
    """Recognize durable LangGraph savers, including explicitly fenced wrappers."""

    current = checkpointer
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        module = type(current).__module__.lower()
        name = type(current).__name__.lower()
        identity = f"{module}.{name}"
        if "checkpoint.memory" in identity or "inmemory" in name:
            return False
        if "checkpoint.sqlite" in identity or "checkpoint.postgres" in identity:
            return True
        if getattr(current, "durable_checkpointer", False) is True:
            return True
        current = getattr(current, "_inner", None)
    return False


class WorkflowRuntimeState(TypedDict, total=False):
    task_id: str
    target_ref: dict[str, Any]
    workflow_id: str
    current_stage: str
    last_outcome: str
    runtime_status: str
    next_action: str
    resume_stage: str
    external_event: dict[str, Any]
    human_decision: dict[str, Any]
    step_attempts: dict[str, int]
    step_results: dict[str, list[dict[str, Any]]]
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
        "resume_stage": "",
        "step_attempts": {},
        "step_results": {},
        "evidence_refs": [],
    }
    if problem_ledger_ref:
        state["problem_ledger_ref"] = str(problem_ledger_ref)
    return state


def resume_workflow_state(
    state: Mapping[str, Any],
    *,
    external_event: Mapping[str, Any] | None = None,
    human_decision: Mapping[str, Any] | None = None,
) -> WorkflowRuntimeState:
    """Prepare one durable workflow state for exactly one event-driven resume.

    The resumed invocation re-enters the step that produced the wait/gate. That
    step must interpret the supplied external event or human decision and return
    one of its already-declared outcomes. No new topology is created at resume.
    """

    runtime_status = str(state.get("runtime_status") or "").strip()
    current_stage = str(state.get("current_stage") or "").strip()
    if runtime_status not in {RUNTIME_STATUS_WAITING_EXTERNAL, RUNTIME_STATUS_HUMAN_GATE}:
        raise WorkflowRuntimeError(
            f"workflow can resume only from WAITING_EXTERNAL or HUMAN_GATE, got {runtime_status!r}"
        )
    if not current_stage:
        raise WorkflowRuntimeError("workflow resume requires current_stage")
    if runtime_status == RUNTIME_STATUS_WAITING_EXTERNAL and not external_event:
        raise WorkflowRuntimeError("WAITING_EXTERNAL resume requires external_event")
    if runtime_status == RUNTIME_STATUS_HUMAN_GATE and not human_decision:
        raise WorkflowRuntimeError("HUMAN_GATE resume requires human_decision")

    resumed: WorkflowRuntimeState = dict(state)  # type: ignore[assignment]
    resumed["runtime_status"] = RUNTIME_STATUS_RUNNING
    resumed["resume_stage"] = current_stage
    resumed["next_action"] = current_stage
    resumed.pop("runtime_error", None)
    if external_event is not None:
        resumed["external_event"] = dict(external_event)
    if human_decision is not None:
        resumed["human_decision"] = dict(human_decision)
    return resumed


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
        "resume_stage": "",
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
        except Exception as exc:  # dispatcher failures are runtime blockers, never success
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
        if target == "WAITING_EXTERNAL" and not result.external_wait:
            return _blocked_update(
                state,
                step=step,
                attempts=attempts,
                reason=f"workflow step {step.step_id!r} routes to WAITING_EXTERNAL without a wait handle",
            )
        if target == "HUMAN_GATE" and not result.human_gate:
            return _blocked_update(
                state,
                step=step,
                attempts=attempts,
                reason=f"workflow step {step.step_id!r} routes to HUMAN_GATE without a gate contract",
            )

        step_results = {
            step_id: [dict(item) for item in history]
            for step_id, history in dict(state.get("step_results") or {}).items()
        }
        step_results.setdefault(step.step_id, []).append(
            {
                "attempt": attempt,
                "outcome": outcome,
                "payload": dict(result.payload or {}),
                "evidence_refs": list(refs),
            }
        )
        update: WorkflowRuntimeState = {
            "current_stage": step.step_id,
            "last_outcome": outcome,
            "runtime_status": RUNTIME_STATUS_RUNNING,
            "next_action": target,
            "resume_stage": "",
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


def _entry_node(state: WorkflowRuntimeState) -> WorkflowRuntimeState:
    return {"runtime_status": RUNTIME_STATUS_RUNNING}


def _make_entry_route(workflow: WorkflowSpec):
    assert workflow.graph is not None

    def route(state: WorkflowRuntimeState) -> str:
        resume_stage = str(state.get("resume_stage") or "").strip()
        if resume_stage:
            return resume_stage if resume_stage in workflow.graph.steps else "__BLOCKED__"
        return workflow.graph.start

    return route


def _workflow_end_node(state: WorkflowRuntimeState) -> WorkflowRuntimeState:
    return {
        "runtime_status": RUNTIME_STATUS_END,
        "next_action": "EVALUATE_COMPLETION_POLICY",
        "resume_stage": "",
    }


def _waiting_external_node(state: WorkflowRuntimeState) -> WorkflowRuntimeState:
    if not isinstance(state.get("external_wait"), dict) or not state.get("external_wait"):
        return {
            "runtime_status": RUNTIME_STATUS_BLOCKED,
            "next_action": "INSPECT_BLOCKER",
            "resume_stage": "",
            "runtime_error": "WAITING_EXTERNAL requires a durable external_wait handle",
        }
    current_stage = str(state.get("current_stage") or "").strip()
    return {
        "runtime_status": RUNTIME_STATUS_WAITING_EXTERNAL,
        "next_action": "RESUME_ON_EXTERNAL_EVENT",
        "resume_stage": current_stage,
    }


def _human_gate_node(state: WorkflowRuntimeState) -> WorkflowRuntimeState:
    if not isinstance(state.get("human_gate"), dict) or not state.get("human_gate"):
        return {
            "runtime_status": RUNTIME_STATUS_BLOCKED,
            "next_action": "INSPECT_BLOCKER",
            "resume_stage": "",
            "runtime_error": "HUMAN_GATE requires a durable human_gate contract",
        }
    current_stage = str(state.get("current_stage") or "").strip()
    return {
        "runtime_status": RUNTIME_STATUS_HUMAN_GATE,
        "next_action": "AWAIT_HUMAN_DECISION",
        "resume_stage": current_stage,
    }


def _blocked_node(state: WorkflowRuntimeState) -> WorkflowRuntimeState:
    return {
        "runtime_status": RUNTIME_STATUS_BLOCKED,
        "next_action": "INSPECT_BLOCKER",
        "resume_stage": "",
    }


def build_langgraph_workflow(
    *,
    workflow: WorkflowSpec,
    activation: WorkflowActivation,
    dispatcher: WorkflowStepDispatcher,
    checkpointer: Any | None = None,
):
    """Compile a validated Workflow spec into a LangGraph runtime.

    LangGraph owns only sequence/branch/loop/yield/resume routing. The dispatcher
    owns step execution, CapabilityResolver owns provider binding, and TaskRun
    remains lifecycle/completion authority outside this graph.
    """

    if activation.workflow.workflow_id != workflow.workflow_id:
        raise WorkflowRuntimeError("activation/workflow identity mismatch")
    if not activation.ready:
        raise WorkflowRuntimeError("workflow activation is not ready")
    if workflow.graph is None:
        raise WorkflowRuntimeError(f"workflow {workflow.workflow_id!r} has no declarative graph")

    bindings = _binding_index(activation)
    builder = StateGraph(WorkflowRuntimeState)
    builder.add_node(_ENTRY_NODE, _entry_node)
    for step_id, step in workflow.graph.steps.items():
        builder.add_node(
            step_id,
            _make_step_node(step=step, dispatcher=dispatcher, bindings=bindings),
        )

    builder.add_node(_TERMINAL_NODE["END"], _workflow_end_node)
    builder.add_node(_TERMINAL_NODE["WAITING_EXTERNAL"], _waiting_external_node)
    builder.add_node(_TERMINAL_NODE["HUMAN_GATE"], _human_gate_node)
    builder.add_node(_TERMINAL_NODE["BLOCKED_UNRECOVERABLE"], _blocked_node)

    builder.add_edge(START, _ENTRY_NODE)
    entry_paths = {step_id: step_id for step_id in workflow.graph.steps}
    entry_paths["__BLOCKED__"] = _TERMINAL_NODE["BLOCKED_UNRECOVERABLE"]
    builder.add_conditional_edges(_ENTRY_NODE, _make_entry_route(workflow), entry_paths)

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
    "is_durable_checkpointer",
    "resume_workflow_state",
]
