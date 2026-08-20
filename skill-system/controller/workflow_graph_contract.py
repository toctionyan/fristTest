from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

STEP_TYPES = frozenset({"skill", "executor", "gate", "external_wait", "human_gate"})
TERMINAL_TARGETS = frozenset({"END", "WAITING_EXTERNAL", "HUMAN_GATE", "BLOCKED_UNRECOVERABLE"})
_RESERVED_STEP_PREFIX = "__workflow_"


class WorkflowGraphContractError(ValueError):
    """Raised when a declarative Workflow graph is malformed or violates boundaries."""


@dataclass(frozen=True)
class WorkflowStepSpec:
    step_id: str
    step_type: str
    use: str | None
    routes: dict[str, str]
    max_attempts: int

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.step_type,
            "routes": dict(self.routes),
            "max_attempts": self.max_attempts,
        }
        if self.use is not None:
            payload["use"] = self.use
        return payload


@dataclass(frozen=True)
class WorkflowGraphSpec:
    start: str
    steps: dict[str, WorkflowStepSpec]
    max_attempts_per_step: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "max_attempts_per_step": self.max_attempts_per_step,
            "steps": {step_id: step.as_dict() for step_id, step in self.steps.items()},
        }


def _text(value: object) -> str:
    return str(value or "").strip()


def _stable_id(value: object, *, field: str) -> str:
    text = _text(value)
    if not text or any(char.isspace() for char in text):
        raise WorkflowGraphContractError(f"{field} must be a non-empty stable identifier")
    return text


def _step_id(value: object) -> str:
    step_id = _stable_id(value, field="workflow step_id")
    if step_id in TERMINAL_TARGETS or step_id.startswith(_RESERVED_STEP_PREFIX):
        raise WorkflowGraphContractError(f"workflow step_id {step_id!r} is reserved by the runtime")
    return step_id


def _positive_int(value: object, *, field: str, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 64:
        raise WorkflowGraphContractError(f"{field} must be an integer between 1 and 64")
    return value


def _parse_routes(value: object, *, step_id: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise WorkflowGraphContractError(f"workflow step {step_id!r} requires non-empty routes")
    routes: dict[str, str] = {}
    for raw_outcome, raw_target in value.items():
        outcome = _stable_id(raw_outcome, field=f"workflow step {step_id!r} route outcome")
        target = _stable_id(raw_target, field=f"workflow step {step_id!r} route target")
        if outcome in routes:
            raise WorkflowGraphContractError(f"workflow step {step_id!r} has duplicate route outcome {outcome!r}")
        routes[outcome] = target
    return routes


def _reachable_steps(start: str, steps: Mapping[str, WorkflowStepSpec]) -> set[str]:
    reachable: set[str] = set()
    pending = [start]
    while pending:
        step_id = pending.pop()
        if step_id in reachable:
            continue
        reachable.add(step_id)
        step = steps[step_id]
        for target in step.routes.values():
            if target not in TERMINAL_TARGETS and target not in reachable:
                pending.append(target)
    return reachable


def parse_workflow_graph(
    raw: object,
    *,
    workflow_id: str,
    skills: tuple[str, ...],
    required_capabilities: tuple[str, ...],
) -> WorkflowGraphSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise WorkflowGraphContractError(f"workflow {workflow_id!r} graph must be an object")
    unexpected = sorted(set(raw) - {"start", "steps", "max_attempts_per_step"})
    if unexpected:
        raise WorkflowGraphContractError(
            f"workflow {workflow_id!r} graph contains unsupported keys: {unexpected}"
        )
    start = _stable_id(raw.get("start"), field=f"workflow {workflow_id!r} graph start")
    if start in TERMINAL_TARGETS or start.startswith(_RESERVED_STEP_PREFIX):
        raise WorkflowGraphContractError(f"workflow {workflow_id!r} graph start {start!r} is reserved")
    default_max_attempts = _positive_int(
        raw.get("max_attempts_per_step"),
        field=f"workflow {workflow_id!r} max_attempts_per_step",
        default=8,
    )
    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, Mapping) or not raw_steps:
        raise WorkflowGraphContractError(f"workflow {workflow_id!r} graph requires non-empty steps")

    steps: dict[str, WorkflowStepSpec] = {}
    for raw_step_id, raw_step in raw_steps.items():
        step_id = _step_id(raw_step_id)
        if not isinstance(raw_step, Mapping):
            raise WorkflowGraphContractError(f"workflow step {step_id!r} must be an object")
        unexpected = sorted(set(raw_step) - {"type", "use", "routes", "max_attempts"})
        if unexpected:
            raise WorkflowGraphContractError(
                f"workflow step {step_id!r} contains unsupported keys: {unexpected}"
            )
        step_type = _stable_id(raw_step.get("type"), field=f"workflow step {step_id!r} type")
        if step_type not in STEP_TYPES:
            raise WorkflowGraphContractError(f"workflow step {step_id!r} has unsupported type {step_type!r}")
        use = _text(raw_step.get("use")) or None
        if step_type in {"skill", "executor", "gate", "external_wait"} and use is None:
            raise WorkflowGraphContractError(f"workflow step {step_id!r} type {step_type!r} requires use")
        if step_type == "human_gate" and use is not None:
            raise WorkflowGraphContractError(f"workflow human_gate step {step_id!r} cannot bind a provider/use target")
        if step_type == "skill" and use not in skills:
            raise WorkflowGraphContractError(
                f"workflow step {step_id!r} references Skill {use!r} not declared by workflow"
            )
        if step_type in {"executor", "gate", "external_wait"} and use not in required_capabilities:
            raise WorkflowGraphContractError(
                f"workflow step {step_id!r} capability {use!r} must be declared required; "
                "runtime graph nodes cannot depend on optional providers"
            )
        routes = _parse_routes(raw_step.get("routes"), step_id=step_id)
        steps[step_id] = WorkflowStepSpec(
            step_id=step_id,
            step_type=step_type,
            use=use,
            routes=routes,
            max_attempts=_positive_int(
                raw_step.get("max_attempts"),
                field=f"workflow step {step_id!r} max_attempts",
                default=default_max_attempts,
            ),
        )

    if start not in steps:
        raise WorkflowGraphContractError(f"workflow {workflow_id!r} graph start references unknown step {start!r}")
    for step in steps.values():
        for target in step.routes.values():
            if target not in steps and target not in TERMINAL_TARGETS:
                raise WorkflowGraphContractError(
                    f"workflow step {step.step_id!r} routes to unknown target {target!r}"
                )
        if step.step_type == "external_wait" and "WAITING_EXTERNAL" not in step.routes.values():
            raise WorkflowGraphContractError(
                f"external_wait step {step.step_id!r} must have a route to WAITING_EXTERNAL"
            )
        if step.step_type == "human_gate" and "HUMAN_GATE" not in step.routes.values():
            raise WorkflowGraphContractError(
                f"human_gate step {step.step_id!r} must have a route to HUMAN_GATE"
            )

    unreachable = sorted(set(steps) - _reachable_steps(start, steps))
    if unreachable:
        raise WorkflowGraphContractError(
            f"workflow {workflow_id!r} graph contains unreachable steps: {unreachable}"
        )
    return WorkflowGraphSpec(
        start=start,
        steps=steps,
        max_attempts_per_step=default_max_attempts,
    )


__all__ = [
    "STEP_TYPES",
    "TERMINAL_TARGETS",
    "WorkflowGraphContractError",
    "WorkflowGraphSpec",
    "WorkflowStepSpec",
    "parse_workflow_graph",
]
