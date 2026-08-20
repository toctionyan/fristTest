from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping, TypedDict

from langgraph.graph import END, StateGraph

from workflow_spec import WORKFLOW_END

Handler = Callable[[dict[str, Any], dict[str, Any]], Mapping[str, Any]]

FLOW_COMPLETE = "FLOW_COMPLETE"
FLOW_BLOCKED = "BLOCKED"
_INTERNAL_COMPLETE = "__workflow_complete__"
_INTERNAL_ERROR = "__workflow_error__"


class WorkflowRuntimeError(RuntimeError):
    """Raised when a compiled Workflow cannot be executed safely."""


class WorkflowState(TypedDict, total=False):
    workflow_id: str
    task_id: str
    target: dict[str, Any]
    artifacts: dict[str, Any]
    step_outputs: dict[str, Any]
    current_step: str
    last_outcome: str
    visits: dict[str, int]
    history: list[dict[str, Any]]
    status: str
    error: str
    authority_effect: bool


def handler_key(step: Mapping[str, Any]) -> str:
    step_type = str(step.get("type") or "").strip()
    use = str(step.get("use") or "").strip()
    return f"{step_type}:{use}" if use else step_type


def _blocked_update(
    state: Mapping[str, Any],
    *,
    step_id: str,
    reason: str,
    visits: Mapping[str, int],
) -> dict[str, Any]:
    history = list(state.get("history") or [])
    history.append(
        {
            "step": step_id,
            "outcome": "BLOCKED",
            "reason": reason,
        }
    )
    return {
        "current_step": step_id,
        "last_outcome": "BLOCKED",
        "visits": dict(visits),
        "history": history,
        "status": FLOW_BLOCKED,
        "error": reason,
        "authority_effect": False,
    }


