#!/usr/bin/env python3
"""Finalize a governed GitHub repair TaskRun after a Draft PR is published."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-root", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--pr-url", required=True)
    args = parser.parse_args()

    control = Path(args.control_root).resolve()
    workspace = Path(args.workspace).resolve()
    task_path = Path(args.task_run).resolve()
    sys.path.insert(0, str(control / "skill-system" / "controller"))
    from task_run import PrematureCompletionError, TaskRunStore  # type: ignore

    payload = json.loads(task_path.read_text(encoding="utf-8"))
    task = TaskRunStore(task_path, payload)
    snapshot = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True, capture_output=True, check=True
    ).stdout.strip()
    task.mark_condition("draft_pr_published", evidence_refs=[args.pr_url, f"commit:{snapshot}"])
    try:
        task.complete(workspace_fingerprint=snapshot, evidence_refs=[args.pr_url, f"commit:{snapshot}"])
    except PrematureCompletionError as exc:
        print(str(exc), file=sys.stderr)
        return 9
    print(json.dumps({"status": "COMPLETED", "draft_pr": args.pr_url, "production_closed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
