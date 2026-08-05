#!/usr/bin/env python3
"""Run a bounded model-repair/independent-validation loop for one GitHub failure."""
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


def _git_snapshot(workspace: Path) -> str:
    parts: list[bytes] = []
    for command in (["git", "status", "--porcelain=v1"], ["git", "diff", "--binary", "--"]):
        completed = subprocess.run(command, cwd=workspace, capture_output=True, check=False)
        if completed.returncode:
            raise RuntimeError(f"git snapshot command failed: {' '.join(command)}")
        parts.append(completed.stdout)
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


def _failure_signature(stdout: str, stderr: str) -> str:
    text = (stdout + "\n" + stderr)[-20000:]
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-root", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--failure-case", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--max-cycles", type=int, default=8)
    parser.add_argument("--github-output")
    parser.add_argument("--validation-command", nargs=argparse.REMAINDER, required=True)
    args = parser.parse_args()

    control = Path(args.control_root).resolve()
    workspace = Path(args.workspace).resolve()
    failure_path = Path(args.failure_case).resolve()
    task_path = Path(args.task_run).resolve()
    evidence_root = Path(args.evidence_root).resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(control / "skill-system" / "controller"))
    from task_run import PrematureCompletionError, TaskRunStore  # type: ignore

    payload = json.loads(task_path.read_text(encoding="utf-8"))
    task = TaskRunStore(task_path, payload)
    if task.payload.get("status") == "COMPLETED":
        return 0
    if not args.validation_command:
        raise SystemExit("validation command is required")

    last_validation_signature = ""
    repeated_failure_count = 0
    for cycle in range(1, min(max(args.max_cycles, 1), 8) + 1):
        cycle_dir = evidence_root / f"cycle-{cycle:02d}"
        cycle_dir.mkdir(parents=True, exist_ok=True)
        before = _git_snapshot(workspace)
        task.checkpoint(
            status="REPAIRING",
            phase="MODEL_FIXER_RUNNING",
            workspace_fingerprint=before,
            evidence_refs=[str(failure_path)],
            metadata={"cycle": cycle},
        )
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
                code="MODEL_FIXER_FAILED",
                reason=f"restricted fixer exited with {fixed.returncode}",
                attempted_strategies=("openai-compatible-restricted-fixer",),
                next_action="inspect the repair evidence; do not weaken the judge or expand scope implicitly",
                workspace_fingerprint=_git_snapshot(workspace),
                evidence_refs=[str(fix_result), str(cycle_dir / "fixer.stderr.txt")],
            )
            return fixed.returncode
        after = _git_snapshot(workspace)
        if before == after:
            task.block(
                code="FIXER_NO_SOURCE_CHANGE",
                reason="restricted fixer produced no governed source change",
                attempted_strategies=("openai-compatible-restricted-fixer",),
                next_action="change the repair plan or candidate path scope",
                workspace_fingerprint=after,
                evidence_refs=[str(fix_result)],
            )
            return 4
        task.mark_condition("source_changed", evidence_refs=[str(fix_result), f"git-snapshot:{after}"])
        task.checkpoint(
            status="VALIDATING",
            phase="INDEPENDENT_QUICK_VALIDATION",
            workspace_fingerprint=after,
            evidence_refs=[str(fix_result)],
            metadata={"cycle": cycle},
        )

        validation_dir = cycle_dir / "validation"
        validation_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        for key in tuple(env):
            if key.startswith("GOVERNED_REPAIR_MODEL_"):
                env.pop(key, None)
        env["QUALITY_EVIDENCE_DIR"] = str(validation_dir)
        validated = subprocess.run(
            args.validation_command,
            cwd=workspace,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        (cycle_dir / "validation.stdout.txt").write_text(validated.stdout, encoding="utf-8")
        (cycle_dir / "validation.stderr.txt").write_text(validated.stderr, encoding="utf-8")
        if validated.returncode == 0:
            task.mark_condition(
                "validation_passed",
                evidence_refs=[str(cycle_dir / "validation.stdout.txt"), str(validation_dir)],
            )
            task.checkpoint(
                status="VALIDATING",
                phase="REPAIR_VALIDATED",
                workspace_fingerprint=_git_snapshot(workspace),
                evidence_refs=[str(validation_dir), str(fix_result)],
                metadata={"cycle": cycle, "next_action": "publish Draft PR"},
            )
            result = {
                "schema": "github-repair-orchestrator@1",
                "status": "READY_FOR_DRAFT_PR",
                "cycle": cycle,
                "task_run": str(task_path),
                "production_closed": False,
            }
            (evidence_root / "repair-result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if args.github_output:
                with Path(args.github_output).open("a", encoding="utf-8") as handle:
                    handle.write("repair_ready=true\n")
                    handle.write(f"repair_cycle={cycle}\n")
            return 0

        signature = _failure_signature(validated.stdout, validated.stderr)
        repeated_failure_count = repeated_failure_count + 1 if signature == last_validation_signature else 1
        last_validation_signature = signature
        failure = _load(failure_path)
        failure["latest_validation"] = {
            "cycle": cycle,
            "returncode": validated.returncode,
            "failure_signature": signature,
            "stdout_tail": validated.stdout[-7000:],
            "stderr_tail": validated.stderr[-7000:],
        }
        failure_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        task.checkpoint(
            status="FAILED_RECOVERABLE",
            phase="VALIDATION_FAILED",
            workspace_fingerprint=_git_snapshot(workspace),
            evidence_refs=[str(cycle_dir / "validation.stderr.txt"), str(failure_path)],
            metadata={"cycle": cycle, "failure_signature": signature},
        )
        if repeated_failure_count >= 2:
            task.block(
                code="REPEATED_FAILURE_NO_PROGRESS",
                reason="the same validation failure signature occurred twice",
                attempted_strategies=("bounded-model-repair",),
                next_action="require a new root-cause proof or human review before resuming",
                workspace_fingerprint=_git_snapshot(workspace),
                evidence_refs=[str(failure_path)],
            )
            return 8

    task.block(
        code="REPAIR_CYCLE_BUDGET_EXHAUSTED",
        reason="the eight-cycle repair budget was exhausted without independent validation",
        attempted_strategies=("bounded-model-repair",),
        next_action="review evidence and create a new governed repair target",
        workspace_fingerprint=_git_snapshot(workspace),
        evidence_refs=[str(evidence_root)],
    )
    return 8


if __name__ == "__main__":
    raise SystemExit(main())
