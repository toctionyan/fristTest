#!/usr/bin/env python3
"""Bounded Stage-2 repair controller for ingested GitHub CI failures.

Stage 2 may create a local repair candidate and evidence artifact. It never pushes a
branch, opens a pull request, merges protected refs, or claims full validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPTS = Path(__file__).resolve().parent
for entry in (str(CONTROL), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from task_run import TaskRunStore  # type: ignore  # noqa: E402
from github_agent_fixer import (  # noqa: E402
    FixerError,
    ModelConfig,
    fingerprint,
    repair_round,
    validate_allowed_paths,
)

MAX_CYCLES = 8


class OrchestratorError(RuntimeError):
    """Fail-closed Stage-2 orchestration error."""


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise OrchestratorError(f"JSON object required: {path}")
    return payload


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False, timeout=120)


def _git(workspace: Path, *args: str) -> str:
    completed = _run(["git", *args], workspace)
    if completed.returncode:
        raise OrchestratorError((completed.stderr or completed.stdout or "git command failed").strip())
    return completed.stdout.strip()


def _changed_paths(workspace: Path) -> tuple[str, ...]:
    completed = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], workspace)
    if completed.returncode:
        raise OrchestratorError((completed.stderr or completed.stdout or "git status failed").strip())
    rows = completed.stdout.splitlines()
    result: list[str] = []
    for row in rows:
        raw = row[3:] if len(row) > 3 else ""
        path = raw.split(" -> ")[-1].strip().replace("\\", "/")
        if path and path not in result:
            result.append(path)
    return tuple(result)


def _workspace_diff_fingerprint(workspace: Path) -> str:
    patch = _git(workspace, "diff", "--no-ext-diff", "--binary")
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()


def _validate_failure_case(report: dict[str, Any], workspace: Path) -> tuple[str, ...]:
    if report.get("schema") != "github-failure-ingest@1" or report.get("status") != "INGESTED":
        raise OrchestratorError("unsupported or incomplete failure-case evidence")
    if report.get("repair_allowed") is not True:
        raise OrchestratorError("Stage 1 did not authorize a repair candidate")
    if report.get("classification") != "code_or_contract":
        raise OrchestratorError("only code_or_contract failures are repairable")
    if report.get("same_repository") is not True:
        raise OrchestratorError("fork or foreign-repository failures are not repairable")
    expected_sha = str(report.get("head_sha") or "")
    actual_sha = _git(workspace, "rev-parse", "HEAD")
    if not expected_sha or expected_sha != actual_sha:
        raise OrchestratorError("candidate checkout does not match the ingested commit SHA")
    initial = _changed_paths(workspace)
    if initial:
        raise OrchestratorError(f"candidate workspace must start clean: {list(initial)}")
    paths = tuple(str(item) for item in report.get("candidate_paths") or [])
    if not paths:
        raise OrchestratorError("failure evidence does not identify a repair path")
    return validate_allowed_paths(workspace, paths)


def _validate_task_binding(task: TaskRunStore, report: dict[str, Any]) -> None:
    binding = task.payload.get("binding") if isinstance(task.payload.get("binding"), dict) else {}
    expected = {
        "repository": report.get("repository"),
        "workflow_name": report.get("workflow_name"),
        "workflow_run_id": str(report.get("workflow_run_id")),
        "workflow_run_attempt": str(report.get("workflow_run_attempt")),
        "head_sha": report.get("head_sha"),
        "failure_signature": report.get("failure_signature"),
    }
    mismatched = [key for key, value in expected.items() if str(binding.get(key)) != str(value)]
    if mismatched:
        raise OrchestratorError(f"Stage-1 TaskRun binding mismatch: {mismatched}")


def _open_task(path: Path) -> TaskRunStore:
    payload = _load_object(path)
    return TaskRunStore(path.resolve(), payload)


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _block(
    task: TaskRunStore,
    *,
    workspace: Path,
    code: str,
    reason: str,
    evidence_refs: list[str],
    result_path: Path,
    cycles: list[dict[str, Any]],
) -> int:
    result = {
        "schema": "github-governed-repair-stage2@1",
        "status": "BLOCKED",
        "code": code,
        "reason": reason,
        "cycles": cycles,
        "changed_paths": list(_changed_paths(workspace)),
        "full_validation_passed": False,
        "draft_pr_published": False,
        "production_closed": False,
    }
    _write_result(result_path, result)
    task.block(
        code=code,
        reason=reason,
        attempted_strategies=("restricted-model-fixer", "deterministic-file-verifier"),
        next_action="inspect Stage-2 evidence; do not publish this candidate",
        workspace_fingerprint=_workspace_diff_fingerprint(workspace),
        evidence_refs=[*evidence_refs, str(result_path)],
    )
    return 2


def _canonical_diagnostics(row: dict[str, Any]) -> str:
    return json.dumps(
        {"summary": row.get("summary"), "verification": row.get("verification")},
        ensure_ascii=False,
        sort_keys=True,
    )[:16_000]


def run_stage2(
    *,
    workspace: Path,
    failure_case_path: Path,
    task_run_path: Path,
    evidence_root: Path,
    max_cycles: int,
    config: ModelConfig | None = None,
) -> int:
    workspace = workspace.resolve()
    evidence_root = evidence_root.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    result_path = evidence_root / "repair-result.json"
    patch_path = evidence_root / "repair.patch"
    report = _load_object(failure_case_path)
    task = _open_task(task_run_path)
    cycles: list[dict[str, Any]] = []

    try:
        _validate_task_binding(task, report)
        allowed_paths = _validate_failure_case(report, workspace)
        config = config or ModelConfig.from_environment()
    except (OSError, json.JSONDecodeError, OrchestratorError, FixerError) as exc:
        return _block(
            task,
            workspace=workspace,
            code="STAGE2_PREFLIGHT_REJECTED",
            reason=str(exc),
            evidence_refs=[str(failure_case_path), str(task_run_path)],
            result_path=result_path,
            cycles=cycles,
        )

    task.checkpoint(
        status="RUNNING",
        phase="STAGE2_PREFLIGHT_PASSED",
        workspace_fingerprint=_workspace_diff_fingerprint(workspace),
        evidence_refs=[str(failure_case_path)],
        metadata={
            "stage": 2,
            "max_cycles": max_cycles,
            "candidate_paths": list(allowed_paths),
            "provider": config.provider,
            "model": config.model,
        },
    )

    diagnostics = str(report.get("failure_summary") or "")
    previous_verifier_signature = ""
    repeated_verifier_count = 0
    before_fingerprint = _workspace_diff_fingerprint(workspace)

    for cycle in range(1, min(max_cycles, MAX_CYCLES) + 1):
        task.checkpoint(
            status="REPAIRING",
            phase="STAGE2_FIXER_RUNNING",
            workspace_fingerprint=_workspace_diff_fingerprint(workspace),
            evidence_refs=[str(failure_case_path)],
            metadata={"cycle": cycle},
        )
        try:
            row = repair_round(
                workspace=workspace,
                failure_case=report,
                allowed_paths=allowed_paths,
                diagnostics=diagnostics,
                cycle=cycle,
                config=config,
            )
        except (FixerError, OSError, subprocess.SubprocessError) as exc:
            return _block(
                task,
                workspace=workspace,
                code="STAGE2_FIXER_FAILED",
                reason=str(exc),
                evidence_refs=[str(failure_case_path)],
                result_path=result_path,
                cycles=cycles,
            )

        current_paths = _changed_paths(workspace)
        unexpected = sorted(set(current_paths) - set(allowed_paths))
        if unexpected:
            return _block(
                task,
                workspace=workspace,
                code="STAGE2_SCOPE_VIOLATION",
                reason=f"repair changed paths outside immutable evidence scope: {unexpected}",
                evidence_refs=[str(failure_case_path)],
                result_path=result_path,
                cycles=[*cycles, row],
            )
        current_fingerprint = _workspace_diff_fingerprint(workspace)
        if current_fingerprint == before_fingerprint:
            return _block(
                task,
                workspace=workspace,
                code="STAGE2_NO_PROGRESS",
                reason="repair cycle produced no new governed source diff",
                evidence_refs=[str(failure_case_path)],
                result_path=result_path,
                cycles=[*cycles, row],
            )
        before_fingerprint = current_fingerprint
        cycles.append(row)
        cycle_file = evidence_root / f"cycle-{cycle:02d}.json"
        _write_result(cycle_file, row)
        task.checkpoint(
            status="VALIDATING",
            phase="STAGE2_DETERMINISTIC_VALIDATION",
            workspace_fingerprint=current_fingerprint,
            evidence_refs=[str(cycle_file)],
            metadata={"cycle": cycle, "verification_passed": row.get("verification_passed") is True},
        )

        if row.get("verification_passed") is True:
            patch = _git(workspace, "diff", "--no-ext-diff", "--binary")
            if not patch.strip():
                return _block(
                    task,
                    workspace=workspace,
                    code="STAGE2_EMPTY_PATCH",
                    reason="verification passed without a publishable source diff",
                    evidence_refs=[str(cycle_file)],
                    result_path=result_path,
                    cycles=cycles,
                )
            patch_path.write_text(patch + ("\n" if not patch.endswith("\n") else ""), encoding="utf-8")
            task.mark_condition(
                "source_changed",
                evidence_refs=[str(patch_path), f"repair-diff:{current_fingerprint}"],
            )
            task.checkpoint(
                status="WAITING_EXTERNAL_RESULT",
                phase="STAGE3_VALIDATION_REQUIRED",
                workspace_fingerprint=current_fingerprint,
                evidence_refs=[str(patch_path), str(cycle_file)],
                metadata={
                    "stage2_status": "REPAIR_CANDIDATE_READY",
                    "changed_paths": list(current_paths),
                    "next_action": "run independent targeted and full regression validation",
                },
            )
            result = {
                "schema": "github-governed-repair-stage2@1",
                "status": "REPAIR_CANDIDATE_READY",
                "workflow_run_id": report.get("workflow_run_id"),
                "head_sha": report.get("head_sha"),
                "failure_signature": report.get("failure_signature"),
                "cycles": cycles,
                "changed_paths": list(current_paths),
                "patch": str(patch_path),
                "deterministic_file_verification_passed": True,
                "full_validation_passed": False,
                "draft_pr_published": False,
                "production_closed": False,
            }
            _write_result(result_path, result)
            return 0

        verifier_signature = fingerprint(row.get("verification") or [])
        if verifier_signature == previous_verifier_signature:
            repeated_verifier_count += 1
        else:
            repeated_verifier_count = 1
            previous_verifier_signature = verifier_signature
        if repeated_verifier_count >= 2:
            return _block(
                task,
                workspace=workspace,
                code="STAGE2_REPEATED_FAILURE",
                reason="the same deterministic verification failure repeated twice",
                evidence_refs=[str(cycle_file)],
                result_path=result_path,
                cycles=cycles,
            )
        diagnostics = _canonical_diagnostics(row)

    return _block(
        task,
        workspace=workspace,
        code="STAGE2_CYCLE_BUDGET_EXHAUSTED",
        reason=f"repair did not reach a deterministic candidate within {min(max_cycles, MAX_CYCLES)} cycles",
        evidence_refs=[str(failure_case_path)],
        result_path=result_path,
        cycles=cycles,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--failure-case", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--max-cycles", type=int, default=8)
    args = parser.parse_args()
    if args.max_cycles < 1 or args.max_cycles > MAX_CYCLES:
        parser.error(f"--max-cycles must be between 1 and {MAX_CYCLES}")
    return run_stage2(
        workspace=Path(args.workspace),
        failure_case_path=Path(args.failure_case).resolve(),
        task_run_path=Path(args.task_run).resolve(),
        evidence_root=Path(args.evidence_root),
        max_cycles=args.max_cycles,
    )


if __name__ == "__main__":
    raise SystemExit(main())
