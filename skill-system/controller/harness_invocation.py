from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
SKILL_SYSTEM = ROOT / "skill-system"
for search_path in (CONTROLLER, SKILL_SYSTEM):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

from composition_bootstrap import (  # type: ignore  # noqa: E402
    CompositionBootstrap,
    CompositionBootstrapError,
)
from capability_registry import CapabilityRegistryError  # type: ignore  # noqa: E402
from full_development_workflow import (  # type: ignore  # noqa: E402
    FullDevelopmentWorkflowError,
    load_full_development_workflow,
)
from runtime import HarnessRuntimeEngine  # type: ignore  # noqa: E402
from skill_invocation import canonical_skill_path  # type: ignore  # noqa: E402
from workflow_activation import WorkflowActivationError  # type: ignore  # noqa: E402
from workflow_registry import WorkflowRegistryError  # type: ignore  # noqa: E402

INVOCATION_ROUTE_SCHEMA = "harness-invocation-route@1"
OPEN_MODE = "OPEN"
SKILL_MODE = "SKILL_BOUND"
WORKFLOW_MODE = "WORKFLOW_BOUND"
STARTER_MODE = "STARTER_WORKFLOW_BOUND"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]+$")


class HarnessInvocationError(ValueError):
    """Raised when explicit invocation cannot be resolved exactly and safely."""


