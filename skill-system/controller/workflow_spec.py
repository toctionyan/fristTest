from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from plugin_registry import PluginRegistryError, resolve_skill_plugin, resolve_workflow_plugin  # type: ignore  # noqa: E402

WORKFLOW_SCHEMA_VERSION = 1
WORKFLOW_TYPES = {"skill", "executor", "gate", "human_gate"}
WORKFLOW_END = "END"
EXECUTOR_PROFILE_PREFIX = "profile:"


class WorkflowSpecError(ValueError):
    """Raised when a declarative Workflow is unsafe or incomplete."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise WorkflowSpecError(f"{label} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowSpecError(f"{label} must be a positive integer") from exc
    if parsed < 1:
        raise WorkflowSpecError(f"{label} must be a positive integer")
    return parsed


def _validate_executor(workspace: Path, *, step_id: str, use: str) -> None:
    if not use.startswith(EXECUTOR_PROFILE_PREFIX):
        raise WorkflowSpecError(
            f"workflow executor step {step_id} must reference {EXECUTOR_PROFILE_PREFIX}<profile>"
        )
    profile_id = use[len(EXECUTOR_PROFILE_PREFIX):].strip()
    if not profile_id:
        raise WorkflowSpecError(f"workflow executor step {step_id} has empty profile id")
    path = workspace / "skill-system" / "profiles" / f"{profile_id}.json"
    if not path.is_file():
        raise WorkflowSpecError(
            f"workflow executor step {step_id} references unknown profile: {profile_id}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowSpecError(
            f"workflow executor profile is invalid JSON: {profile_id}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("id") != profile_id:
        raise WorkflowSpecError(
            f"workflow executor profile identity is invalid: {profile_id}"
        )


def validate_workflow_spec(workspace: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    workspace = workspace.resolve()
    if payload.get("schema_version") != WORKFLOW_SCHEMA_VERSION:
        raise WorkflowSpecError("workflow schema_version must be 1")
    workflow_id = _text(payload.get("id"))
    if not workflow_id:
        raise WorkflowSpecError("workflow requires id")
    start = _text(payload.get("start"))
    raw_steps = payload.get("steps")
    if not start or not isinstance(raw_steps, Mapping) or not raw_steps:
        raise WorkflowSpecError("workflow requires start and non-empty steps")

    normalized_steps: dict[str, dict[str, Any]] = {}
    for raw_id, raw_step in raw_steps.items():
        step_id = _text(raw_id)
        if not step_id or not isinstance(raw_step, Mapping):
            raise WorkflowSpecError("workflow contains an invalid step")
        if step_id in normalized_steps:
            raise WorkflowSpecError(f"duplicate workflow step: {step_id}")
        step_type = _text(raw_step.get("type"))
        if step_type not in WORKFLOW_TYPES:
            raise WorkflowSpecError(f"unsupported workflow step type for {step_id}: {step_type}")
        use = _text(raw_step.get("use"))
        if step_type != "human_gate" and not use:
            raise WorkflowSpecError(f"workflow step {step_id} requires use")
        if step_type == "skill":
            try:
                resolve_skill_plugin(workspace, use)
            except PluginRegistryError as exc:
                raise WorkflowSpecError(f"workflow step {step_id} references invalid Skill: {use}") from exc
        elif step_type == "executor":
            _validate_executor(workspace, step_id=step_id, use=use)
        raw_transitions = raw_step.get("transitions")
        if not isinstance(raw_transitions, Mapping) or not raw_transitions:
            raise WorkflowSpecError(f"workflow step {step_id} requires transitions")
        transitions: dict[str, str] = {}
        for raw_outcome, raw_destination in raw_transitions.items():
            outcome = _text(raw_outcome).upper()
            destination = _text(raw_destination)
            if not outcome or not destination:
                raise WorkflowSpecError(f"workflow step {step_id} has an invalid transition")
            if outcome in transitions:
                raise WorkflowSpecError(
                    f"workflow step {step_id} contains duplicate transition outcome: {outcome}"
                )
            transitions[outcome] = destination
        normalized_steps[step_id] = {
            "type": step_type,
            "use": use,
            "max_visits": _positive_int(raw_step.get("max_visits", 1), label=f"{step_id}.max_visits"),
            "transitions": transitions,
        }

    if start not in normalized_steps:
        raise WorkflowSpecError(f"workflow start step does not exist: {start}")

    for step_id, step in normalized_steps.items():
        for destination in step["transitions"].values():
            if destination != WORKFLOW_END and destination not in normalized_steps:
                raise WorkflowSpecError(
                    f"workflow step {step_id} transitions to unknown step: {destination}"
                )

    reachable: set[str] = set()
    queue: deque[str] = deque([start])
    end_reachable = False
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        for destination in normalized_steps[current]["transitions"].values():
            if destination == WORKFLOW_END:
                end_reachable = True
            elif destination not in reachable:
                queue.append(destination)
    unreachable = sorted(set(normalized_steps) - reachable)
    if unreachable:
        raise WorkflowSpecError(f"workflow contains unreachable steps: {unreachable}")
    if not end_reachable:
        raise WorkflowSpecError("workflow has no reachable END transition")

    policy = payload.get("policy") if isinstance(payload.get("policy"), Mapping) else {}
    if policy.get("taskrun_is_lifecycle_authority") is not True:
        raise WorkflowSpecError("workflow must preserve TaskRun as lifecycle authority")
    if policy.get("workflow_runtime_authority_effect") is not False:
        raise WorkflowSpecError("workflow runtime must declare authority_effect=false")
    if policy.get("max_visits_are_not_success") is not True:
        raise WorkflowSpecError("workflow must declare max_visits_are_not_success=true")

    return {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "id": workflow_id,
        "description": _text(payload.get("description")),
        "start": start,
        "steps": normalized_steps,
        "policy": {
            "taskrun_is_lifecycle_authority": True,
            "workflow_runtime_authority_effect": False,
            "max_visits_are_not_success": True,
        },
    }


def load_workflow_spec(workspace: Path, name: str) -> dict[str, Any]:
    plugin = resolve_workflow_plugin(workspace, name)
    path = workspace.resolve() / plugin.path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowSpecError(f"workflow is invalid JSON: {plugin.path}") from exc
    if not isinstance(payload, dict):
        raise WorkflowSpecError(f"workflow must be a JSON object: {plugin.path}")
    normalized = validate_workflow_spec(workspace, payload)
    if normalized["id"] != plugin.name:
        raise WorkflowSpecError(
            f"workflow registry/name mismatch: registry={plugin.name} document={normalized['id']}"
        )
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one registered declarative Workflow")
    parser.add_argument("--workflow", required=True)
    args = parser.parse_args()
    try:
        payload = load_workflow_spec(ROOT, args.workflow)
    except (PluginRegistryError, WorkflowSpecError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "PASS", "workflow": payload}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
