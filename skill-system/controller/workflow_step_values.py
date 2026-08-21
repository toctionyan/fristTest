from __future__ import annotations

from typing import Any, Mapping

from langgraph_workflow_runtime import WorkflowRuntimeState


class WorkflowStepValueError(ValueError):
    """Raised when a workflow request cannot resolve a prior-step value safely."""


def _text(value: object) -> str:
    return str(value or "").strip()


def latest_step_payload(state: WorkflowRuntimeState, step_id: str) -> dict[str, Any]:
    step_id = _text(step_id)
    if not step_id:
        raise WorkflowStepValueError("step_id must be non-empty")
    raw_results = state.get("step_results")
    results = raw_results if isinstance(raw_results, Mapping) else {}
    history = results.get(step_id)
    if not isinstance(history, list) or not history:
        raise WorkflowStepValueError(f"prior workflow step {step_id!r} has no recorded result")
    latest = history[-1]
    if not isinstance(latest, Mapping):
        raise WorkflowStepValueError(f"prior workflow step {step_id!r} latest result is malformed")
    payload = latest.get("payload")
    if not isinstance(payload, Mapping):
        raise WorkflowStepValueError(f"prior workflow step {step_id!r} latest payload is missing")
    return dict(payload)


def _read_path(payload: Mapping[str, Any], path: str, *, step_id: str) -> Any:
    parts = [_text(part) for part in _text(path).split(".")]
    if not parts or any(not part for part in parts):
        raise WorkflowStepValueError("from_steps path must be a non-empty dot path")
    current: Any = payload
    for part in parts:
        if not isinstance(current, Mapping) or part not in current:
            raise WorkflowStepValueError(
                f"prior workflow step {step_id!r} payload does not contain path {path!r}"
            )
        current = current[part]
    if current is None or (isinstance(current, str) and not current.strip()):
        raise WorkflowStepValueError(
            f"prior workflow step {step_id!r} payload path {path!r} resolved empty"
        )
    return current


def resolve_request_from_steps(
    state: WorkflowRuntimeState,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve explicit request fields from already-recorded workflow step payloads.

    The template is target data, never workflow topology. Example::

        {
          "from_steps": {
            "head_sha": {"step_id": "commit", "path": "commit_sha"}
          }
        }

    Only the latest durable result of an already-executed step may be referenced.
    A concrete field may coexist only when it is exactly equal to the resolved value.
    Missing or conflicting values fail closed.
    """

    resolved = dict(request)
    raw_bindings = resolved.pop("from_steps", None)
    if raw_bindings is None:
        return resolved
    if not isinstance(raw_bindings, Mapping) or not raw_bindings:
        raise WorkflowStepValueError("from_steps must be a non-empty mapping")

    for raw_field, raw_source in raw_bindings.items():
        field = _text(raw_field)
        if not field or field == "from_steps":
            raise WorkflowStepValueError("from_steps destination field must be a stable non-empty name")
        if not isinstance(raw_source, Mapping):
            raise WorkflowStepValueError(f"from_steps[{field!r}] must be an object")
        unexpected = sorted(set(raw_source) - {"step_id", "path"})
        if unexpected:
            raise WorkflowStepValueError(
                f"from_steps[{field!r}] contains unsupported keys: {unexpected}"
            )
        step_id = _text(raw_source.get("step_id"))
        path = _text(raw_source.get("path"))
        if not step_id or not path:
            raise WorkflowStepValueError(
                f"from_steps[{field!r}] requires non-empty step_id and path"
            )
        value = _read_path(latest_step_payload(state, step_id), path, step_id=step_id)
        if field in resolved and resolved[field] != value:
            raise WorkflowStepValueError(
                f"request field {field!r} conflicts with value resolved from step {step_id!r}"
            )
        resolved[field] = value
    return resolved


__all__ = [
    "WorkflowStepValueError",
    "latest_step_payload",
    "resolve_request_from_steps",
]
