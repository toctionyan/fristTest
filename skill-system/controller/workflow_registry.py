from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORKFLOW_REGISTRY_SCHEMA = "dev-workflow-registry@1"
WORKFLOW_REGISTRY_PATH = Path("skill-system/registry/dev-workflows.json")


class WorkflowRegistryError(ValueError):
    """Raised when the explicit development workflow registry is invalid."""


@dataclass(frozen=True)
class WorkflowSpec:
    workflow_id: str
    request_class: str
    skills: tuple[str, ...]
    mode: str
    status_first: bool = False
    deterministic_response: bool = False
    write_governed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "request_class": self.request_class,
            "skills": list(self.skills),
            "mode": self.mode,
            "status_first": self.status_first,
            "deterministic_response": self.deterministic_response,
            "write_governed": self.write_governed,
        }


def _text(value: object) -> str:
    return str(value or "").strip()


def _bool(row: dict[str, Any], key: str) -> bool:
    value = row.get(key, False)
    if not isinstance(value, bool):
        raise WorkflowRegistryError(f"workflow field {key!r} must be boolean")
    return value


def _validate_no_target_binding(row: dict[str, Any], workflow_id: str) -> None:
    forbidden = {
        "target",
        "targets",
        "target_id",
        "task_id",
        "change_id",
        "repository",
        "repository_full_name",
        "branch",
        "github_repo",
    }
    present = sorted(key for key in forbidden if key in row)
    if present:
        raise WorkflowRegistryError(
            f"workflow {workflow_id!r} must stay target-independent; forbidden fields: {', '.join(present)}"
        )


def _parse_workflow(row: dict[str, Any]) -> WorkflowSpec:
    workflow_id = _text(row.get("workflow_id"))
    if not workflow_id:
        raise WorkflowRegistryError("workflow_id is required")
    request_class = _text(row.get("request_class"))
    if not request_class:
        raise WorkflowRegistryError(f"workflow {workflow_id!r} request_class is required")
    mode = _text(row.get("mode"))
    if not mode:
        raise WorkflowRegistryError(f"workflow {workflow_id!r} mode is required")

    raw_skills = row.get("skills")
    if not isinstance(raw_skills, list) or not raw_skills:
        raise WorkflowRegistryError(f"workflow {workflow_id!r} requires a non-empty skills list")
    skills = tuple(_text(value) for value in raw_skills)
    if any(not value for value in skills):
        raise WorkflowRegistryError(f"workflow {workflow_id!r} contains an empty Skill name")
    if len(set(skills)) != len(skills):
        raise WorkflowRegistryError(f"workflow {workflow_id!r} contains duplicate Skills")

    _validate_no_target_binding(row, workflow_id)
    return WorkflowSpec(
        workflow_id=workflow_id,
        request_class=request_class,
        skills=skills,
        mode=mode,
        status_first=_bool(row, "status_first"),
        deterministic_response=_bool(row, "deterministic_response"),
        write_governed=_bool(row, "write_governed"),
    )


def load_workflow_registry(workspace: Path) -> dict[str, WorkflowSpec]:
    """Load the explicit, target-independent development Workflow registry.

    The registry only describes orchestration policy: request class, required
    Skills and execution mode. It deliberately contains no TaskRun, target,
    repository, GitHub, Quality or completion authority.
    """

    path = workspace.resolve() / WORKFLOW_REGISTRY_PATH
    if not path.is_file():
        raise WorkflowRegistryError(f"workflow registry is missing: {WORKFLOW_REGISTRY_PATH.as_posix()}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowRegistryError(f"workflow registry JSON is invalid: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != WORKFLOW_REGISTRY_SCHEMA:
        raise WorkflowRegistryError(
            f"workflow registry schema must be {WORKFLOW_REGISTRY_SCHEMA!r}"
        )
    rows = payload.get("workflows")
    if not isinstance(rows, list) or not rows:
        raise WorkflowRegistryError("workflow registry requires a non-empty workflows list")

    workflows: dict[str, WorkflowSpec] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise WorkflowRegistryError("each workflow registry row must be an object")
        spec = _parse_workflow(raw)
        if spec.workflow_id in workflows:
            raise WorkflowRegistryError(f"duplicate workflow_id: {spec.workflow_id}")
        workflows[spec.workflow_id] = spec
    return workflows


def require_workflow(workspace: Path, workflow_id: str) -> WorkflowSpec:
    key = _text(workflow_id)
    workflows = load_workflow_registry(workspace)
    try:
        return workflows[key]
    except KeyError as exc:
        raise WorkflowRegistryError(f"unknown workflow_id: {workflow_id!r}") from exc


__all__ = [
    "WORKFLOW_REGISTRY_PATH",
    "WORKFLOW_REGISTRY_SCHEMA",
    "WorkflowRegistryError",
    "WorkflowSpec",
    "load_workflow_registry",
    "require_workflow",
]
