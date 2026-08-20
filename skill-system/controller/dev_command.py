from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from skill_invocation import (  # type: ignore  # noqa: E402
    SKILL_CONTEXT_SCHEMA,
    SkillInvocationError,
    build_receipt,
    canonical_skill_path,
    write_receipt,
)
from workflow_registry import (  # type: ignore  # noqa: E402
    WORKFLOW_REGISTRY_SCHEMA,
    WorkflowRegistryError,
    WorkflowSpec,
    require_workflow,
)

# Keep the existing route schema stable during this incremental orchestration
# refactor. New workflow fields are additive.
DEV_COMMAND_ROUTE_SCHEMA = "dev-command-route@1"


@dataclass(frozen=True)
class CommandSpec:
    command: str
    workflow_id: str
    description: str

    # Compatibility accessors keep existing callers/tests readable while the
    # authoritative route is now command -> Workflow -> Skill(s).
    @property
    def workflow(self) -> WorkflowSpec:
        return require_workflow(ROOT, self.workflow_id)

    @property
    def request_class(self) -> str:
        return self.workflow.request_class

    @property
    def skills(self) -> tuple[str, ...]:
        return self.workflow.skills

    @property
    def mode(self) -> str:
        return self.workflow.mode

    @property
    def status_first(self) -> bool:
        return self.workflow.status_first

    @property
    def deterministic_response(self) -> bool:
        return self.workflow.deterministic_response

    @property
    def write_governed(self) -> bool:
        return self.workflow.write_governed


COMMANDS: dict[str, CommandSpec] = {
    "/status": CommandSpec(
        command="/status",
        workflow_id="status-project",
        description="Show the authoritative whole-task execution status.",
    ),
    "/continue": CommandSpec(
        command="/continue",
        workflow_id="continue-project",
        description="Check authoritative status first, then continue only if the TaskRun allows it.",
    ),
    "/diagnose": CommandSpec(
        command="/diagnose",
        workflow_id="diagnose-product",
        description="Perform read-only product diagnosis before deciding whether to design or repair.",
    ),
    "/arch": CommandSpec(
        command="/arch",
        workflow_id="architecture-review",
        description="Compare architecture options without modifying code.",
    ),
    "/agent-arch": CommandSpec(
        command="/agent-arch",
        workflow_id="customer-agent-architecture-review",
        description="Review customer-agent architecture with the generic architecture comparison Skill.",
    ),
    "/oracle": CommandSpec(
        command="/oracle",
        workflow_id="oracle-review",
        description="Review Claim, Requirement, test, or Oracle correctness without modifying code.",
    ),
    "/repair": CommandSpec(
        command="/repair",
        workflow_id="governed-repair",
        description="Enter the governed repair flow; every actual write still requires change-scope.",
    ),
    "/review": CommandSpec(
        command="/review",
        workflow_id="adversarial-review",
        description="Run read-only adversarial review after a candidate repair.",
    ),
    "/cert": CommandSpec(
        command="/cert",
        workflow_id="release-certification",
        description="Certify an immutable candidate without repairing it.",
    ),
}


class DevCommandError(ValueError):
    """Raised when an explicit development command cannot be routed safely."""


def _text(value: object) -> str:
    return str(value or "").strip()


def normalize_command(command: str) -> str:
    value = _text(command).lower()
    if value not in COMMANDS:
        supported = ", ".join(COMMANDS)
        raise DevCommandError(f"unsupported development command {command!r}; supported: {supported}")
    return value


def parse_command_text(text: str) -> tuple[str, str]:
    """Parse `/command` plus arbitrary natural-language payload.

    The first non-whitespace token is routing only. Everything after it remains
    user payload; it is not reclassified by keyword rules.
    """

    stripped = str(text or "").lstrip()
    if not stripped:
        raise DevCommandError("development command input is empty")
    parts = stripped.split(None, 1)
    command = normalize_command(parts[0])
    payload = parts[1].strip() if len(parts) == 2 else ""
    return command, payload


