from __future__ import annotations

# Composition validation belongs to the control plane, not the execution-state package.

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from workflow_registry import load_workflow_registry

FULL_DEVELOPMENT_SCHEMA = "full-development-workflow@1"
FULL_DEVELOPMENT_PATH = Path("skill-system/registry/full-development-workflow.json")
STEP_TYPES = frozenset({"skill", "workflow"})


class FullDevelopmentWorkflowError(ValueError):
    """Raised when the composed full-development plan is not exact and closed."""


@dataclass(frozen=True)
class FullDevelopmentStep:
    step_id: str
    step_type: str
    use: str
    next_step: str

    def as_dict(self) -> dict[str, str]:
        return {"type": self.step_type, "use": self.use, "next": self.next_step}


@dataclass(frozen=True)
class FullDevelopmentWorkflow:
    workflow_id: str
    request_class: str
    mode: str
    start: str
    steps: Mapping[str, FullDevelopmentStep]
    completion_authority: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": FULL_DEVELOPMENT_SCHEMA,
            "workflow_id": self.workflow_id,
            "request_class": self.request_class,
            "mode": self.mode,
            "write_governed": True,
            "completion_authority": self.completion_authority,
            "start": self.start,
            "steps": {step_id: step.as_dict() for step_id, step in self.steps.items()},
        }


def _identifier(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or any(char.isspace() for char in text):
        raise FullDevelopmentWorkflowError(f"{field} must be a non-empty stable identifier")
    return text


def _active_skill_names(workspace: Path) -> set[str]:
    path = workspace / "skill-system/registry/active-skills.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise FullDevelopmentWorkflowError(f"active Skill registry is unavailable: {path}") from exc
    rows = payload.get("skills") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(rows, list):
        raise FullDevelopmentWorkflowError("active Skill registry is invalid")
    return {
        _identifier(row.get("name"), field="Skill name")
        for row in rows
        if isinstance(row, dict) and row.get("status") == "active"
    }


def _validate_chain(*, start: str, steps: Mapping[str, FullDevelopmentStep]) -> None:
    visited: set[str] = set()
    current = start
    while current != "END":
        if current in visited:
            raise FullDevelopmentWorkflowError(f"full-development workflow contains a cycle at {current!r}")
        step = steps.get(current)
        if step is None:
            raise FullDevelopmentWorkflowError(f"full-development workflow routes to unknown step {current!r}")
        visited.add(current)
        current = step.next_step
    unreachable = sorted(set(steps) - visited)
    if unreachable:
        raise FullDevelopmentWorkflowError(
            f"full-development workflow contains unreachable steps: {unreachable}"
        )


def load_full_development_workflow(workspace: Path) -> FullDevelopmentWorkflow:
    workspace = workspace.resolve()
    path = workspace / FULL_DEVELOPMENT_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FullDevelopmentWorkflowError(f"full-development workflow is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FullDevelopmentWorkflowError(f"full-development workflow JSON is invalid: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != FULL_DEVELOPMENT_SCHEMA:
        raise FullDevelopmentWorkflowError(
            f"full-development workflow schema must be {FULL_DEVELOPMENT_SCHEMA!r}"
        )
    if payload.get("write_governed") is not True:
        raise FullDevelopmentWorkflowError("full-development workflow must remain write_governed")
    if payload.get("completion_authority") != "TaskRun":
        raise FullDevelopmentWorkflowError("full-development completion authority must remain TaskRun")

    workflow_id = _identifier(payload.get("workflow_id"), field="workflow_id")
    request_class = _identifier(payload.get("request_class"), field="request_class")
    mode = _identifier(payload.get("mode"), field="mode")
    start = _identifier(payload.get("start"), field="start")
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, dict) or not raw_steps:
        raise FullDevelopmentWorkflowError("full-development workflow requires non-empty steps")

    active_skills = _active_skill_names(workspace)
    workflows = load_workflow_registry(workspace)
    steps: dict[str, FullDevelopmentStep] = {}
    for raw_step_id, raw_step in raw_steps.items():
        step_id = _identifier(raw_step_id, field="step_id")
        if not isinstance(raw_step, dict) or set(raw_step) != {"type", "use", "next"}:
            raise FullDevelopmentWorkflowError(
                f"full-development step {step_id!r} must contain exactly type/use/next"
            )
        step_type = _identifier(raw_step.get("type"), field=f"step {step_id!r} type")
        use = _identifier(raw_step.get("use"), field=f"step {step_id!r} use")
        next_step = _identifier(raw_step.get("next"), field=f"step {step_id!r} next")
        if step_type not in STEP_TYPES:
            raise FullDevelopmentWorkflowError(
                f"full-development step {step_id!r} has unsupported type {step_type!r}"
            )
        if step_type == "skill" and use not in active_skills:
            raise FullDevelopmentWorkflowError(
                f"full-development step {step_id!r} references inactive Skill {use!r}"
            )
        if step_type == "workflow" and use not in workflows:
            raise FullDevelopmentWorkflowError(
                f"full-development step {step_id!r} references unknown Workflow {use!r}"
            )
        if step_type == "workflow" and use == workflow_id:
            raise FullDevelopmentWorkflowError(
                f"full-development step {step_id!r} cannot recursively invoke {workflow_id!r}"
            )
        steps[step_id] = FullDevelopmentStep(
            step_id=step_id,
            step_type=step_type,
            use=use,
            next_step=next_step,
        )
    _validate_chain(start=start, steps=steps)
    if workflow_id not in workflows:
        raise FullDevelopmentWorkflowError(
            f"full-development workflow {workflow_id!r} is not registered for activation"
        )
    registered = workflows[workflow_id]
    direct_skills = tuple(step.use for step in steps.values() if step.step_type == "skill")
    child_workflows = tuple(step.use for step in steps.values() if step.step_type == "workflow")
    child_capabilities: list[str] = []
    seen_capabilities: set[str] = set()
    for child_id in child_workflows:
        for capability_id in workflows[child_id].required_capabilities:
            if capability_id not in seen_capabilities:
                child_capabilities.append(capability_id)
                seen_capabilities.add(capability_id)
    if registered.request_class != request_class or registered.mode != mode:
        raise FullDevelopmentWorkflowError(
            "full-development manifest identity does not match its activation registry row"
        )
    if not registered.write_governed or registered.graph is not None:
        raise FullDevelopmentWorkflowError(
            "harness-full-dev activation row must be write-governed and leave topology to the composition manifest"
        )
    if registered.skills != direct_skills:
        raise FullDevelopmentWorkflowError(
            "harness-full-dev Skill activation projection does not match the composition manifest"
        )
    if registered.required_capabilities != tuple(child_capabilities) or registered.optional_capabilities:
        raise FullDevelopmentWorkflowError(
            "harness-full-dev capability activation projection does not equal its child Workflow requirements"
        )
    return FullDevelopmentWorkflow(
        workflow_id=workflow_id,
        request_class=request_class,
        mode=mode,
        start=start,
        steps=steps,
        completion_authority="TaskRun",
    )


__all__ = [
    "FULL_DEVELOPMENT_PATH",
    "FULL_DEVELOPMENT_SCHEMA",
    "FullDevelopmentStep",
    "FullDevelopmentWorkflow",
    "FullDevelopmentWorkflowError",
    "load_full_development_workflow",
]
