#!/usr/bin/env python3
"""Build a trusted Stage-1 event from the active PR quality workflow.

This binder is used only by the terminal failure job inside ``quality.yml``. It
converts the immutable pull-request event and current Actions run identity into the
same ``workflow_run`` shape consumed by the existing redacting Stage-1 ingestion
controller. It never reads Secrets, executes candidate content, or authorizes Stage 2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SHA40 = re.compile(r"^[0-9a-f]{40}$")


class DirectQualityEventError(RuntimeError):
    """Fail-closed direct quality event validation error."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DirectQualityEventError(f"invalid JSON input: {path}") from exc
    if not isinstance(payload, dict):
        raise DirectQualityEventError(f"JSON object required: {path}")
    return payload


def _repo_name(value: object) -> str:
    return str(value or "").strip()


def build_event(
    *,
    source_event: dict[str, Any],
    repository: str,
    run_id: int,
    run_attempt: int,
    workflow_url: str,
) -> dict[str, Any]:
    repo = _repo_name(repository)
    if not repo or "/" not in repo:
        raise DirectQualityEventError("repository must be owner/name")
    if run_id <= 0 or run_attempt <= 0:
        raise DirectQualityEventError("run id and attempt must be positive")

    event_repo = source_event.get("repository")
    if not isinstance(event_repo, dict) or _repo_name(event_repo.get("full_name")) != repo:
        raise DirectQualityEventError("pull-request event repository mismatch")
    pull_request = source_event.get("pull_request")
    if not isinstance(pull_request, dict):
        raise DirectQualityEventError("pull_request event payload is required")

    try:
        pr_number = int(pull_request.get("number") or 0)
    except (TypeError, ValueError) as exc:
        raise DirectQualityEventError("pull request number is invalid") from exc
    if pr_number <= 0:
        raise DirectQualityEventError("pull request number is required")

    head = pull_request.get("head")
    base = pull_request.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise DirectQualityEventError("pull request head/base identity is missing")
    head_repo = head.get("repo")
    if not isinstance(head_repo, dict) or _repo_name(head_repo.get("full_name")) != repo:
        raise DirectQualityEventError("only same-repository pull requests are supported")

    head_sha = str(head.get("sha") or "").casefold()
    if not SHA40.fullmatch(head_sha):
        raise DirectQualityEventError("pull request head SHA is invalid")
    head_branch = str(head.get("ref") or "").strip()
    base_branch = str(base.get("ref") or "").strip()
    if not head_branch or not base_branch:
        raise DirectQualityEventError("pull request branch identity is missing")
    if head_branch.startswith("governed-repair/"):
        raise DirectQualityEventError("recursive repair branches are not direct-ingestion sources")

    binding = {
        "repository": repo,
        "pull_request": pr_number,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head_sha": head_sha,
        "head_branch": head_branch,
        "base_branch": base_branch,
        "workflow_name": "quality",
        "conclusion": "failure",
        "mode": "in-run-stage1-only",
    }
    return {
        "repository": {"full_name": repo},
        "workflow_run": {
            "id": run_id,
            "run_attempt": run_attempt,
            "name": "quality",
            "status": "completed",
            "conclusion": "failure",
            "event": "pull_request",
            "head_sha": head_sha,
            "head_branch": head_branch,
            "head_repository": {"full_name": repo},
            "html_url": workflow_url,
            "pull_requests": [
                {
                    "number": pr_number,
                    "head": {"ref": head_branch, "sha": head_sha},
                    "base": {"ref": base_branch},
                }
            ],
        },
        "direct_handoff": {
            "schema": "quality-in-run-stage1@1",
            "mode": "in-run-stage1-only",
            "source_pr_number": pr_number,
            "source_run_id": run_id,
            "binding_sha256": hashlib.sha256(
                json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "production_closed": False,
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
    parser.add_argument("--event", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--workflow-url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    event = build_event(
        source_event=_load_object(Path(args.event)),
        repository=args.repository,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        workflow_url=args.workflow_url,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run = event["workflow_run"]
    direct = event["direct_handoff"]
    _write_outputs(
        Path(args.github_output) if args.github_output else None,
        {
            "source_run_id": run["id"],
            "source_run_attempt": run["run_attempt"],
            "source_pr_number": direct["source_pr_number"],
            "head_sha": run["head_sha"],
            "head_branch": run["head_branch"],
            "workflow_name": run["name"],
            "source_conclusion": run["conclusion"],
            "source_url": run["html_url"],
            "trigger_mode": "quality-in-run-stage1",
        },
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "mode": direct["mode"],
                "source_run_id": run["id"],
                "source_pr_number": direct["source_pr_number"],
                "head_sha": run["head_sha"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
