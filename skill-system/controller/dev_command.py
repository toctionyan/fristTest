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

DEV_COMMAND_ROUTE_SCHEMA = "dev-command-route@1"


@dataclass(frozen=True)
class CommandSpec:
    command: str
    request_class: str
    skills: tuple[str, ...]
    mode: str
    description: str
    status_first: bool = False
    deterministic_response: bool = False
    write_governed: bool = False


COMMANDS: dict[str, CommandSpec] = {
    "/status": CommandSpec(
        command="/status",
        request_class="STATUS_QUERY",
        skills=("task-execution-status",),
        mode="STATUS",
        description="Show the authoritative whole-task execution status.",
        status_first=True,
        deterministic_response=True,
    ),
    "/continue": CommandSpec(
        command="/continue",
        request_class="STATUS_QUERY",
        skills=("task-execution-status",),
        mode="CONTINUE_AFTER_STATUS",
        description="Check authoritative status first, then continue only if the TaskRun allows it.",
        status_first=True,
        deterministic_response=True,
    ),
    "/diagnose": CommandSpec(
        command="/diagnose",
        request_class="DIAGNOSIS",
        skills=("product-code-governance",),
        mode="READ_ONLY",
        description="Perform read-only product diagnosis before deciding whether to design or repair.",
    ),
    "/arch": CommandSpec(
        command="/arch",
        request_class="DESIGN",
        skills=("architecture-options",),
        mode="READ_ONLY",
        description="Compare architecture options without modifying code.",
    ),
    "/agent-arch": CommandSpec(
        command="/agent-arch",
        request_class="DESIGN",
        skills=("architecture-options", "customer-agent-architecture"),
        mode="READ_ONLY",
        description="Review customer-agent architecture with the generic architecture comparison Skill.",
    ),
    "/oracle": CommandSpec(
        command="/oracle",
        request_class="ORACLE_REVIEW",
        skills=("oracle-review",),
        mode="READ_ONLY",
        description="Review Claim, Requirement, test, or Oracle correctness without modifying code.",
    ),
    "/repair": CommandSpec(
        command="/repair",
        request_class="REPAIR",
        skills=("product-code-governance", "red-baseline-repair"),
        mode="WRITE_GOVERNED",
        description="Enter the governed repair flow; every actual write still requires change-scope.",
        write_governed=True,
    ),
    "/review": CommandSpec(
        command="/review",
        request_class="ADVERSARIAL_REVIEW",
        skills=("adversarial-review",),
        mode="READ_ONLY",
        description="Run read-only adversarial review after a candidate repair.",
    ),
    "/cert": CommandSpec(
        command="/cert",
        request_class="CERTIFICATION",
        skills=("release-certification",),
        mode="READ_ONLY",
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

    raw = str(text or "")
    stripped = raw.lstrip()
    if not stripped:
        raise DevCommandError("development command input is empty")
    first_line, separator, remainder = stripped.partition("\n")
    token, spacing, inline_payload = first_line.partition(" ")
    command = normalize_command(token)
    payload_parts: list[str] = []
    if spacing and inline_payload:
        payload_parts.append(inline_payload)
    if separator:
        payload_parts.append(remainder)
    payload = "\n".join(payload_parts).strip()
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
    """Resolve one explicit command to a fixed Skill set.

    This is intentionally not a natural-language router. The caller chooses the
    command; the dispatcher only validates the command, loads the exact canonical
    Skills, and records selection/load evidence. The free-form payload is carried
    through unchanged for the host to consume with those Skill contexts.
    """

    workspace = workspace.resolve()
    canonical = normalize_command(command)
    spec = COMMANDS[canonical]
    prefix = _safe_invocation_prefix(invocation_prefix)
    refs = [str(item).strip() for item in context_refs if str(item).strip()]

    skill_contexts: dict[str, str] = {}
    receipt_paths: dict[str, str] = {}
    receipts: dict[str, dict[str, Any]] = {}
    for index, skill in enumerate(spec.skills, start=1):
        skill_path, context = _load_skill_context(workspace, skill)
        skill_contexts[skill] = context
        receipt = build_receipt(
            workspace,
            invocation_id=f"{prefix}-{index}",
            request_class=spec.request_class,
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

    return {
        "schema": DEV_COMMAND_ROUTE_SCHEMA,
        "status": "PASS",
        "command": canonical,
        "request_class": spec.request_class,
        "mode": spec.mode,
        "description": spec.description,
        "user_payload": str(payload or ""),
        "context_refs": refs,
        "required_skills": list(spec.skills),
        "skill_contexts": skill_contexts,
        "receipt_paths": receipt_paths,
        "receipts": receipts,
        "policy": {
            "explicit_command_is_authoritative_route": True,
            "natural_language_keyword_rerouting_allowed": False,
            "fallback_without_required_skill_allowed": False,
            "host_must_consume_user_payload": True,
            "host_must_consume_skill_contexts": True,
            "status_first": spec.status_first,
            "deterministic_response_required": spec.deterministic_response,
            "write_requires_change_scope": spec.write_governed,
        },
        "next": (
            "Run `python3 -B skillctl.py task-status-project ...` against the authoritative TaskRun; "
            "do not synthesize whole-task status from GitHub objects."
            if spec.status_first
            else (
                "Use the loaded Skill contexts and user_payload. Before any repository write, the existing "
                "change-scope Hook must pass."
                if spec.write_governed
                else "Use the loaded Skill contexts and user_payload; remain read-only."
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
        description="Route explicit low-complexity development commands to fixed canonical Skills"
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
                "status": "PASS",
                "commands": [
                    {
                        "command": spec.command,
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
    except (DevCommandError, SkillInvocationError) as exc:
        _print({"schema": DEV_COMMAND_ROUTE_SCHEMA, "status": "FAIL", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
