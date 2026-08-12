#!/usr/bin/env python3
"""Validate Stage-2 repair artifacts and publish only a fully tested Draft candidate.

Stage 3 is intentionally independent from the model fixer. It verifies the immutable
handoff, applies the patch to the exact failed commit, runs fixed targeted suites and
the repository Quick quality loop, then records a Draft PR publication. It never
merges a branch, changes Secrets, runs production certification, or closes production.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPTS = Path(__file__).resolve().parent
for entry in (str(CONTROL), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from task_run import TaskRunStore  # type: ignore  # noqa: E402
from github_agent_fixer import (  # noqa: E402
    FixerError,
    validate_allowed_paths,
    verify_changed_files,
)

STAGE2_SCHEMA = "github-governed-repair-stage2@1"
STAGE3_SCHEMA = "github-governed-repair-stage3@1"
MAX_PATCH_BYTES = 2_000_000
MAX_OUTPUT_CHARS = 40_000


class Stage3Error(RuntimeError):
    """Fail-closed Stage-3 validation error."""


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise Stage3Error(f"JSON object required: {path}")
    return payload


def _write_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 1800,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=dict(env) if env is not None else os.environ.copy(),
    )


def _git(workspace: Path, *args: str, timeout: int = 180) -> str:
    completed = _run(["git", *args], cwd=workspace, timeout=timeout)
    if completed.returncode:
        message = (completed.stderr or completed.stdout or "git command failed").strip()
        raise Stage3Error(message[:MAX_OUTPUT_CHARS])
    return completed.stdout.strip()


def _changed_paths(workspace: Path) -> tuple[str, ...]:
    completed = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=workspace,
        timeout=180,
    )
    if completed.returncode:
        message = (completed.stderr or completed.stdout or "git status failed").strip()
        raise Stage3Error(message[:MAX_OUTPUT_CHARS])
    rows = completed.stdout.splitlines()
    result: list[str] = []
    for row in rows:
        raw = row[3:] if len(row) > 3 else ""
        path = raw.split(" -> ")[-1].strip().replace("\\", "/")
        if path and path not in result:
            result.append(path)
    return tuple(result)


def _diff_fingerprint(workspace: Path) -> str:
    patch = _git(workspace, "diff", "HEAD", "--no-ext-diff", "--binary")
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()


def _normalize_path(raw: str) -> str:
    value = str(raw).strip().replace("\\", "/")
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise Stage3Error(f"invalid repository path: {raw!r}")
    normalized = pure.as_posix()
    if normalized != value:
        raise Stage3Error(f"non-canonical repository path: {raw!r}")
    return normalized


def _open_task(path: Path) -> TaskRunStore:
    payload = _load_object(path)
    return TaskRunStore(path.resolve(), payload)


def _task_binding(task: TaskRunStore) -> dict[str, Any]:
    binding = task.payload.get("binding")
    if not isinstance(binding, dict):
        raise Stage3Error("TaskRun binding is missing")
    return binding


def _validate_binding(task: TaskRunStore, result: dict[str, Any]) -> None:
    binding = _task_binding(task)
    expected = {
        "repository": result.get("repository"),
        "workflow_run_id": str(result.get("workflow_run_id")),
        "head_sha": result.get("head_sha"),
        "failure_signature": result.get("failure_signature"),
    }
    mismatched = [
        key for key, value in expected.items() if not value or str(binding.get(key)) != str(value)
    ]
    if mismatched:
        raise Stage3Error(f"Stage-2 TaskRun binding mismatch: {mismatched}")
    if task.payload.get("status") != "WAITING_EXTERNAL_RESULT":
        raise Stage3Error("TaskRun is not waiting for Stage-3 validation")
    if task.payload.get("phase") != "STAGE3_VALIDATION_REQUIRED":
        raise Stage3Error("TaskRun phase is not STAGE3_VALIDATION_REQUIRED")


def _validate_stage2_result(result: dict[str, Any]) -> None:
    if result.get("schema") != STAGE2_SCHEMA:
        raise Stage3Error("unsupported Stage-2 result schema")
    if result.get("status") != "REPAIR_CANDIDATE_READY":
        raise Stage3Error("Stage 2 did not produce a repair candidate")
    if result.get("deterministic_file_verification_passed") is not True:
        raise Stage3Error("Stage-2 deterministic file verification did not pass")
    for field in ("full_validation_passed", "draft_pr_published", "production_closed"):
        if result.get(field) is not False:
            raise Stage3Error(f"invalid Stage-2 authority boundary: {field}")
    for field in (
        "repository",
        "workflow_run_id",
        "head_sha",
        "failure_signature",
        "repair_branch",
        "repair_base_branch",
        "patch_sha256",
    ):
        if not str(result.get(field) or "").strip():
            raise Stage3Error(f"Stage-2 result is missing {field}")
    repair_branch = str(result["repair_branch"])
    base_branch = str(result["repair_base_branch"])
    if not repair_branch.startswith("governed-repair/"):
        raise Stage3Error("repair branch is outside governed-repair namespace")
    if repair_branch == base_branch or base_branch.startswith("governed-repair/"):
        raise Stage3Error("invalid repair/base branch relationship")


def inspect_handoff(
    *,
    result_path: Path,
    task_run_path: Path,
    patch_path: Path,
) -> dict[str, Any]:
    result = _load_object(result_path)
    task = _open_task(task_run_path)
    _validate_stage2_result(result)
    _validate_binding(task, result)
    if not patch_path.is_file() or patch_path.is_symlink():
        raise Stage3Error("Stage-2 patch must be an existing regular file")
    data = patch_path.read_bytes()
    if not data or len(data) > MAX_PATCH_BYTES or b"\x00" in data:
        raise Stage3Error("Stage-2 patch is empty, oversized, or binary")
    digest = hashlib.sha256(data).hexdigest()
    if digest != str(result.get("patch_sha256")):
        raise Stage3Error("Stage-2 patch digest mismatch")
    changed = tuple(_normalize_path(str(item)) for item in result.get("changed_paths") or [])
    if not changed or len(set(changed)) != len(changed):
        raise Stage3Error("Stage-2 changed path set is empty or duplicated")
    return {
        "publish_allowed": True,
        "repository": str(result["repository"]),
        "source_run_id": str(result["workflow_run_id"]),
        "head_sha": str(result["head_sha"]),
        "failure_signature": str(result["failure_signature"]),
        "repair_branch": str(result["repair_branch"]),
        "repair_base_branch": str(result["repair_base_branch"]),
        "changed_paths": list(changed),
        "patch_sha256": digest,
    }


def _target_components(paths: Iterable[str], workspace: Path) -> list[str]:
    components: set[str] = set()
    for path in paths:
        if path.startswith("services/agent-service/frontend/"):
            components.add("agent-frontend")
        elif path.startswith("services/agent-service/"):
            components.add("agent-python")
        elif path.startswith("services/business-service/"):
            components.add("business-python")
        elif path.startswith("contracts/"):
            components.update({"agent-python", "business-python", "agent-frontend"})
        elif path.startswith("web/"):
            if not (workspace / "web" / "package.json").is_file():
                raise Stage3Error("web source changed but web/package.json is unavailable")
            components.add("web-node")
        else:
            raise Stage3Error(f"no fixed targeted suite for changed path: {path}")
    if not components:
        raise Stage3Error("targeted validation component set is empty")
    order = ("agent-python", "business-python", "agent-frontend", "web-node")
    return [name for name in order if name in components]


def prepare_candidate(
    *,
    workspace: Path,
    result_path: Path,
    task_run_path: Path,
    patch_path: Path,
    plan_path: Path,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    handoff = inspect_handoff(
        result_path=result_path,
        task_run_path=task_run_path,
        patch_path=patch_path,
    )
    actual_head = _git(workspace, "rev-parse", "HEAD")
    if actual_head != handoff["head_sha"]:
        raise Stage3Error("candidate checkout does not match Stage-2 head SHA")
    if _changed_paths(workspace):
        raise Stage3Error("Stage-3 candidate workspace must start clean")

    expected_paths = tuple(handoff["changed_paths"])
    try:
        validate_allowed_paths(workspace, expected_paths)
    except FixerError as exc:
        raise Stage3Error(str(exc)) from exc

    check = _run(
        ["git", "apply", "--check", "--whitespace=error-all", str(patch_path.resolve())],
        cwd=workspace,
        timeout=180,
    )
    if check.returncode:
        raise Stage3Error((check.stderr or check.stdout or "git apply --check failed")[:MAX_OUTPUT_CHARS])
    apply_result = _run(
        ["git", "apply", "--whitespace=error-all", str(patch_path.resolve())],
        cwd=workspace,
        timeout=180,
    )
    if apply_result.returncode:
        raise Stage3Error((apply_result.stderr or apply_result.stdout or "git apply failed")[:MAX_OUTPUT_CHARS])

    actual_paths = _changed_paths(workspace)
    if set(actual_paths) != set(expected_paths) or len(actual_paths) != len(expected_paths):
        raise Stage3Error(
            f"applied patch path mismatch: expected={list(expected_paths)} actual={list(actual_paths)}"
        )
    try:
        validate_allowed_paths(workspace, actual_paths)
    except FixerError as exc:
        raise Stage3Error(str(exc)) from exc
    deterministic_passed, verification = verify_changed_files(workspace, actual_paths)
    if not deterministic_passed:
        raise Stage3Error(f"Stage-3 deterministic verification failed: {verification}")

    components = _target_components(actual_paths, workspace)
    _git(workspace, "config", "user.name", "github-actions[bot]")
    _git(
        workspace,
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    _git(workspace, "add", "--", *actual_paths)
    _git(
        workspace,
        "commit",
        "-m",
        f"Governed repair candidate for workflow run {handoff['source_run_id']}",
    )
    candidate_sha = _git(workspace, "rev-parse", "HEAD")
    if _changed_paths(workspace):
        raise Stage3Error("candidate workspace is dirty after local repair commit")

    plan = {
        "schema": STAGE3_SCHEMA,
        "status": "CANDIDATE_PREPARED",
        **handoff,
        "candidate_sha": candidate_sha,
        "targeted_components": components,
        "deterministic_file_verification": verification,
        "full_validation_passed": False,
        "draft_pr_published": False,
        "production_closed": False,
    }
    _write_object(plan_path, plan)
    task = _open_task(task_run_path)
    task.checkpoint(
        status="VALIDATING",
        phase="STAGE3_CANDIDATE_PREPARED",
        workspace_fingerprint=_diff_fingerprint(workspace),
        evidence_refs=[str(result_path), str(patch_path), str(plan_path)],
        metadata={
            "stage": 3,
            "candidate_sha": candidate_sha,
            "targeted_components": components,
        },
    )
    return plan


def _component_command(component: str, workspace: Path) -> tuple[list[str], Path]:
    python_test_args = [
        "-B",
        "-m",
        "pytest",
        "-q",
        "-ra",
        "-p",
        "no:cacheprovider",
        "-m",
        "not integration and not preprod",
        "tests",
    ]
    if component == "agent-python":
        return (
            [str(workspace / "services/agent-service/.venv/bin/python"), *python_test_args],
            workspace / "services/agent-service",
        )
    if component == "business-python":
        return (
            [str(workspace / "services/business-service/.venv/bin/python"), *python_test_args],
            workspace / "services/business-service",
        )
    if component == "agent-frontend":
        return (["npm", "test"], workspace / "services/agent-service/frontend")
    if component == "web-node":
        return (["npm", "test"], workspace / "web")
    raise Stage3Error(f"unknown targeted component: {component}")


def _targeted_env(component: str) -> dict[str, str]:
    env = os.environ.copy()
    if component not in {"agent-python", "business-python"}:
        return env
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if component == "agent-python":
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = "src:." + (os.pathsep + existing if existing else "")
    return env


def run_targeted(*, workspace: Path, plan_path: Path, output_path: Path) -> int:
    workspace = workspace.resolve()
    plan = _load_object(plan_path)
    if plan.get("schema") != STAGE3_SCHEMA or plan.get("status") != "CANDIDATE_PREPARED":
        raise Stage3Error("invalid Stage-3 targeted validation plan")
    if _git(workspace, "rev-parse", "HEAD") != str(plan.get("candidate_sha") or ""):
        raise Stage3Error("targeted validation checkout drifted from candidate SHA")
    rows: list[dict[str, Any]] = []
    passed = True
    for component in plan.get("targeted_components") or []:
        component_name = str(component)
        command, cwd = _component_command(component_name, workspace)
        if not cwd.is_dir():
            raise Stage3Error(f"targeted component directory is missing: {cwd}")
        try:
            completed = _run(
                command,
                cwd=cwd,
                timeout=3600,
                env=_targeted_env(component_name),
            )
            row = {
                "component": component,
                "command": command,
                "cwd": str(cwd.relative_to(workspace)),
                "exit_code": completed.returncode,
                "stdout": completed.stdout[-MAX_OUTPUT_CHARS:],
                "stderr": completed.stderr[-MAX_OUTPUT_CHARS:],
                "passed": completed.returncode == 0,
            }
        except subprocess.TimeoutExpired as exc:
            row = {
                "component": component,
                "command": command,
                "cwd": str(cwd.relative_to(workspace)),
                "exit_code": None,
                "stdout": str(exc.stdout or "")[-MAX_OUTPUT_CHARS:],
                "stderr": str(exc.stderr or "")[-MAX_OUTPUT_CHARS:],
                "passed": False,
                "timed_out": True,
            }
        rows.append(row)
        if not row["passed"]:
            passed = False
            break
    payload = {
        "schema": STAGE3_SCHEMA,
        "status": "TARGETED_VALIDATION_PASSED" if passed else "TARGETED_VALIDATION_FAILED",
        "candidate_sha": plan.get("candidate_sha"),
        "results": rows,
        "full_validation_passed": False,
        "draft_pr_published": False,
        "production_closed": False,
    }
    _write_object(output_path, payload)
    return 0 if passed else 2


def validate_quick_evidence(summary_path: Path) -> dict[str, Any]:
    summary = _load_object(summary_path)
    if summary.get("mode") != "quick":
        raise Stage3Error("Stage-3 full validation evidence is not Quick mode")
    if summary.get("run_kind") != "verification":
        raise Stage3Error("Stage-3 Quick evidence is not a verification run")
    if summary.get("decision") != "PASS":
        raise Stage3Error("Stage-3 Quick quality decision did not pass")
    if summary.get("loop_status") != "CI_VERIFIED":
        raise Stage3Error("Stage-3 Quick quality loop did not reach CI_VERIFIED")
    if summary.get("completion_eligible") is not True:
        raise Stage3Error("Stage-3 Quick evidence is not completion eligible")
    statuses = {
        str(row.get("id")): str(row.get("status"))
        for row in summary.get("results") or []
        if isinstance(row, dict)
    }
    missing_or_failed = [
        str(gate)
        for gate in summary.get("required_gate_ids") or []
        if statuses.get(str(gate)) != "PASS"
    ]
    if missing_or_failed:
        raise Stage3Error(f"required Quick gates did not pass: {missing_or_failed}")
    return summary


def record_validation(
    *,
    workspace: Path,
    plan_path: Path,
    targeted_result_path: Path,
    quick_summary_path: Path,
    task_run_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    plan = _load_object(plan_path)
    targeted = _load_object(targeted_result_path)
    if targeted.get("status") != "TARGETED_VALIDATION_PASSED":
        raise Stage3Error("targeted validation did not pass")
    if targeted.get("candidate_sha") != plan.get("candidate_sha"):
        raise Stage3Error("targeted evidence candidate SHA mismatch")
    summary = validate_quick_evidence(quick_summary_path)
    workspace = workspace.resolve()
    candidate_sha = _git(workspace, "rev-parse", "HEAD")
    if candidate_sha != str(plan.get("candidate_sha") or ""):
        raise Stage3Error("Quick validation checkout drifted from candidate SHA")
    snapshot = str(summary.get("workspace_snapshot_fingerprint") or "")
    if not snapshot:
        raise Stage3Error("Quick evidence lacks workspace snapshot fingerprint")

    task = _open_task(task_run_path)
    task.mark_condition(
        "validation_passed",
        evidence_refs=[
            str(targeted_result_path), str(quick_summary_path),
            f"candidate-sha:{candidate_sha}",
            f"quick-snapshot:{snapshot}",
        ],
    )
    task.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="STAGE3_DRAFT_PR_REQUIRED",
        workspace_fingerprint=snapshot,
        evidence_refs=[str(targeted_result_path), str(quick_summary_path)],
        metadata={"candidate_sha": candidate_sha, "quick_loop_status": "CI_VERIFIED"},
    )
    result = {
        "schema": STAGE3_SCHEMA,
        "status": "VALIDATED_FOR_DRAFT_PR",
        "source_run_id": plan.get("source_run_id"),
        "head_sha": plan.get("head_sha"),
        "candidate_sha": candidate_sha,
        "repair_branch": plan.get("repair_branch"),
        "repair_base_branch": plan.get("repair_base_branch"),
        "changed_paths": plan.get("changed_paths"),
        "targeted_components": plan.get("targeted_components"),
        "targeted_validation_passed": True,
        "full_validation_passed": True,
        "quick_loop_status": "CI_VERIFIED",
        "quick_workspace_snapshot_fingerprint": snapshot,
        "draft_pr_published": False,
        "production_closed": False,
    }
    _write_object(output_path, result)
    return result


def complete_publication(
    *,
    workspace: Path,
    validation_result_path: Path,
    task_run_path: Path,
    pr_url: str,
    output_path: Path,
) -> dict[str, Any]:
    result = _load_object(validation_result_path)
    if result.get("status") != "VALIDATED_FOR_DRAFT_PR":
        raise Stage3Error("Stage-3 validation result is not publishable")
    if result.get("full_validation_passed") is not True:
        raise Stage3Error("full validation did not pass")
    if not pr_url.startswith("https://github.com/") or "/pull/" not in pr_url:
        raise Stage3Error("a valid GitHub Draft PR URL is required")
    task = _open_task(task_run_path)
    task.mark_condition(
        "draft_pr_published",
        evidence_refs=[pr_url, f"candidate-sha:{result.get('candidate_sha')}"],
    )
    task.complete(
        workspace_fingerprint=str(result.get("quick_workspace_snapshot_fingerprint") or ""),
        evidence_refs=[str(validation_result_path), pr_url],
    )
    completed = dict(result)
    completed.update(
        {
            "status": "DRAFT_REPAIR_PR_PUBLISHED",
            "draft_pr_published": True,
            "draft_pr_url": pr_url,
            "normal_quality_dispatch_requested": True,
            "production_closed": False,
        }
    )
    _write_object(output_path, completed)
    return completed


def _github_output(path: Path | None, values: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = "true" if value is True else "false" if value is False else str(value)
            if "\n" in text:
                raise Stage3Error(f"multiline GitHub output is not allowed: {key}")
            handle.write(f"{key}={text}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("--result", required=True)
    inspect_parser.add_argument("--task-run", required=True)
    inspect_parser.add_argument("--patch", required=True)
    inspect_parser.add_argument("--output", required=True)
    inspect_parser.add_argument("--github-output")

    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--workspace", required=True)
    prepare_parser.add_argument("--result", required=True)
    prepare_parser.add_argument("--task-run", required=True)
    prepare_parser.add_argument("--patch", required=True)
    prepare_parser.add_argument("--plan", required=True)
    prepare_parser.add_argument("--github-output")

    targeted_parser = sub.add_parser("targeted")
    targeted_parser.add_argument("--workspace", required=True)
    targeted_parser.add_argument("--plan", required=True)
    targeted_parser.add_argument("--output", required=True)

    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--workspace", required=True)
    validate_parser.add_argument("--plan", required=True)
    validate_parser.add_argument("--targeted-result", required=True)
    validate_parser.add_argument("--quick-summary", required=True)
    validate_parser.add_argument("--task-run", required=True)
    validate_parser.add_argument("--output", required=True)
    validate_parser.add_argument("--github-output")

    complete_parser = sub.add_parser("complete")
    complete_parser.add_argument("--workspace", required=True)
    complete_parser.add_argument("--validation-result", required=True)
    complete_parser.add_argument("--task-run", required=True)
    complete_parser.add_argument("--pr-url", required=True)
    complete_parser.add_argument("--output", required=True)

    args = parser.parse_args()
    try:
        if args.command == "inspect":
            payload = inspect_handoff(
                result_path=Path(args.result),
                task_run_path=Path(args.task_run),
                patch_path=Path(args.patch),
            )
            _write_object(Path(args.output), {"schema": STAGE3_SCHEMA, **payload})
            _github_output(Path(args.github_output) if args.github_output else None, payload)
            return 0
        if args.command == "prepare":
            payload = prepare_candidate(
                workspace=Path(args.workspace),
                result_path=Path(args.result),
                task_run_path=Path(args.task_run),
                patch_path=Path(args.patch),
                plan_path=Path(args.plan),
            )
            _github_output(
                Path(args.github_output) if args.github_output else None,
                {
                    "candidate_sha": payload["candidate_sha"],
                    "source_run_id": payload["source_run_id"],
                    "repair_branch": payload["repair_branch"],
                    "repair_base_branch": payload["repair_base_branch"],
                },
            )
            return 0
        if args.command == "targeted":
            return run_targeted(
                workspace=Path(args.workspace),
                plan_path=Path(args.plan),
                output_path=Path(args.output),
            )
        if args.command == "validate":
            payload = record_validation(
                workspace=Path(args.workspace),
                plan_path=Path(args.plan),
                targeted_result_path=Path(args.targeted_result),
                quick_summary_path=Path(args.quick_summary),
                task_run_path=Path(args.task_run),
                output_path=Path(args.output),
            )
            _github_output(
                Path(args.github_output) if args.github_output else None,
                {
                    "candidate_sha": payload["candidate_sha"],
                    "repair_branch": payload["repair_branch"],
                    "repair_base_branch": payload["repair_base_branch"],
                    "source_run_id": payload["source_run_id"],
                },
            )
            return 0
        if args.command == "complete":
            complete_publication(
                workspace=Path(args.workspace),
                validation_result_path=Path(args.validation_result),
                task_run_path=Path(args.task_run),
                pr_url=args.pr_url,
                output_path=Path(args.output),
            )
            return 0
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError, Stage3Error) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc), "production_closed": False}), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())