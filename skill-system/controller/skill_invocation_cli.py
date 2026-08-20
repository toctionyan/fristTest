from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from execution_progress import render_progress_text  # type: ignore  # noqa: E402
from skill_invocation import (  # type: ignore  # noqa: E402
    SKILL_CONTEXT_SCHEMA,
    SkillInvocationError,
    build_receipt,
    canonical_skill_path,
    find_active_receipt,
    load_receipt,
    validate_receipt,
    write_receipt,
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SkillInvocationError(f"JSON input is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SkillInvocationError(f"JSON input is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise SkillInvocationError(f"JSON input must be an object: {path}")
    return payload


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _workspace_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise SkillInvocationError(f"path must stay inside the workspace: {path}") from exc
    return path


def _read_response(args: argparse.Namespace) -> tuple[str, str]:
    if args.response and args.response_file:
        raise SkillInvocationError("use only one of --response or --response-file")
    if args.response_file:
        path = _workspace_path(args.response_file)
        try:
            response = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise SkillInvocationError(f"response file is missing: {path}") from exc
        evidence_ref = args.evidence_ref or f"file:{path.relative_to(ROOT).as_posix()}"
    else:
        response = str(args.response or "")
        evidence_ref = args.evidence_ref or "arg:response"
    if not response.strip():
        raise SkillInvocationError("response payload must not be empty")
    return response, evidence_ref


def cmd_load(args: argparse.Namespace) -> int:
    skill_path = canonical_skill_path(args.skill)
    absolute = ROOT / skill_path
    if not absolute.is_file():
        raise SkillInvocationError(f"canonical Skill is missing: {skill_path.as_posix()}")
    context = absolute.read_text(encoding="utf-8")
    receipt = build_receipt(
        ROOT,
        invocation_id=args.invocation_id,
        request_class=args.request_class,
        required_skill=args.skill,
        selected_skill=args.skill,
        entrypoint=skill_path.as_posix(),
        output_schema=SKILL_CONTEXT_SCHEMA,
        output_content=context,
        output_evidence_ref="stdout:skill_context",
        change_id=args.change_id,
        task_id=args.task_id,
        response_bound=False,
    )
    path = write_receipt(ROOT, receipt)
    _print({
        "status": "PASS",
        "skill_context": context,
        "receipt_path": path.relative_to(ROOT).as_posix(),
        "receipt": receipt,
    })
    return 0


def _renderer_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "-B",
        str(ROOT / "scripts" / "render_task_progress.py"),
        "--task-run",
        str(Path(args.task_run)),
        "--format",
        "json",
    ]
    for flag, value in (
        ("--github-jobs", args.github_jobs),
        ("--github-steps", args.github_steps),
        ("--quality-results", args.quality_results),
        ("--run-id", args.run_id),
        ("--workflow", args.workflow),
        ("--head-sha", args.head_sha),
    ):
        if value is not None:
            command.extend([flag, str(value)])
    return command


def cmd_status_project(args: argparse.Namespace) -> int:
    skill_name = "task-execution-status"
    skill_path = canonical_skill_path(skill_name)
    if not (ROOT / skill_path).is_file():
        raise SkillInvocationError(f"canonical Skill is missing: {skill_path.as_posix()}")
    task_path = Path(args.task_run)
    if not task_path.is_absolute():
        task_path = ROOT / task_path
    task_payload = _read_json(task_path)

    completed = subprocess.run(
        _renderer_command(args),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise SkillInvocationError(
            "task status projection failed: " + (completed.stderr or completed.stdout)[-2000:]
        )
    try:
        projection = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SkillInvocationError("task status projection did not return JSON") from exc
    if not isinstance(projection, dict) or projection.get("schema") != "execution-progress@1":
        raise SkillInvocationError("task status projection returned the wrong schema")
    rendered_text = render_progress_text(projection)
    task = projection.get("task") if isinstance(projection.get("task"), dict) else {}
    task_id = str(task.get("task_id") or args.task_id or "").strip() or None
    if args.task_id and task_id != args.task_id:
        raise SkillInvocationError("projected TaskRun id does not match --task-id")

    receipt = build_receipt(
        ROOT,
        invocation_id=args.invocation_id,
        request_class="STATUS_QUERY",
        required_skill=skill_name,
        selected_skill=skill_name,
        entrypoint="scripts/render_task_progress.py",
        output_schema="execution-progress@1",
        output_content=rendered_text,
        output_evidence_ref="stdout:rendered_text",
        task_id=task_id,
        response_bound=True,
    )
    path = write_receipt(ROOT, receipt)
    _print({
        "status": "PASS",
        "projection": projection,
        "rendered_text": rendered_text,
        "receipt_path": path.relative_to(ROOT).as_posix(),
        "receipt": receipt,
    })
    return 0


def cmd_bind_response(args: argparse.Namespace) -> int:
    if args.receipt:
        source_path = _workspace_path(args.receipt)
        source = load_receipt(source_path)
    else:
        if not args.request_class or not args.skill:
            raise SkillInvocationError(
                "active response binding requires --request-class and --skill when --receipt is omitted"
            )
        source_path, source = find_active_receipt(
            ROOT,
            request_class=args.request_class,
            skill=args.skill,
            change_id=args.change_id,
            task_id=args.task_id,
        )

    source = validate_receipt(
        ROOT,
        source,
        expected_request_class=args.request_class,
        expected_skill=args.skill,
        expected_change_id=args.change_id,
        expected_task_id=args.task_id,
    )
    if source.get("output", {}).get("response_bound") is True:
        raise SkillInvocationError("source Skill invocation is already response-bound")

    response, evidence_ref = _read_response(args)
    subject = source.get("subject") if isinstance(source.get("subject"), dict) else {}
    source_fingerprint = str(source.get("receipt_fingerprint_sha256") or "")
    lineage_ref = f"{evidence_ref};source_receipt_sha256={source_fingerprint}"
    receipt = build_receipt(
        ROOT,
        invocation_id=args.invocation_id,
        request_class=str(source["request_class"]),
        required_skill=str(source["required_skill"]),
        selected_skill=str(source["selected_skill"]),
        entrypoint=str(source["canonical_skill_path"]),
        output_schema="host-response@1",
        output_content=response,
        output_evidence_ref=lineage_ref,
        change_id=str(subject.get("change_id") or "") or None,
        task_id=str(subject.get("task_id") or "") or None,
        response_bound=True,
    )
    path = write_receipt(ROOT, receipt)
    validate_receipt(
        ROOT,
        receipt,
        expected_request_class=str(source["request_class"]),
        expected_skill=str(source["selected_skill"]),
        expected_change_id=str(subject.get("change_id") or "") or None,
        expected_task_id=str(subject.get("task_id") or "") or None,
        require_response_bound=True,
    )
    _print({
        "status": "PASS",
        "source_receipt_path": source_path.relative_to(ROOT).as_posix(),
        "receipt_path": path.relative_to(ROOT).as_posix(),
        "receipt": receipt,
    })
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    if args.receipt:
        path = Path(args.receipt)
        if not path.is_absolute():
            path = ROOT / path
        payload = load_receipt(path)
    else:
        if not args.request_class or not args.skill:
            raise SkillInvocationError(
                "active receipt lookup requires --request-class and --skill when --receipt is omitted"
            )
        path, payload = find_active_receipt(
            ROOT,
            request_class=args.request_class,
            skill=args.skill,
            change_id=args.change_id,
            task_id=args.task_id,
        )
    payload = validate_receipt(
        ROOT,
        payload,
        expected_request_class=args.request_class,
        expected_skill=args.skill,
        expected_change_id=args.change_id,
        expected_task_id=args.task_id,
        require_response_bound=args.require_response_bound,
    )
    _print({"status": "PASS", "receipt_path": path.relative_to(ROOT).as_posix(), "receipt": payload})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and verify durable host Skill invocation evidence")
    sub = parser.add_subparsers(dest="command", required=True)

    load = sub.add_parser("load")
    load.add_argument("--skill", required=True)
    load.add_argument("--request-class", required=True)
    load.add_argument("--invocation-id", required=True)
    load.add_argument("--change-id")
    load.add_argument("--task-id")
    load.set_defaults(func=cmd_load)

    status = sub.add_parser("status-project")
    status.add_argument("--task-run", required=True)
    status.add_argument("--github-jobs")
    status.add_argument("--github-steps")
    status.add_argument("--quality-results")
    status.add_argument("--run-id", type=int)
    status.add_argument("--workflow")
    status.add_argument("--head-sha")
    status.add_argument("--task-id")
    status.add_argument("--invocation-id", required=True)
    status.set_defaults(func=cmd_status_project)

    bind = sub.add_parser("bind-response")
    bind.add_argument("--receipt")
    bind.add_argument("--request-class")
    bind.add_argument("--skill")
    bind.add_argument("--change-id")
    bind.add_argument("--task-id")
    bind.add_argument("--invocation-id", required=True)
    bind.add_argument("--response")
    bind.add_argument("--response-file")
    bind.add_argument("--evidence-ref")
    bind.set_defaults(func=cmd_bind_response)

    verify = sub.add_parser("verify")
    verify.add_argument("--receipt")
    verify.add_argument("--request-class")
    verify.add_argument("--skill")
    verify.add_argument("--change-id")
    verify.add_argument("--task-id")
    verify.add_argument("--require-response-bound", action="store_true")
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    try:
        return int(args.func(args))
    except SkillInvocationError as exc:
        _print({"status": "FAIL", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
