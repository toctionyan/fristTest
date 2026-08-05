#!/usr/bin/env python3
"""Record isolated validation evidence against one governed repair TaskRun."""
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
    parser.add_argument("--result", choices=("success", "failure"), required=True)
    parser.add_argument("--validation-evidence", required=True)
    parser.add_argument("--pr-url", default="")
    parser.add_argument("--failure-signature", default="")
    args = parser.parse_args()

    control = Path(args.control_root).resolve()
    workspace = Path(args.workspace).resolve()
    task_path = Path(args.task_run).resolve()
    evidence = Path(args.validation_evidence).resolve()
    sys.path.insert(0, str(control / "skill-system" / "controller"))
    from task_run import PrematureCompletionError, TaskRunStore  # type: ignore

    payload = json.loads(task_path.read_text(encoding="utf-8"))
    task = TaskRunStore(task_path, payload)
    snapshot = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True, capture_output=True, check=True
    ).stdout.strip()

    if args.result == "failure":
        task.set_metadata(
            validation_pending=False,
            last_validation_failure_signature=args.failure_signature,
            last_validation_evidence=str(evidence),
        )
        task.checkpoint(
            status="FAILED_RECOVERABLE",
            phase="ISOLATED_VALIDATION_FAILED",
            workspace_fingerprint=snapshot,
            evidence_refs=[str(evidence), f"commit:{snapshot}"],
            metadata={"failure_signature": args.failure_signature},
        )
        print(json.dumps({"status": "FAILED_RECOVERABLE", "production_closed": False}))
        return 0

    task.checkpoint(
        status="VALIDATING",
        phase="ISOLATED_VALIDATION_PASSED",
        workspace_fingerprint=snapshot,
        evidence_refs=[str(evidence), f"commit:{snapshot}"],
        metadata={"validation": "quick-plus-deterministic-integration"},
    )
    task.mark_condition(
        "validation_passed",
        evidence_refs=[str(evidence), f"commit:{snapshot}"],
    )
    if not args.pr_url:
        print("successful validation requires the Draft PR URL", file=sys.stderr)
        return 9
    task.mark_condition("draft_pr_published", evidence_refs=[args.pr_url, f"commit:{snapshot}"])
    task.set_metadata(validation_pending=False, validated_commit=snapshot, draft_pr=args.pr_url)
    try:
        task.complete(
            workspace_fingerprint=snapshot,
            evidence_refs=[args.pr_url, str(evidence), f"commit:{snapshot}"],
        )
    except PrematureCompletionError as exc:
        print(str(exc), file=sys.stderr)
        return 9
    print(
        json.dumps(
            {
                "status": "COMPLETED",
                "draft_pr": args.pr_url,
                "validated_commit": snapshot,
                "production_closed": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
