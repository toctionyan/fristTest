#!/usr/bin/env python3
"""Build a bounded workflow_run event for owner-authorized Stage-1 recovery.

The recovery lane accepts only an exact numeric command, binds the requested failed
run to the current open pull request and same repository, and writes a synthetic
event consumed by the existing Stage-1 ingestion controller. It never reads
Secrets, modifies source, or authorizes Stage 2 by itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ALLOWED_WORKFLOWS = {"quality", "wp08-full-stack-certification"}
FAILED_CONCLUSIONS = {
    "failure",
    "timed_out",
    "cancelled",
    "action_required",
    "startup_failure",
    "stale",
}
COMMAND = re.compile(r"^/governed-repair-ingest[ \t]+([1-9][0-9]*)[ \t]*$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")


class RecoveryEventError(RuntimeError):
    """Fail-closed owner recovery validation error."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryEventError(f"invalid JSON input: {path}") from exc
    if not isinstance(payload, dict):
        raise RecoveryEventError(f"JSON object required: {path}")
    return payload


def parse_command(comment: str) -> int:
    match = COMMAND.fullmatch(comment.strip())
    if not match:
        raise RecoveryEventError(
            "comment must be exactly '/governed-repair-ingest <numeric-run-id>'"
        )
    return int(match.group(1))


def _repo_name(value: object) -> str:
    return str(value or "").strip()


def _run_pull_numbers(run: dict[str, Any]) -> set[int]:
    result: set[int] = set()
    for row in run.get("pull_requests") or []:
        if not isinstance(row, dict):
            continue
        try:
            number = int(row.get("number") or 0)
        except (TypeError, ValueError):
            continue
        if number > 0:
            result.add(number)
    return result


def build_event(
    *,
    repository: str,
    issue_number: int,
    expected_run_id: int,
    run: dict[str, Any],
    pull_request: dict[str, Any],
) -> dict[str, Any]:
    repo = _repo_name(repository)
    if not repo or "/" not in repo:
        raise RecoveryEventError("repository must be owner/name")

    try:
        actual_run_id = int(run.get("id") or 0)
    except (TypeError, ValueError) as exc:
        raise RecoveryEventError("workflow run id is invalid") from exc
    if actual_run_id != expected_run_id:
        raise RecoveryEventError("requested run id does not match fetched workflow run")

    workflow_name = str(run.get("name") or "")
    if workflow_name not in ALLOWED_WORKFLOWS:
        raise RecoveryEventError(f"workflow is not recoverable: {workflow_name!r}")
    if str(run.get("status") or "").casefold() != "completed":
        raise RecoveryEventError("workflow run is not completed")
    conclusion = str(run.get("conclusion") or "").casefold()
    if conclusion not in FAILED_CONCLUSIONS:
        raise RecoveryEventError(f"workflow run conclusion is not recoverable: {conclusion!r}")

    head_repository = run.get("head_repository")
    if (
        not isinstance(head_repository, dict)
        or _repo_name(head_repository.get("full_name")) != repo
    ):
        raise RecoveryEventError("workflow run head repository is not the current repository")
    head_sha = str(run.get("head_sha") or "").casefold()
    if not SHA40.fullmatch(head_sha):
        raise RecoveryEventError("workflow run head SHA is invalid")

    try:
        actual_pr_number = int(pull_request.get("number") or 0)
    except (TypeError, ValueError) as exc:
        raise RecoveryEventError("pull request number is invalid") from exc
    if actual_pr_number != issue_number:
        raise RecoveryEventError("comment issue number does not match fetched pull request")
    if str(pull_request.get("state") or "").casefold() != "open":
        raise RecoveryEventError("pull request must remain open for recovery")

    pr_head = pull_request.get("head")
    if not isinstance(pr_head, dict):
        raise RecoveryEventError("pull request head is missing")
    pr_head_repo = pr_head.get("repo")
    if (
        not isinstance(pr_head_repo, dict)
        or _repo_name(pr_head_repo.get("full_name")) != repo
    ):
        raise RecoveryEventError("pull request head repository is not the current repository")
    if str(pr_head.get("sha") or "").casefold() != head_sha:
        raise RecoveryEventError("workflow run is stale relative to the current pull request head")
    if issue_number not in _run_pull_numbers(run):
        raise RecoveryEventError("workflow run is not bound to the commented pull request")

    binding = {
        "repository": repo,
        "pull_request": issue_number,
        "run_id": actual_run_id,
        "head_sha": head_sha,
        "workflow_name": workflow_name,
        "conclusion": conclusion,
        "mode": "stage1-only",
    }
    return {
        "repository": {"full_name": repo},
        "workflow_run": run,
        "recovery": {
            "schema": "owner-comment-stage1-recovery@1",
            "mode": "stage1-only",
            "source_pr_number": issue_number,
            "source_run_id": actual_run_id,
            "binding_sha256": hashlib.sha256(
                json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        },
    }


def _write_outputs(path: Path | None, values: dict[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comment", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue-number", required=True, type=int)
    parser.add_argument("--run-json", required=True)
    parser.add_argument("--pr-json", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    requested_run_id = parse_command(args.comment)
    run = _load_object(Path(args.run_json))
    pull_request = _load_object(Path(args.pr_json))
    event = build_event(
        repository=args.repository,
        issue_number=args.issue_number,
        expected_run_id=requested_run_id,
        run=run,
        pull_request=pull_request,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    workflow_run = event["workflow_run"]
    _write_outputs(
        Path(args.github_output) if args.github_output else None,
        {
            "source_run_id": workflow_run["id"],
            "source_run_attempt": workflow_run.get("run_attempt") or 1,
            "source_pr_number": args.issue_number,
            "head_sha": workflow_run["head_sha"],
            "workflow_name": workflow_run["name"],
            "trigger_mode": "owner-comment-stage1-recovery",
        },
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "mode": "stage1-only",
                "source_run_id": workflow_run["id"],
                "source_pr_number": args.issue_number,
                "head_sha": workflow_run["head_sha"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
