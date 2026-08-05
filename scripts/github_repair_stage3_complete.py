#!/usr/bin/env python3
"""Finalize a validated Stage-3 TaskRun after a Draft PR is published."""
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

from task_run import TaskRunStore  # type: ignore  # noqa: E402


class CompletionError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CompletionError(f"JSON object required: {path}")
    return payload


def complete_publication(
    *,
    validation_result_path: Path,
    task_run_path: Path,
    pr_url: str,
    output_path: Path,
) -> dict[str, Any]:
    result = _load(validation_result_path)
    if result.get("schema") != "github-governed-repair-stage3@1":
        raise CompletionError("unsupported Stage-3 validation schema")
    if result.get("status") != "VALIDATED_FOR_DRAFT_PR":
        raise CompletionError("Stage-3 validation result is not publishable")
    if result.get("full_validation_passed") is not True:
        raise CompletionError("full validation did not pass")
    if result.get("draft_pr_published") is not False:
        raise CompletionError("Draft PR publication was already asserted")
    if result.get("production_closed") is not False:
        raise CompletionError("invalid production closure authority")
    if not pr_url.startswith("https://github.com/") or "/pull/" not in pr_url:
        raise CompletionError("a valid GitHub Draft PR URL is required")

    task_payload = _load(task_run_path)
    task = TaskRunStore(task_run_path.resolve(), task_payload)
    if task.payload.get("status") != "WAITING_EXTERNAL_RESULT":
        raise CompletionError("TaskRun is not waiting for Draft PR publication")
    if task.payload.get("phase") != "STAGE3_DRAFT_PR_REQUIRED":
        raise CompletionError("TaskRun phase is not STAGE3_DRAFT_PR_REQUIRED")

    candidate_sha = str(result.get("candidate_sha") or "")
    snapshot = str(result.get("quick_workspace_snapshot_fingerprint") or "")
    if not candidate_sha or not snapshot:
        raise CompletionError("Stage-3 validation evidence lacks candidate identity")

    task.mark_condition(
        "draft_pr_published",
        evidence_refs=[pr_url, f"candidate-sha:{candidate_sha}"],
    )
    task.checkpoint(
        status="VALIDATING",
        phase="STAGE3_PUBLICATION_RECORDED",
        workspace_fingerprint=snapshot,
        evidence_refs=[pr_url, str(validation_result_path)],
        metadata={"draft_pr_url": pr_url, "candidate_sha": candidate_sha},
    )
    task.complete(
        workspace_fingerprint=snapshot,
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(completed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-result", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--pr-url", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        complete_publication(
            validation_result_path=Path(args.validation_result),
            task_run_path=Path(args.task_run),
            pr_url=args.pr_url,
            output_path=Path(args.output),
        )
    except (OSError, json.JSONDecodeError, CompletionError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
