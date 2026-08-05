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
    publication_commit_path: Path,
    task_run_path: Path,
    pr_url: str,
    output_path: Path,
) -> dict[str, Any]:
    validation = _load(validation_result_path)
    publication = _load(publication_commit_path)
    if validation.get("schema") != "github-governed-repair-stage3@1":
        raise CompletionError("unsupported Stage-3 validation schema")
    if validation.get("status") != "VALIDATED_FOR_DRAFT_PR":
        raise CompletionError("Stage-3 validation result is not publishable")
    if validation.get("full_validation_passed") is not True:
        raise CompletionError("full validation did not pass")
    if validation.get("draft_pr_published") is not False:
        raise CompletionError("Draft PR publication was already asserted")
    if validation.get("production_closed") is not False:
        raise CompletionError("invalid production closure authority")
    if publication.get("schema") != "github-governed-repair-stage3-publication@1":
        raise CompletionError("unsupported publication commit schema")
    if publication.get("status") != "PUBLICATION_COMMIT_PREPARED":
        raise CompletionError("publication commit was not prepared")
    if publication.get("full_validation_passed") is not True:
        raise CompletionError("publication commit is not bound to full validation")
    if publication.get("draft_pr_published") is not False:
        raise CompletionError("publication commit already asserts a Draft PR")
    if publication.get("production_closed") is not False:
        raise CompletionError("invalid publication closure authority")
    if not pr_url.startswith("https://github.com/") or "/pull/" not in pr_url:
        raise CompletionError("a valid GitHub Draft PR URL is required")

    expected = {
        "source_run_id": validation.get("source_run_id"),
        "source_head_sha": validation.get("head_sha"),
        "validated_candidate_sha": validation.get("candidate_sha"),
        "repair_branch": validation.get("repair_branch"),
        "repair_base_branch": validation.get("repair_base_branch"),
        "changed_paths": validation.get("changed_paths"),
    }
    mismatched = [key for key, value in expected.items() if publication.get(key) != value]
    if mismatched:
        raise CompletionError(f"validation/publication binding mismatch: {mismatched}")

    task_payload = _load(task_run_path)
    task = TaskRunStore(task_run_path.resolve(), task_payload)
    if task.payload.get("status") != "WAITING_EXTERNAL_RESULT":
        raise CompletionError("TaskRun is not waiting for Draft PR publication")
    if task.payload.get("phase") != "STAGE3_DRAFT_PR_REQUIRED":
        raise CompletionError("TaskRun phase is not STAGE3_DRAFT_PR_REQUIRED")

    validated_candidate_sha = str(validation.get("candidate_sha") or "")
    published_candidate_sha = str(publication.get("published_candidate_sha") or "")
    validated_tree_sha = str(publication.get("validated_tree_sha") or "")
    snapshot = str(validation.get("quick_workspace_snapshot_fingerprint") or "")
    if not validated_candidate_sha or not published_candidate_sha or not validated_tree_sha or not snapshot:
        raise CompletionError("Stage-3 evidence lacks candidate/tree identity")

    task.mark_condition(
        "draft_pr_published",
        evidence_refs=[
            pr_url,
            f"published-candidate-sha:{published_candidate_sha}",
            f"validated-tree-sha:{validated_tree_sha}",
        ],
    )
    task.checkpoint(
        status="VALIDATING",
        phase="STAGE3_PUBLICATION_RECORDED",
        workspace_fingerprint=snapshot,
        evidence_refs=[pr_url, str(validation_result_path), str(publication_commit_path)],
        metadata={
            "draft_pr_url": pr_url,
            "validated_candidate_sha": validated_candidate_sha,
            "published_candidate_sha": published_candidate_sha,
            "validated_tree_sha": validated_tree_sha,
        },
    )
    task.complete(
        workspace_fingerprint=snapshot,
        evidence_refs=[str(validation_result_path), str(publication_commit_path), pr_url],
    )

    completed = dict(validation)
    completed.update(
        {
            "status": "DRAFT_REPAIR_PR_PUBLISHED",
            "validated_candidate_sha": validated_candidate_sha,
            "published_candidate_sha": published_candidate_sha,
            "validated_tree_sha": validated_tree_sha,
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
    parser.add_argument("--publication-commit", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--pr-url", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        complete_publication(
            validation_result_path=Path(args.validation_result),
            publication_commit_path=Path(args.publication_commit),
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