def _safe_invocation_prefix(value: str) -> str:
    raw = _text(value)
    if not raw:
        raise DevCommandError("--invocation-prefix is required")
    normalized = "".join(char if char.isalnum() or char in "._:-" else "-" for char in raw)
    normalized = normalized.strip("-")
    if not normalized:
        raise DevCommandError("--invocation-prefix does not contain a safe token")
    return normalized


def _load_skill_context(workspace: Path, skill: str) -> tuple[str, str]:
    relative = canonical_skill_path(skill)
    path = workspace / relative
    if not path.is_file():
        raise DevCommandError(f"canonical Skill is missing: {relative.as_posix()}")
    return relative.as_posix(), path.read_text(encoding="utf-8")


def _response_binding_commands(
    workflow: WorkflowSpec,
    *,
    task_id: str | None,
    change_id: str | None,
) -> list[str]:
    if workflow.status_first:
        return []
    subject_flags: list[str] = []
    if change_id:
        subject_flags.extend(["--change-id", change_id])
    if task_id:
        subject_flags.extend(["--task-id", task_id])
    suffix = " " + " ".join(subject_flags) if subject_flags else ""
    return [
        (
            "python3 -B skillctl.py skill-response-bind "
            f"--request-class {workflow.request_class} --skill {skill}{suffix} "
            "--invocation-id <unique-id> --response-file <response-file>"
        )
        for skill in workflow.skills
    ]