def compile_workflow(
    spec: Mapping[str, Any],
    *,
    handlers: Mapping[str, Handler],
):
    """Compile one already-validated Workflow into a LangGraph runtime.

    LangGraph owns orchestration only. It never becomes lifecycle/completion
    authority: the returned state always carries ``authority_effect=False`` and
    successful graph exhaustion is ``FLOW_COMPLETE``, not TaskRun COMPLETED.
    """

    workflow_id = str(spec.get("id") or "").strip()
    start = str(spec.get("start") or "").strip()
    steps = spec.get("steps")
    if not workflow_id or not start or not isinstance(steps, Mapping) or start not in steps:
        raise WorkflowRuntimeError("workflow spec must be validated before compilation")
    policy = spec.get("policy") if isinstance(spec.get("policy"), Mapping) else {}
    if (
        policy.get("taskrun_is_lifecycle_authority") is not True
        or policy.get("workflow_runtime_authority_effect") is not False
    ):
        raise WorkflowRuntimeError("workflow authority policy is missing or unsafe")

    graph = StateGraph(WorkflowState)

    def make_node(step_id: str):
        step = dict(steps[step_id])

        def node(state: WorkflowState) -> dict[str, Any]:
            visits = dict(state.get("visits") or {})
            next_visit = int(visits.get(step_id) or 0) + 1
            visits[step_id] = next_visit
            max_visits = int(step.get("max_visits") or 1)
            if next_visit > max_visits:
                return _blocked_update(
                    state,
                    step_id=step_id,
                    reason=f"workflow step visit budget exhausted: {step_id} > {max_visits}",
                    visits=visits,
                )

            key = handler_key(step)
            handler = handlers.get(key)
            if handler is None:
                return _blocked_update(
                    state,
                    step_id=step_id,
                    reason=f"workflow handler is missing: {key}",
                    visits=visits,
                )

            snapshot = deepcopy(dict(state))
            result = handler(snapshot, dict(step))
            if not isinstance(result, Mapping):
                return _blocked_update(
                    state,
                    step_id=step_id,
                    reason=f"workflow handler returned non-object output: {key}",
                    visits=visits,
                )
            outcome = str(result.get("outcome") or "").strip().upper()
            if not outcome:
                return _blocked_update(
                    state,
                    step_id=step_id,
                    reason=f"workflow handler returned no outcome: {key}",
                    visits=visits,
                )

            artifacts = dict(state.get("artifacts") or {})
            raw_artifacts = result.get("artifacts")
            if isinstance(raw_artifacts, Mapping):
                artifacts.update(dict(raw_artifacts))
            step_outputs = dict(state.get("step_outputs") or {})
            step_outputs[step_id] = {
                key_name: deepcopy(value)
                for key_name, value in result.items()
                if key_name != "artifacts"
            }
            history = list(state.get("history") or [])
            history.append(
                {
                    "step": step_id,
                    "handler": key,
                    "visit": next_visit,
                    "outcome": outcome,
                }
            )
            return {
                "workflow_id": workflow_id,
                "current_step": step_id,
                "last_outcome": outcome,
                "visits": visits,
                "artifacts": artifacts,
                "step_outputs": step_outputs,
                "history": history,
                "status": "RUNNING",
                "error": "",
                "authority_effect": False,
            }

        return node

    def make_router(step_id: str):
        transitions = dict(steps[step_id]["transitions"])

        def router(state: WorkflowState) -> str:
            if state.get("status") == FLOW_BLOCKED:
                return _INTERNAL_ERROR
            outcome = str(state.get("last_outcome") or "").strip().upper()
            destination = transitions.get(outcome)
            if destination is None:
                return _INTERNAL_ERROR
            if destination == WORKFLOW_END:
                return _INTERNAL_COMPLETE
            return str(destination)

        return router

    for step_id in steps:
        graph.add_node(step_id, make_node(str(step_id)))

    def complete_node(state: WorkflowState) -> dict[str, Any]:
        return {
            "status": FLOW_COMPLETE,
            "authority_effect": False,
            "error": "",
        }

    def error_node(state: WorkflowState) -> dict[str, Any]:
        if state.get("status") == FLOW_BLOCKED:
            return {"authority_effect": False}
        current = str(state.get("current_step") or "")
        outcome = str(state.get("last_outcome") or "")
        reason = f"workflow transition is undefined: step={current} outcome={outcome}"
        return {
            "status": FLOW_BLOCKED,
            "error": reason,
            "authority_effect": False,
        }

    graph.add_node(_INTERNAL_COMPLETE, complete_node)
    graph.add_node(_INTERNAL_ERROR, error_node)
    graph.add_edge(_INTERNAL_COMPLETE, END)
    graph.add_edge(_INTERNAL_ERROR, END)
    graph.set_entry_point(start)

    for step_id in steps:
        destinations = {
            str(destination)
            for destination in steps[step_id]["transitions"].values()
            if destination != WORKFLOW_END
        }
        path_map = {destination: destination for destination in destinations}
        path_map[_INTERNAL_COMPLETE] = _INTERNAL_COMPLETE
        path_map[_INTERNAL_ERROR] = _INTERNAL_ERROR
        graph.add_conditional_edges(str(step_id), make_router(str(step_id)), path_map)

    return graph.compile()


def run_workflow(
    spec: Mapping[str, Any],
    *,
    handlers: Mapping[str, Handler],
    initial_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = compile_workflow(spec, handlers=handlers)
    state = dict(initial_state or {})
    if state.get("authority_effect") not in (None, False):
        raise WorkflowRuntimeError("workflow initial state cannot carry authority_effect=true")
    state.setdefault("workflow_id", str(spec.get("id") or ""))
    state.setdefault("artifacts", {})
    state.setdefault("step_outputs", {})
    state.setdefault("visits", {})
    state.setdefault("history", [])
    state.setdefault("status", "RUNNING")
    state["authority_effect"] = False

    steps = spec.get("steps") if isinstance(spec.get("steps"), Mapping) else {}
    visit_budget = sum(int(step.get("max_visits") or 1) for step in steps.values())
    recursion_limit = max(25, visit_budget * 3 + len(steps) + 10)
    result = runtime.invoke(state, config={"recursion_limit": recursion_limit})
    payload = dict(result)
    payload["authority_effect"] = False
    return payload
