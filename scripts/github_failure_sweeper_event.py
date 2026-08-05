#!/usr/bin/env python3
"""Bind a recent failed PR workflow run for Stage-1-only ingestion.

The sweeper is a recovery mechanism for repositories where ``workflow_run`` event
delivery is unavailable or not observable. It accepts only GitHub API objects,
requires a current open same-repository pull request, and emits the same synthetic
``workflow_run`` shape consumed by the existing redacting Stage-1 controller.
It never reads Secrets, edits source, or authorizes Stage 2 by itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ALLOWED_WORKFLOWS = {"quality"}
FAILED_CONCLUSIONS = {
    "failure",
    "timed_out",
    "cancelled",
    "action_required",
    "startup_failure",
    "stale",
}
SHA40 = re.compile(r"^[0-9a-f]{40}$")


class SweeperEventError(RuntimeError):
    """Fail-closed failed-run binding error."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SweeperEventError(f"invalid JSON input: {path}") from exc
    if not isinstance(payload, dict):
        raise SweeperEventError(f"JSON object required: {path}")
    return payload


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
    expected_run_id: int,
    expected_pr_number: int,
    run: dict[str, Any],
    pull_request: dict[str, Any],
) -> dict[str, Any]:
    repo = _repo_name(repository)
    if not repo or "/" not in repo:
        raise SweeperEventError("repository must be owner/name")

    try:
        actual_run_id = int(run.get("id") or 0)
    except (TypeError, ValueError) as exc:
        raise SweeperEventError("workflow run id is invalid") from exc
    if actual_run_id != expected_run_id:
        raise SweeperEventError("selected run id does not match fetched workflow run")

    workflow_name = str(run.get("name") or "")
    if workflow_name not in ALLOWED_WORKFLOWS:
        raise SweeperEventError(f"workflow is not sweepable: {workflow_name!r}")
    if str(run.get("status") or "").casefold() != "completed":
        raise SweeperEventError("workflow run is not completed")
    conclusion = str(run.get("conclusion") or "").casefold()
    if conclusion not in FAILED_CONCLUSIONS:
        raise SweeperEventError(f"workflow run conclusion is not sweepable: {conclusion!r}")

    head_repository = run.get("head_repository")
    if (
        not isinstance(head_repository, dict)
        or _repo_name(head_repository.get("full_name")) != repo
    ):
        raise SweeperEventError("workflow run head repository is not the current repository")
    head_branch = str(run.get("head_branch") or "")
    if not head_branch or head_branch.startswith("governed-repair/"):
        raise SweeperEventError("workflow run branch is not eligible for Stage-1 sweeping")
    head_sha = str(run.get("head_sha") or "").casefold()
    if not SHA40.fullmatch(head_sha):
        raise SweeperEventError("workflow run head SHA is invalid")

    try:
        actual_pr_number = int(pull_request.get("number") or 0)
    except (TypeError, ValueError) as exc:
        raise SweeperEventError("pull request number is invalid") from exc
    if actual_pr_number != expected_pr_number:
        raise SweeperEventError("selected PR number does not match fetched pull request")
    if str(pull_request.get("state") or "").casefold() != "open":
        raise SweeperEventError("pull request must remain open")

    pr_head = pull_request.get("head")
    if not isinstance(pr_head, dict):
        raise SweeperEventError("pull request head is missing")
    pr_head_repo = pr_head.get("repo")
    if (
        not isinstance(pr_head_repo, dict)
        or _repo_name(pr_head_repo.get("full_name")) != repo
    ):
        raise SweeperEventError("pull request head repository is not the current repository")
    if str(pr_head.get("sha") or "").casefold() != head_sha:
        raise SweeperEventError("workflow run is stale relative to the current PR head")
    if expected_pr_number not in _run_pull_numbers(run):
        raise SweeperEventError("workflow run is not bound to the selected pull request")

    binding = {
        "repository": repo,
        "pull_request": expected_pr_number,
        "run_id": actual_run_id,
        "head_sha": head_sha,
        "workflow_name": workflow_name,
        "conclusion": conclusion,
        "mode": "scheduled-stage1-only",
    }
    return {
        "repository": {"full_name": repo},
        "workflow_run": run,
        "recovery": {
            "schema": "failed-run-sweeper@1",
            "mode": "scheduled-stage1-only",
            "source_pr_number": expected_pr_number,
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
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--run-json", required=True)
    parser.add_argument("--pr-json", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    run = _load_object(Path(args.run_json))
    pull_request = _load_object(Path(args.pr_json))
    event = build_event(
        repository=args.repository,
        expected_run_id=args.run_id,
        expected_pr_number=args.pr_number,
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
            "source_pr_number": args.pr_number,
            "head_sha": workflow_run["head_sha"],
            "head_branch": workflow_run.get("head_branch") or "",
            "workflow_name": workflow_run["name"],
            "source_conclusion": workflow_run["conclusion"],
            "source_url": workflow_run.get("html_url") or "",
            "trigger_mode": "scheduled-stage1-sweeper",
        },
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "mode": "scheduled-stage1-only",
                "source_run_id": workflow_run["id"],
                "source_pr_number": args.pr_number,
                "head_sha": workflow_run["head_sha"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