def _identifier(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or not _SAFE_ID.fullmatch(text):
        raise HarnessInvocationError(f"{field} must be a stable identifier")
    return text


def infer_mode(
    *,
    skill: str | None = None,
    workflow: str | None = None,
    starter_entrypoint: str | None = None,
    starter_command: str | None = None,
) -> str:
    selectors = [bool(skill), bool(workflow), bool(starter_entrypoint), bool(starter_command)]
    if sum(selectors) > 1:
        raise HarnessInvocationError(
            "choose at most one explicit Skill, Workflow, Starter entrypoint, or Starter command"
        )
    if skill:
        return SKILL_MODE
    if workflow:
        return WORKFLOW_MODE
    if starter_entrypoint or starter_command:
        return STARTER_MODE
    return OPEN_MODE


def _active_skills(workspace: Path) -> set[str]:
    path = workspace / "skill-system/registry/active-skills.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise HarnessInvocationError(f"active Skill registry is unavailable: {path}") from exc
    rows = payload.get("skills") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(rows, list):
        raise HarnessInvocationError("active Skill registry is invalid")
    return {
        str(row.get("name") or "").strip()
        for row in rows
        if isinstance(row, dict) and row.get("status") == "active"
    }


def build_open_route(*, payload: str) -> dict[str, Any]:
    return {
        "schema": INVOCATION_ROUTE_SCHEMA,
        "status": "PASS",
        "mode": OPEN_MODE,
        "user_payload": str(payload or ""),
        "selected_skill": None,
        "selected_workflow": None,
        "policy": {
            "automatic_skill_selection_allowed": False,
            "automatic_workflow_selection_allowed": False,
            "authority_effect": False,
        },
        "next_action": "OPEN_ANALYSIS",
    }


def build_skill_route(
    workspace: Path,
    *,
    skill: str,
    task_id: str | None,
    payload: str,
) -> dict[str, Any]:
    skill_name = _identifier(skill, field="skill")
    task_key = _identifier(task_id, field="task_id") if task_id else None
    if skill_name not in _active_skills(workspace):
        raise HarnessInvocationError(f"active Skill not found: {skill_name}")
    relative = canonical_skill_path(skill_name)
    path = workspace / relative
    if not path.is_file():
        raise HarnessInvocationError(f"canonical Skill is missing: {relative.as_posix()}")
    return {
        "schema": INVOCATION_ROUTE_SCHEMA,
        "status": "PASS",
        "mode": SKILL_MODE,
        "user_payload": str(payload or ""),
        "selected_skill": skill_name,
        "selected_workflow": None,
        "task_id": task_key,
        "skill_context": path.read_text(encoding="utf-8"),
        "receipt": None,
        "policy": {
            "exact_selection_required": True,
            "fuzzy_fallback_allowed": False,
            "selection_is_execution": False,
            "host_execution_required_before_receipt": True,
            "authority_effect": False,
        },
        "next_action": "EXECUTE_EXACT_SKILL_VIA_HOST",
    }


def build_workflow_route(
    workspace: Path,
    *,
    workflow: str,
    composition_id: str | None,
    task_id: str | None,
    payload: str,
) -> dict[str, Any]:
    workflow_id = _identifier(workflow, field="workflow")
    if not composition_id:
        raise HarnessInvocationError("Workflow invocation requires explicit --composition-id")
    composition_key = _identifier(composition_id, field="composition_id")
    activation = CompositionBootstrap(workspace).build_runtime_input(composition_key)
    if activation["workflow_id"] != workflow_id:
        raise HarnessInvocationError(
            f"composition/workflow mismatch: composition={activation['workflow_id']!r} "
            f"requested={workflow_id!r}"
        )
    if activation["status"] != "PASS":
        raise HarnessInvocationError(f"Workflow activation is blocked: {workflow_id}")

    route: dict[str, Any] = {
        "schema": INVOCATION_ROUTE_SCHEMA,
        "status": "PASS",
        "mode": WORKFLOW_MODE,
        "user_payload": str(payload or ""),
        "selected_skill": None,
        "selected_workflow": workflow_id,
        "task_id": _identifier(task_id, field="task_id") if task_id else None,
        "composition_activation": activation,
        "policy": {
            "exact_selection_required": True,
            "fuzzy_fallback_allowed": False,
            "activation_grants_write_authority": False,
            "activation_completes_taskrun": False,
            "taskrun_is_completion_authority": True,
            "authority_effect": False,
        },
        "next_action": "EXECUTE_WITH_EXISTING_LANGGRAPH_RUNTIME",
    }
    if workflow_id == "harness-full-dev":
        if not task_id:
            raise HarnessInvocationError("harness-full-dev invocation requires --task-id")
        plan = load_full_development_workflow(workspace)
        state = HarnessRuntimeEngine().start(
            task_id=_identifier(task_id, field="task_id"),
            workflow_id=workflow_id,
            start_step=plan.start,
        )
        route["full_development"] = plan.as_dict()
        route["runtime_state"] = state.model_dump(mode="json")
        route["next_action"] = plan.start
    return route


def build_starter_route(
    workspace: Path,
    *,
    project_workspace: Path,
    starter_registration: Path,
    starter_entrypoint: str | None,
    starter_command: str | None,
    payload: str,
) -> dict[str, Any]:
    from starter_runtime import (
        StarterRuntimeError,
        load_starter_registration,
        resolve_starter_entrypoint,
    )

    try:
        loaded = load_starter_registration(
            project_workspace=project_workspace,
            registration=starter_registration,
            registry_workspace=workspace,
        )
        resolved = resolve_starter_entrypoint(
            loaded,
            registry_workspace=workspace,
            entrypoint=starter_entrypoint,
            command=starter_command,
            user_payload=payload,
        )
    except StarterRuntimeError as exc:
        raise HarnessInvocationError(str(exc)) from exc
    route = resolved.as_route(registry_workspace=workspace)
    route["mode"] = STARTER_MODE
    return route


def build_route(
    workspace: Path,
    *,
    skill: str | None = None,
    workflow: str | None = None,
    composition_id: str | None = None,
    task_id: str | None = None,
    payload: str = "",
    project_workspace: Path | None = None,
    starter_registration: Path | None = None,
    starter_entrypoint: str | None = None,
    starter_command: str | None = None,
) -> dict[str, Any]:
    mode = infer_mode(
        skill=skill,
        workflow=workflow,
        starter_entrypoint=starter_entrypoint,
        starter_command=starter_command,
    )
    if mode == OPEN_MODE:
        if composition_id or task_id or starter_registration or project_workspace:
            raise HarnessInvocationError(
                "OPEN invocation cannot bind composition_id, task_id, or Starter runtime options"
            )
        return build_open_route(payload=payload)
    if mode == SKILL_MODE:
        if composition_id:
            raise HarnessInvocationError("Skill invocation cannot bind a Workflow composition")
        return build_skill_route(
            workspace.resolve(),
            skill=str(skill),
            task_id=task_id,
            payload=payload,
        )
    if mode == STARTER_MODE:
        if composition_id or skill or workflow:
            raise HarnessInvocationError("Starter invocation cannot bind global Skill/Workflow options")
        if task_id:
            raise HarnessInvocationError("Starter routing does not create or bind a TaskRun")
        if project_workspace is None or starter_registration is None:
            raise HarnessInvocationError(
                "Starter invocation requires --project-workspace and --starter-registration"
            )
        return build_starter_route(
            workspace.resolve(),
            project_workspace=Path(project_workspace).resolve(),
            starter_registration=Path(starter_registration).resolve(),
            starter_entrypoint=starter_entrypoint,
            starter_command=starter_command,
            payload=payload,
        )
    return build_workflow_route(
        workspace.resolve(),
        workflow=str(workflow),
        composition_id=composition_id,
        task_id=task_id,
        payload=payload,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select OPEN mode, one exact Skill, or one exact activated Workflow"
    )
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--skill")
    selector.add_argument("--workflow")
    selector.add_argument("--starter-entrypoint")
    selector.add_argument("--starter-command")
    parser.add_argument("--composition-id")
    parser.add_argument("--task-id")
    parser.add_argument("--payload", default="")
    parser.add_argument("--project-workspace")
    parser.add_argument("--starter-registration")
    args = parser.parse_args()
    try:
        route = build_route(
            ROOT,
            skill=args.skill,
            workflow=args.workflow,
            composition_id=args.composition_id,
            task_id=args.task_id,
            payload=args.payload,
            project_workspace=Path(args.project_workspace) if args.project_workspace else None,
            starter_registration=(
                Path(args.starter_registration) if args.starter_registration else None
            ),
            starter_entrypoint=args.starter_entrypoint,
            starter_command=args.starter_command,
        )
    except (
        CapabilityRegistryError,
        CompositionBootstrapError,
        FullDevelopmentWorkflowError,
        HarnessInvocationError,
        WorkflowActivationError,
        WorkflowRegistryError,
    ) as exc:
        print(
            json.dumps(
                {"schema": INVOCATION_ROUTE_SCHEMA, "status": "FAIL", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(route, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
