#!/usr/bin/env python3
"""Apply one secret-bearing governed repair cycle, then stop for external validation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _run_git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=check,
    )


def _git_head(workspace: Path) -> str:
    return _run_git(workspace, "rev-parse", "HEAD").stdout.strip()


def _git_status(workspace: Path) -> list[str]:
    return [
        line
        for line in _run_git(workspace, "status", "--porcelain=v1", "--untracked-files=all").stdout.splitlines()
        if line.strip()
    ]


def _git_snapshot(workspace: Path) -> str:
    parts: list[bytes] = []
    for command in (
        ["git", "rev-parse", "HEAD"],
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        ["git", "diff", "--binary", "--"],
        ["git", "diff", "--cached", "--binary", "--"],
    ):
        completed = subprocess.run(command, cwd=workspace, capture_output=True, check=False)
        if completed.returncode:
            raise RuntimeError(f"git snapshot command failed: {' '.join(command)}")
        parts.append(completed.stdout)
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


def _commit_repair_cycle(
    workspace: Path,
    *,
    cycle: int,
    task_id: str,
    origin_sha: str,
    source_run_id: str,
    failure_signature: str,
    allowed_paths: list[str],
) -> str:
    _run_git(workspace, "add", "--", *allowed_paths)
    staged = [
        line.strip()
        for line in _run_git(workspace, "diff", "--cached", "--name-only", "--").stdout.splitlines()
        if line.strip()
    ]
    if not staged:
        raise RuntimeError("repair patch produced no staged source change")
    outside = sorted(set(staged) - set(allowed_paths))
    if outside:
        raise RuntimeError("repair staging escaped the frozen scope: " + ", ".join(outside))
    message = (
        f"Governed repair cycle {cycle} for workflow run {source_run_id}\n\n"
        f"Governed-Repair-Task: {task_id}\n"
        f"Governed-Repair-Origin: {origin_sha}\n"
        f"Governed-Repair-Cycle: {cycle}\n"
        f"Governed-Failure-Signature: {failure_signature}\n"
        f"Governed-Source-Run: {source_run_id}"
    )
    _run_git(workspace, "commit", "-m", message)
    return _git_head(workspace)


def _write_output(path: Path | None, values: dict[str, Any]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = "true" if value is True else "false" if value is False else str(value)
            handle.write(f"{key}={text}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-root", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--failure-case", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--max-cycles", type=int, default=8)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    control = Path(args.control_root).resolve()
    workspace = Path(args.workspace).resolve()
    failure_path = Path(args.failure_case).resolve()
    task_path = Path(args.task_run).resolve()
    evidence_root = Path(args.evidence_root).resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(control / "skill-system" / "controller"))
    from task_run import TaskRunStore  # type: ignore

    failure = _load(failure_path)
    task = TaskRunStore(task_path, _load(task_path))
    if task.payload.get("status") == "COMPLETED":
        return 0
    if task.payload.get("status") == "BLOCKED":
        print("governed repair TaskRun is blocked and requires an explicit new target", file=sys.stderr)
        return 8

    allowed_paths = [str(item) for item in failure.get("candidate_paths") or []]
    if not allowed_paths:
        raise SystemExit("failure case has no frozen candidate paths")
    metadata = task.payload.get("metadata") if isinstance(task.payload.get("metadata"), dict) else {}
    cycle = int(metadata.get("repair_cycle") or 0) + 1
    maximum = min(max(args.max_cycles, 1), 8)
    if cycle > maximum:
        task.block(
            code="REPAIR_CYCLE_BUDGET_EXHAUSTED",
            reason=f"repair cycle {cycle} exceeds the bounded maximum {maximum}",
            attempted_strategies=("cross-workflow-governed-repair",),
            next_action="review all prior repair and validation evidence before creating a new target",
            workspace_fingerprint=_git_snapshot(workspace),
            evidence_refs=[str(failure_path)],
        )
        return 8

    dirty = _git_status(workspace)
    if dirty:
        task.block(
            code="CANDIDATE_DIRTY_BEFORE_MODEL_CALL",
            reason="the candidate has uncommitted changes before the secret-bearing model call",
            attempted_strategies=("clean-checkout",),
            next_action="inspect checkout or supply-chain drift before resuming",
            workspace_fingerprint=_git_snapshot(workspace),
            evidence_refs=["dirty-paths:" + "|".join(dirty[:40])],
        )
        return 10

    source_run_id = str(failure.get("workflow_run_id") or "unknown")
    failure_signature = str(failure.get("failure_signature") or "")
    binding = task.payload.get("binding") if isinstance(task.payload.get("binding"), dict) else {}
    origin_sha = str(binding.get("origin_sha") or failure.get("head_sha") or _git_head(workspace))
    task_id = str(task.payload.get("task_id") or "")
    before = _git_snapshot(workspace)
    task.checkpoint(
        status="REPAIRING",
        phase="MULTI_ROLE_MODEL_REPAIR",
        workspace_fingerprint=before,
        evidence_refs=[str(failure_path)],
        metadata={"cycle": cycle, "model_roles": 4},
    )

    cycle_dir = evidence_root / f"cycle-{cycle:02d}"
    cycle_dir.mkdir(parents=True, exist_ok=True)
    fix_result = cycle_dir / "fix-result.json"
    fixed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(control / "scripts" / "github_agent_fixer.py"),
            "--workspace",
            str(workspace),
            "--failure-case",
            str(failure_path),
            "--output",
            str(fix_result),
        ],
        cwd=workspace,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )
    (cycle_dir / "fixer.stdout.txt").write_text(fixed.stdout, encoding="utf-8")
    (cycle_dir / "fixer.stderr.txt").write_text(fixed.stderr, encoding="utf-8")
    if fixed.returncode:
        task.block(
            code="MODEL_REPAIR_REJECTED",
            reason=f"multi-role repair exited with {fixed.returncode}",
            attempted_strategies=(
                "failure-explorer",
                "repair-plan-reviewer",
                "restricted-fixer",
                "diff-integrity-reviewer",
            ),
            next_action="inspect reviewer and fixer evidence; do not expand scope or weaken the judge",
            workspace_fingerprint=_git_snapshot(workspace),
            evidence_refs=[str(fix_result), str(cycle_dir / "fixer.stderr.txt")],
        )
        return fixed.returncode
    if before == _git_snapshot(workspace):
        task.block(
            code="FIXER_NO_SOURCE_CHANGE",
            reason="approved multi-role repair produced no governed source change",
            attempted_strategies=("multi-role-model-repair",),
            next_action="require a different root-cause proof before resuming",
            workspace_fingerprint=before,
            evidence_refs=[str(fix_result)],
        )
        return 4

    _run_git(workspace, "config", "user.name", "github-actions[bot]")
    _run_git(
        workspace,
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    try:
        repair_commit = _commit_repair_cycle(
            workspace,
            cycle=cycle,
            task_id=task_id,
            origin_sha=origin_sha,
            source_run_id=source_run_id,
            failure_signature=failure_signature,
            allowed_paths=allowed_paths,
        )
    except RuntimeError as exc:
        task.block(
            code="REPAIR_COMMIT_REJECTED",
            reason=str(exc),
            attempted_strategies=("commit-frozen-scope",),
            next_action="inspect the staged paths and create a new governed target if scope changed",
            workspace_fingerprint=_git_snapshot(workspace),
            evidence_refs=[str(fix_result)],
        )
        return 7

    if _git_status(workspace):
        task.block(
            code="REPAIR_COMMIT_LEFT_DIRTY_TREE",
            reason="the frozen repair commit did not consume the entire approved source diff",
            attempted_strategies=("commit-frozen-scope",),
            next_action="inspect out-of-scope modifications before validation",
            workspace_fingerprint=_git_snapshot(workspace),
            evidence_refs=[f"commit:{repair_commit}", str(fix_result)],
        )
        return 7

    task.mark_condition(
        "source_changed",
        evidence_refs=[str(fix_result), f"commit:{repair_commit}"],
    )
    task.set_metadata(
        repair_cycle=cycle,
        repair_commit=repair_commit,
        last_source_run_id=source_run_id,
        validation_pending=True,
    )
    task.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="VALIDATION_DISPATCH_REQUIRED",
        workspace_fingerprint=repair_commit,
        evidence_refs=[str(fix_result), f"commit:{repair_commit}"],
        metadata={"cycle": cycle, "next_action": "run no-secret Quick and deterministic Integration validation"},
    )
    result = {
        "schema": "github-repair-cycle@1",
        "status": "VALIDATION_REQUIRED",
        "task_id": task_id,
        "cycle": cycle,
        "repair_commit": repair_commit,
        "repair_branch": str(failure.get("repair_branch") or ""),
        "production_closed": False,
    }
    (evidence_root / "repair-cycle-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_output(
        Path(args.github_output).resolve() if args.github_output else None,
        {
            "repair_ready": True,
            "repair_cycle": cycle,
            "repair_commit": repair_commit,
            "task_id": task_id,
        },
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
