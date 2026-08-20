from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from plugin_registry import PluginRegistryError, resolve_skill_plugin  # type: ignore  # noqa: E402
from skill_invocation import SKILL_CONTEXT_SCHEMA, SkillInvocationError, build_receipt, write_receipt  # type: ignore  # noqa: E402
from workflow_spec import WorkflowSpecError, load_workflow_spec  # type: ignore  # noqa: E402

PLUGIN_ROUTE_SCHEMA = "plugin-invocation-route@1"
OPEN_MODE = "OPEN"
SKILL_MODE = "SKILL_BOUND"
WORKFLOW_MODE = "WORKFLOW_BOUND"
EXPLICIT_SKILL_REQUEST_CLASS = "EXPLICIT_SKILL"


class PluginGatewayError(ValueError):
    """Raised when the caller supplies an ambiguous or invalid plugin route."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _refs(values: Iterable[str]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def infer_structural_mode(*, skill: str | None = None, workflow: str | None = None) -> str:
    """Infer only from explicit structured selectors, never from request language."""
    if skill and workflow:
        raise PluginGatewayError("choose at most one explicit Skill or Workflow")
    if skill:
        return SKILL_MODE
    if workflow:
        return WORKFLOW_MODE
    return OPEN_MODE


def build_open_route(*, payload: str, target: str | None = None, context_refs: Iterable[str] = ()) -> dict[str, Any]:
    return {"schema": PLUGIN_ROUTE_SCHEMA, "status": "PASS", "mode": OPEN_MODE, "target": _text(target), "user_payload": str(payload or ""), "context_refs": _refs(context_refs), "selected_skill": None, "selected_workflow": None, "policy": {"automatic_skill_selection_allowed": False, "automatic_workflow_selection_allowed": False, "open_mode_when_unspecified": True, "authority_effect": False}, "next": "Perform open analysis without fabricating Skill invocation evidence. If the user later names a Skill or Workflow, create a new explicit route."}


def build_skill_route(workspace: Path, *, skill: str, payload: str, invocation_id: str, target: str | None = None, task_id: str | None = None, change_id: str | None = None, context_refs: Iterable[str] = (), persist_receipt: bool = True) -> dict[str, Any]:
    workspace = workspace.resolve()
    plugin = resolve_skill_plugin(workspace, skill)
    context = (workspace / plugin.path).read_text(encoding="utf-8")
    receipt = build_receipt(workspace, invocation_id=invocation_id, request_class=EXPLICIT_SKILL_REQUEST_CLASS, required_skill=plugin.name, selected_skill=plugin.name, entrypoint=plugin.path, output_schema=SKILL_CONTEXT_SCHEMA, output_content=context, output_evidence_ref=f"stdout:skill_context.{plugin.name}", task_id=task_id, change_id=change_id, response_bound=False)
    receipt_path = write_receipt(workspace, receipt).relative_to(workspace).as_posix() if persist_receipt else None
    subject_flags: list[str] = []
    if change_id: subject_flags.extend(["--change-id", change_id])
    if task_id: subject_flags.extend(["--task-id", task_id])
    suffix = " " + " ".join(subject_flags) if subject_flags else ""
    bind_command = f"python3 -B skillctl.py skill-response-bind --request-class {EXPLICIT_SKILL_REQUEST_CLASS} --skill {plugin.name}{suffix} --invocation-id <unique-id> --response-file <response-file>"
    return {"schema": PLUGIN_ROUTE_SCHEMA, "status": "PASS", "mode": SKILL_MODE, "target": _text(target), "user_payload": str(payload or ""), "context_refs": _refs(context_refs), "selected_skill": plugin.name, "selected_workflow": None, "skill_context": context, "receipt": receipt, "receipt_path": receipt_path, "policy": {"explicit_skill_is_authoritative_route": True, "automatic_skill_selection_allowed": False, "fuzzy_skill_fallback_allowed": False, "host_must_consume_skill_context": True, "host_must_consume_user_payload": True, "response_binding_required": True, "authority_effect": False}, "completion": {"binding_command": bind_command, "verify_requirement": f"skill-invocation-verify --request-class {EXPLICIT_SKILL_REQUEST_CLASS} --skill {plugin.name} --require-response-bound"}, "next": "Execute the exact selected Skill against the target and payload. Before treating the invocation as complete, bind the exact final response to this Skill with skill-response-bind."}


def build_workflow_route(workspace: Path, *, workflow: str, payload: str, target: str | None = None, task_id: str | None = None, context_refs: Iterable[str] = ()) -> dict[str, Any]:
    spec = load_workflow_spec(workspace.resolve(), workflow)
    return {"schema": PLUGIN_ROUTE_SCHEMA, "status": "PASS", "mode": WORKFLOW_MODE, "target": _text(target), "user_payload": str(payload or ""), "context_refs": _refs(context_refs), "task_id": _text(task_id), "selected_skill": None, "selected_workflow": spec["id"], "workflow": spec, "policy": {"explicit_workflow_is_authoritative_route": True, "automatic_skill_selection_allowed": False, "automatic_workflow_selection_allowed": False, "workflow_owns_step_order_only": True, "taskrun_is_lifecycle_authority": True, "workflow_runtime_authority_effect": False}, "next": "Execute this validated Workflow through the LangGraph workflow runtime. Each Skill step must still resolve the exact active Skill and produce its own invocation/output evidence; deterministic executors and gates remain separate."}


def build_route(workspace: Path, *, mode: str, payload: str, target: str | None = None, skill: str | None = None, workflow: str | None = None, invocation_id: str | None = None, task_id: str | None = None, change_id: str | None = None, context_refs: Iterable[str] = (), persist_receipt: bool = True) -> dict[str, Any]:
    normalized = _text(mode).upper()
    if normalized == OPEN_MODE:
        if skill or workflow or invocation_id: raise PluginGatewayError("OPEN mode cannot select a Skill/Workflow or create an invocation receipt")
        return build_open_route(payload=payload, target=target, context_refs=context_refs)
    if normalized == SKILL_MODE:
        if not skill or workflow: raise PluginGatewayError("SKILL_BOUND mode requires exactly one --skill")
        if not invocation_id: raise PluginGatewayError("SKILL_BOUND mode requires --invocation-id")
        return build_skill_route(workspace, skill=skill, payload=payload, invocation_id=invocation_id, target=target, task_id=task_id, change_id=change_id, context_refs=context_refs, persist_receipt=persist_receipt)
    if normalized == WORKFLOW_MODE:
        if not workflow or skill or invocation_id: raise PluginGatewayError("WORKFLOW_BOUND mode requires exactly one --workflow and does not create a top-level Skill receipt")
        return build_workflow_route(workspace, workflow=workflow, payload=payload, target=target, task_id=task_id, context_refs=context_refs)
    raise PluginGatewayError(f"unsupported plugin mode: {mode!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Invoke OPEN mode, one exact Skill, or one exact Workflow without semantic guessing")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--skill")
    selector.add_argument("--workflow")
    parser.add_argument("--mode", choices=[OPEN_MODE, SKILL_MODE, WORKFLOW_MODE], help="Compatibility override; normally omit it and let explicit --skill/--workflow select the structural mode.")
    parser.add_argument("--payload", default="")
    parser.add_argument("--target")
    parser.add_argument("--task-id")
    parser.add_argument("--change-id")
    parser.add_argument("--context-ref", action="append", default=[])
    parser.add_argument("--invocation-id")
    args = parser.parse_args()
    try:
        inferred = infer_structural_mode(skill=args.skill, workflow=args.workflow)
        mode = args.mode or inferred
        if args.mode and mode != inferred:
            raise PluginGatewayError(f"explicit --mode {args.mode} conflicts with supplied selector; expected {inferred}")
        route = build_route(ROOT, mode=mode, payload=args.payload, target=args.target, skill=args.skill, workflow=args.workflow, invocation_id=args.invocation_id, task_id=args.task_id, change_id=args.change_id, context_refs=args.context_ref, persist_receipt=True)
    except (PluginGatewayError, PluginRegistryError, SkillInvocationError, WorkflowSpecError) as exc:
        print(json.dumps({"schema": PLUGIN_ROUTE_SCHEMA, "status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2)); return 1
    print(json.dumps(route, ensure_ascii=False, indent=2, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