def build_route(
    workspace: Path,
    *,
    command: str,
    payload: str,
    invocation_prefix: str,
    task_id: str | None = None,
    change_id: str | None = None,
    context_refs: Iterable[str] = (),
    persist_receipts: bool = True,
) -> dict[str, Any]:
    """Resolve one explicit command through a fixed Workflow to canonical Skills.

    This is intentionally not a natural-language router. The caller chooses the
    command; the command selects one explicit Workflow; the Workflow selects the
    required canonical Skills. Free-form payload is carried through unchanged.

    Workflow definitions are target-independent. task_id/change_id remain
    invocation subject evidence and are not embedded into Workflow definitions.
    Existing Skill Invocation, TaskRun, Quality, change-scope and completion
    authorities are preserved unchanged.
    """

    workspace = workspace.resolve()
    canonical = normalize_command(command)
    command_spec = COMMANDS[canonical]
    workflow = require_workflow(workspace, command_spec.workflow_id)
    prefix = _safe_invocation_prefix(invocation_prefix)
    refs = [str(item).strip() for item in context_refs if str(item).strip()]

    skill_contexts: dict[str, str] = {}
    receipt_paths: dict[str, str] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for index, skill in enumerate(workflow.skills, start=1):
        skill_path, context = _load_skill_context(workspace, skill)
        skill_contexts[skill] = context
        receipt = build_receipt(
            workspace,
            invocation_id=f"{prefix}-{index}",
            request_class=workflow.request_class,
            required_skill=skill,
            selected_skill=skill,
            entrypoint=skill_path,
            output_schema=SKILL_CONTEXT_SCHEMA,
            output_content=context,
            output_evidence_ref=f"stdout:skill_contexts.{skill}",
            change_id=change_id,
            task_id=task_id,
            response_bound=False,
        )
        receipts[skill] = receipt
        if persist_receipts:
            path = write_receipt(workspace, receipt)
            receipt_paths[skill] = path.relative_to(workspace).as_posix()

    binding_commands = _response_binding_commands(
        workflow,
        task_id=task_id,
        change_id=change_id,
    )
    return {
        "schema": DEV_COMMAND_ROUTE_SCHEMA,
        "status": "PASS",
        "command": canonical,
        "workflow_registry_schema": WORKFLOW_REGISTRY_SCHEMA,
        "workflow_id": workflow.workflow_id,
        "workflow": workflow.as_dict(),
        "request_class": workflow.request_class,
        "mode": workflow.mode,
        "description": command_spec.description,
        "user_payload": str(payload or ""),
        "context_refs": refs,
        "required_skills": list(workflow.skills),
        "skill_contexts": skill_contexts,
        "receipt_paths": receipt_paths,
        "receipts": receipts,
        "policy": {
            "explicit_command_is_authoritative_route": True,
            "command_selects_workflow_not_skill": True,
            "workflow_selects_required_skills": True,
            "workflow_target_binding_allowed": False,
            "natural_language_keyword_rerouting_allowed": False,
            "fallback_without_required_skill_allowed": False,
            "host_must_consume_user_payload": True,
            "host_must_consume_skill_contexts": True,
            "response_binding_required": True,
            "response_binding_entrypoint": (
                "task-status-project" if workflow.status_first else "skill-response-bind"
            ),
            "status_first": workflow.status_first,
            "deterministic_response_required": workflow.deterministic_response,
            "write_requires_change_scope": workflow.write_governed,
            "taskrun_authority_changed": False,
            "skill_invocation_authority_changed": False,
            "quality_authority_changed": False,
            "github_authority_changed": False,
        },
        "completion": {
            "response_binding_required": True,
            "binding_commands": binding_commands,
            "verify_requirement": "skill-invocation-verify --require-response-bound",
        },
        "next": (
            "Run `python3 -B skillctl.py task-status-project ...` against the authoritative TaskRun; "
            "do not synthesize whole-task status from GitHub objects. The projector itself must create "
            "the response-bound receipt."
            if workflow.status_first
            else (
                "Use the Workflow-selected Skill contexts and user_payload. Before any repository write, the existing "
                "change-scope Hook must pass. Before the final user-facing response, bind that exact response "
                "to every required Skill with `skill-response-bind`."
                if workflow.write_governed
                else "Use the Workflow-selected Skill contexts and user_payload; remain read-only. Before the final "
                "user-facing response, bind that exact response to every required Skill with `skill-response-bind`."
            )
        ),
    }


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _read_payload_file(path: str | None) -> str:
    if not path:
        return ""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    try:
        return candidate.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DevCommandError(f"payload file is missing: {candidate}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Route explicit low-complexity development commands through fixed Workflows to canonical Skills"
    )
    parser.add_argument("--list", action="store_true", help="List supported explicit commands")
    parser.add_argument("--command")
    parser.add_argument("--text", help="Full `/command` + free-form payload text")
    parser.add_argument("--payload", default="")
    parser.add_argument("--payload-file")
    parser.add_argument("--context-ref", action="append", default=[])
    parser.add_argument("--task-id")
    parser.add_argument("--change-id")
    parser.add_argument("--invocation-prefix")
    args = parser.parse_args()

    try:
        if args.list:
            _print({
                "schema": DEV_COMMAND_ROUTE_SCHEMA,
                "workflow_registry_schema": WORKFLOW_REGISTRY_SCHEMA,
                "status": "PASS",
                "commands": [
                    {
                        "command": spec.command,
                        "workflow_id": spec.workflow_id,
                        "request_class": spec.request_class,
                        "skills": list(spec.skills),
                        "mode": spec.mode,
                        "description": spec.description,
                    }
                    for spec in COMMANDS.values()
                ],
            })
            return 0

        if args.text:
            if args.command or args.payload or args.payload_file:
                raise DevCommandError("--text cannot be combined with --command/--payload/--payload-file")
            command, payload = parse_command_text(args.text)
        else:
            if not args.command:
                raise DevCommandError("use --command or --text")
            command = normalize_command(args.command)
            if args.payload and args.payload_file:
                raise DevCommandError("use only one of --payload or --payload-file")
            payload = _read_payload_file(args.payload_file) if args.payload_file else str(args.payload or "")

        if not args.invocation_prefix:
            raise DevCommandError("--invocation-prefix is required for routed commands")
        route = build_route(
            ROOT,
            command=command,
            payload=payload,
            invocation_prefix=args.invocation_prefix,
            task_id=args.task_id,
            change_id=args.change_id,
            context_refs=args.context_ref,
            persist_receipts=True,
        )
        _print(route)
        return 0
    except (DevCommandError, SkillInvocationError, WorkflowRegistryError) as exc:
        _print({"schema": DEV_COMMAND_ROUTE_SCHEMA, "status": "FAIL", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
