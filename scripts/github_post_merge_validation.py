#!/usr/bin/env python3
from __future__ import annotations

"""Validate one already-consumed merge against exact post-merge CI evidence.

This controller is validation-only. It never grants merge, deploy, release, or
production authority. The workflow owns GitHub API orchestration; this module
keeps the semantic checks deterministic and unit-testable.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

TARGET_SCHEMA = "governed-post-merge-target@1"
RESULT_SCHEMA = "governed-post-merge-validation@1"


class PostMergeValidationError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PostMergeValidationError(f"JSON object required: {path}")
    return payload


def _sha(raw: object, label: str) -> str:
    value = str(raw or "")
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise PostMergeValidationError(f"{label} is invalid")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def verify_target(
    pr: Mapping[str, Any],
    merge_commit: Mapping[str, Any],
    *,
    source_pr_number: int,
    merge_sha: str,
    actor: str,
    repository_owner: str,
) -> dict[str, Any]:
    merge_sha = _sha(merge_sha, "merge SHA")
    if actor != repository_owner or not actor:
        raise PostMergeValidationError("post-merge validation may only be started by the repository owner")
    if int(pr.get("number") or 0) != source_pr_number:
        raise PostMergeValidationError("source pull request number mismatch")
    if str(pr.get("state") or "").lower() != "closed":
        raise PostMergeValidationError("source pull request is not closed")
    if pr.get("merged") is not True or not pr.get("merged_at"):
        raise PostMergeValidationError("source pull request is not merged")
    if _sha(pr.get("merge_commit_sha"), "PR merge commit SHA") != merge_sha:
        raise PostMergeValidationError("requested merge SHA is not the pull request merge commit")

    head = pr.get("head")
    base = pr.get("base")
    if not isinstance(head, Mapping) or not isinstance(base, Mapping):
        raise PostMergeValidationError("pull request head/base metadata is missing")
    pr_head_sha = _sha(head.get("sha"), "pull request head SHA")
    base_branch = str(base.get("ref") or "")
    if not base_branch:
        raise PostMergeValidationError("pull request base branch is missing")

    if _sha(merge_commit.get("sha"), "merge commit SHA") != merge_sha:
        raise PostMergeValidationError("merge commit payload does not match requested merge SHA")
    parents = merge_commit.get("parents")
    if not isinstance(parents, list) or len(parents) != 2:
        raise PostMergeValidationError("merge commit must have exactly two parents")
    merge_base_sha = _sha(parents[0].get("sha") if isinstance(parents[0], Mapping) else None, "merge base parent SHA")
    merge_head_sha = _sha(parents[1].get("sha") if isinstance(parents[1], Mapping) else None, "merge head parent SHA")
    if merge_head_sha != pr_head_sha:
        raise PostMergeValidationError("merge second parent is not the pull request head")

    target: dict[str, Any] = {
        "schema": TARGET_SCHEMA,
        "status": "POST_MERGE_TARGET_VERIFIED",
        "source_pr_number": source_pr_number,
        "source_pr_url": str(pr.get("html_url") or ""),
        "merge_sha": merge_sha,
        "merge_base_sha": merge_base_sha,
        "merge_head_sha": merge_head_sha,
        "base_branch": base_branch,
        "actor": actor,
        "authority_effect": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    target["target_receipt_sha256"] = _fingerprint(target)
    return target


def finalize(
    target: Mapping[str, Any],
    quality_run: Mapping[str, Any],
    convergence_run: Mapping[str, Any],
) -> dict[str, Any]:
    if target.get("schema") != TARGET_SCHEMA or target.get("status") != "POST_MERGE_TARGET_VERIFIED":
        raise PostMergeValidationError("post-merge target receipt is not verified")
    if target.get("authority_effect") is not False:
        raise PostMergeValidationError("post-merge target illegally carries authority")
    if target.get("merge_allowed") is not False or target.get("deploy_allowed") is not False or target.get("production_closed") is not False:
        raise PostMergeValidationError("post-merge target changed protected authority flags")
    merge_sha = _sha(target.get("merge_sha"), "target merge SHA")

    if quality_run.get("name") != "quality":
        raise PostMergeValidationError("source Quality workflow name mismatch")
    if quality_run.get("event") != "workflow_dispatch":
        raise PostMergeValidationError("post-merge Quality must be explicitly dispatched")
    if quality_run.get("status") != "completed" or quality_run.get("conclusion") != "success":
        raise PostMergeValidationError("post-merge Quality did not complete successfully")
    if _sha(quality_run.get("head_sha"), "Quality head SHA") != merge_sha:
        raise PostMergeValidationError("post-merge Quality was not bound to the exact merge SHA")

    if convergence_run.get("name") != "project-convergence":
        raise PostMergeValidationError("project-convergence workflow name mismatch")
    if convergence_run.get("event") != "workflow_run":
        raise PostMergeValidationError("project-convergence was not chained from Quality")
    if convergence_run.get("status") != "completed" or convergence_run.get("conclusion") != "success":
        raise PostMergeValidationError("project-convergence did not complete successfully")
    if _sha(convergence_run.get("head_sha"), "project-convergence head SHA") != merge_sha:
        raise PostMergeValidationError("project-convergence was not bound to the exact merge SHA")

    quality_run_id = int(quality_run.get("id") or 0)
    quality_attempt = int(quality_run.get("run_attempt") or 0)
    convergence_run_id = int(convergence_run.get("id") or 0)
    convergence_attempt = int(convergence_run.get("run_attempt") or 0)
    if min(quality_run_id, quality_attempt, convergence_run_id, convergence_attempt) <= 0:
        raise PostMergeValidationError("workflow run identity is invalid")

    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "status": "POST_MERGE_VALIDATED",
        "source_pr_number": int(target.get("source_pr_number") or 0),
        "merge_sha": merge_sha,
        "merge_base_sha": _sha(target.get("merge_base_sha"), "target merge base SHA"),
        "merge_head_sha": _sha(target.get("merge_head_sha"), "target merge head SHA"),
        "quality_run_id": quality_run_id,
        "quality_run_attempt": quality_attempt,
        "project_convergence_run_id": convergence_run_id,
        "project_convergence_run_attempt": convergence_attempt,
        "authority_effect": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    result["post_merge_receipt_sha256"] = _fingerprint(result)
    return result


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify-target")
    verify.add_argument("--pr", required=True)
    verify.add_argument("--merge-commit", required=True)
    verify.add_argument("--source-pr-number", required=True, type=int)
    verify.add_argument("--merge-sha", required=True)
    verify.add_argument("--actor", required=True)
    verify.add_argument("--repository-owner", required=True)
    verify.add_argument("--output", required=True)

    finish = subparsers.add_parser("finalize")
    finish.add_argument("--target", required=True)
    finish.add_argument("--quality-run", required=True)
    finish.add_argument("--convergence-run", required=True)
    finish.add_argument("--output", required=True)

    args = parser.parse_args()
    try:
        if args.command == "verify-target":
            payload = verify_target(
                _load(Path(args.pr)),
                _load(Path(args.merge_commit)),
                source_pr_number=args.source_pr_number,
                merge_sha=args.merge_sha,
                actor=args.actor,
                repository_owner=args.repository_owner,
            )
        else:
            payload = finalize(
                _load(Path(args.target)),
                _load(Path(args.quality_run)),
                _load(Path(args.convergence_run)),
            )
        _write(Path(args.output), payload)
    except (OSError, json.JSONDecodeError, ValueError, PostMergeValidationError) as exc:
        print(json.dumps({
            "status": "BLOCKED",
            "error": str(exc),
            "authority_effect": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
