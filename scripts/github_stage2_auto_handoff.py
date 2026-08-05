#!/usr/bin/env python3
"""Validate a failed Quality run before automatically dispatching Stage 2.

This controller consumes only GitHub API metadata plus the Stage-1 evidence artifact.
It never reads model Secrets, executes candidate code, edits source, or publishes a PR.
Its only purpose is to prove that an exact failed run is still bound to the current
open same-repository PR and remains structurally eligible for the governed Stage-2
repair workflow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SHA40 = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_CONCLUSIONS = {"failure"}


class AutoHandoffError(RuntimeError):
    """Fail-closed automatic handoff validation error."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutoHandoffError(f"invalid JSON input: {path}") from exc
    if not isinstance(payload, dict):
        raise AutoHandoffError(f"JSON object required: {path}")
    return payload


def _repo(value: object) -> str:
    return str(value or "").strip()


def _run_pr_numbers(run: dict[str, Any]) -> set[int]:
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


def _binding(task: dict[str, Any]) -> dict[str, Any]:
    value = task.get("binding")
    return value if isinstance(value, dict) else {}


def validate_handoff(
    *,
    repository: str,
    run: dict[str, Any],
    pull_request: dict[str, Any],
    failure: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any]:
    repo = _repo(repository)
    if not repo or "/" not in repo:
        raise AutoHandoffError("repository must be owner/name")

    try:
        run_id = int(run.get("id") or 0)
        run_attempt = int(run.get("run_attempt") or 0)
    except (TypeError, ValueError) as exc:
        raise AutoHandoffError("workflow run identity is invalid") from exc
    if run_id <= 0 or run_attempt <= 0:
        raise AutoHandoffError("workflow run identity must be positive")
    if str(run.get("name") or "") != "quality":
        raise AutoHandoffError("only the quality workflow may enter automatic Stage 2")
    if str(run.get("status") or "").casefold() != "completed":
        raise AutoHandoffError("quality run is not completed")
    conclusion = str(run.get("conclusion") or "").casefold()
    if conclusion not in ALLOWED_CONCLUSIONS:
        raise AutoHandoffError(f"quality conclusion is not repairable: {conclusion!r}")

    head_repository = run.get("head_repository")
    if (
        not isinstance(head_repository, dict)
        or _repo(head_repository.get("full_name")) != repo
    ):
        raise AutoHandoffError("quality run head repository is not the current repository")
    head_sha = str(run.get("head_sha") or "").casefold()
    if not SHA40.fullmatch(head_sha):
        raise AutoHandoffError("quality run head SHA is invalid")
    head_branch = str(run.get("head_branch") or "")
    if not head_branch or head_branch.startswith("governed-repair/"):
        raise AutoHandoffError("recursive governed-repair branches are not auto-dispatched")

    try:
        pr_number = int(pull_request.get("number") or 0)
    except (TypeError, ValueError) as exc:
        raise AutoHandoffError("pull request number is invalid") from exc
    if pr_number <= 0 or pr_number not in _run_pr_numbers(run):
        raise AutoHandoffError("quality run is not bound to the selected pull request")
    if str(pull_request.get("state") or "").casefold() != "open":
        raise AutoHandoffError("pull request is no longer open")
    pr_head = pull_request.get("head")
    if not isinstance(pr_head, dict):
        raise AutoHandoffError("pull request head is missing")
    pr_head_repo = pr_head.get("repo")
    if (
        not isinstance(pr_head_repo, dict)
        or _repo(pr_head_repo.get("full_name")) != repo
    ):
        raise AutoHandoffError("pull request head repository is foreign")
    if str(pr_head.get("sha") or "").casefold() != head_sha:
        raise AutoHandoffError("quality run is stale relative to the current PR head")

    if failure.get("schema") != "github-failure-ingest@1":
        raise AutoHandoffError("unsupported Stage-1 failure-case schema")
    if failure.get("status") != "INGESTED":
        raise AutoHandoffError("Stage-1 evidence is not complete")
    expected_failure = {
        "repository": repo,
        "workflow_name": "quality",
        "workflow_run_id": str(run_id),
        "workflow_run_attempt": str(run_attempt),
        "head_sha": head_sha,
        "same_repository": True,
        "classification": "code_or_contract",
        "repair_allowed": True,
    }
    for key, expected in expected_failure.items():
        if str(failure.get(key)) != str(expected):
            raise AutoHandoffError(f"Stage-1 evidence mismatch: {key}")

    candidate_paths = {
        str(item).strip().replace("\\", "/")
        for item in failure.get("candidate_paths") or []
        if str(item).strip()
    }
    changed_paths = {
        str(item).strip().replace("\\", "/")
        for item in failure.get("source_changed_files") or []
        if str(item).strip()
    }
    repairable_paths = sorted(candidate_paths & changed_paths)
    if not repairable_paths:
        raise AutoHandoffError(
            "Stage-1 evidence has no candidate path that is also changed by the PR"
        )

    binding = _binding(task)
    expected_binding = {
        "repository": repo,
        "workflow_name": "quality",
        "workflow_run_id": str(run_id),
        "workflow_run_attempt": str(run_attempt),
        "head_sha": head_sha,
        "failure_signature": str(failure.get("failure_signature") or ""),
    }
    mismatched = [
        key
        for key, expected in expected_binding.items()
        if str(binding.get(key)) != expected
    ]
    if mismatched:
        raise AutoHandoffError(f"Stage-1 TaskRun binding mismatch: {mismatched}")

    marker = f"AUTO_STAGE2_DISPATCHED:{run_id}/{run_attempt}"
    digest = hashlib.sha256(
        json.dumps(
            {
                "repository": repo,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "head_sha": head_sha,
                "pr_number": pr_number,
                "failure_signature": failure.get("failure_signature"),
                "repairable_paths": repairable_paths,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema": "github-stage2-auto-handoff@1",
        "status": "READY",
        "repository": repo,
        "source_run_id": str(run_id),
        "source_run_attempt": str(run_attempt),
        "source_pr_number": pr_number,
        "head_sha": head_sha,
        "failure_signature": failure.get("failure_signature"),
        "repairable_paths": repairable_paths,
        "dispatch_marker": marker,
        "binding_sha256": digest,
        "stage2_started": False,
        "source_changed": False,
        "production_closed": False,
    }


def _write_outputs(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key in (
            "source_run_id",
            "source_run_attempt",
            "source_pr_number",
            "head_sha",
            "failure_signature",
            "dispatch_marker",
            "binding_sha256",
        ):
            handle.write(f"{key}={payload[key]}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-json", required=True)
    parser.add_argument("--pr-json", required=True)
    parser.add_argument("--failure-case", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    payload = validate_handoff(
        repository=args.repository,
        run=_load_object(Path(args.run_json)),
        pull_request=_load_object(Path(args.pr_json)),
        failure=_load_object(Path(args.failure_case)),
        task=_load_object(Path(args.task_run)),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_outputs(
        Path(args.github_output) if args.github_output else None,
        payload,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
