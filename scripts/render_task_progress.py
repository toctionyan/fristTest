#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from execution_progress import build_execution_progress, render_progress_text  # type: ignore  # noqa: E402
from task_execution_ledger import projection_inputs  # type: ignore  # noqa: E402


def _load(path: Path | None, *, default: Any) -> Any:
    if path is None:
        return default
    value = json.loads(path.read_text(encoding="utf-8"))
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render one read-only whole-task execution projection from durable TaskRun evidence."
    )
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--github-jobs")
    parser.add_argument("--github-steps")
    parser.add_argument("--quality-results")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--workflow")
    parser.add_argument("--head-sha")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output")
    args = parser.parse_args()

    task = _load(Path(args.task_run), default={})
    if not isinstance(task, dict):
        raise SystemExit("TaskRun must be a JSON object")
    planned, attempts = projection_inputs(task)

    github_jobs = _load(Path(args.github_jobs), default=[]) if args.github_jobs else []
    github_steps = _load(Path(args.github_steps), default=[]) if args.github_steps else []
    quality_results = _load(Path(args.quality_results), default=[]) if args.quality_results else []
    for name, value in (
        ("github jobs", github_jobs),
        ("github steps", github_steps),
        ("quality results", quality_results),
    ):
        if not isinstance(value, list):
            raise SystemExit(f"{name} must be a JSON array")

    progress = build_execution_progress(
        task=task,
        github_jobs=[row for row in github_jobs if isinstance(row, dict)],
        github_steps=[row for row in github_steps if isinstance(row, dict)],
        github_step_job_name="github",
        quality_results=[row for row in quality_results if isinstance(row, dict)],
        planned_stages=planned,
        attempt_history=attempts,
        run_id=args.run_id,
        workflow=args.workflow,
        head_sha=args.head_sha,
    )
    rendered = (
        json.dumps(progress, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else render_progress_text(progress) + "\n"
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
