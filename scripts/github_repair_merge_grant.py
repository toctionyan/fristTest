#!/usr/bin/env python3
from __future__ import annotations

"""Issue an exact PR/head/base merge grant from a completed G6 receipt.

The grant is merge-only authority. It cannot authorize deployment, production
certification, or production closure. The consuming workflow must re-check the
same immutable PR/head/base immediately before merge and consume the grant in the
same run.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

EXACT_HEAD_SCHEMA = "governed-repair-exact-head@1"
MERGE_GRANT_SCHEMA = "governed-repair-merge-grant@1"


class MergeGrantError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise MergeGrantError(f"JSON object required: {path}")
    return payload


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _without(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(payload)
    result.pop(field, None)
    return result


def _pr_number_from_url(raw: object) -> int:
    value = str(raw or "").strip().rstrip("/")
    match = re.search(r"/pull/(\d+)$", value)
    if match is None:
        raise MergeGrantError("exact-head receipt has invalid pull request URL")
    return int(match.group(1))


def issue_merge_grant(
    exact_head: Mapping[str, Any],
    pr_state: Mapping[str, Any],
    *,
    actor: str,
    repository_owner: str,
    approval_ref: str,
) -> dict[str, Any]:
    if exact_head.get("schema") != EXACT_HEAD_SCHEMA:
        raise MergeGrantError("unsupported exact-head receipt")
    if exact_head.get("status") != "READY_FOR_REVIEW":
        raise MergeGrantError("G6 exact-head result is not READY_FOR_REVIEW")
    if exact_head.get("governed_repair_state") != "READY_FOR_REVIEW":
        raise MergeGrantError("G6 governed repair state drift")
    if exact_head.get("governance_closed") is not True:
        raise MergeGrantError("governance is not closed")
    if exact_head.get("baseline_accepted") is not True:
        raise MergeGrantError("baseline was not accepted")
    if exact_head.get("exact_head_certified") is not True:
        raise MergeGrantError("exact head is not certified")
    if exact_head.get("ready_for_review") is not True:
        raise MergeGrantError("repair is not ready for review")
    if exact_head.get("merge_allowed") is not False:
        raise MergeGrantError("exact-head receipt must not already contain merge authority")
    if exact_head.get("deploy_allowed") is not False:
        raise MergeGrantError("exact-head receipt illegally enabled deployment")
    if exact_head.get("production_closed") is not False:
        raise MergeGrantError("exact-head receipt illegally closed production")

    expected_receipt = _fingerprint(_without(exact_head, "exact_head_receipt_sha256"))
    if str(exact_head.get("exact_head_receipt_sha256") or "") != expected_receipt:
        raise MergeGrantError("exact-head receipt fingerprint mismatch")

    gates = exact_head.get("gates")
    if not isinstance(gates, dict):
        raise MergeGrantError("exact-head gates are missing")
    g6 = gates.get("G6_GOVERNANCE_EXACT_HEAD")
    if not isinstance(g6, dict) or g6.get("status") != "PASS":
        raise MergeGrantError("G6 exact-head gate is not PASS")

    if actor != repository_owner:
        raise MergeGrantError("merge grant may only be issued by the repository owner")
    if not actor or not approval_ref:
        raise MergeGrantError("merge grant actor and approval reference are required")

    pr_url = str(exact_head.get("draft_pr_url") or "")
    pr_number = _pr_number_from_url(pr_url)
    if int(exact_head.get("pull_request_number") or 0) != pr_number:
        raise MergeGrantError("exact-head pull request number mismatch")

    exact_head_sha = str(exact_head.get("baseline_commit_sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", exact_head_sha):
        raise MergeGrantError("exact head SHA is invalid")
    expected_base_branch = str(exact_head.get("repair_base_branch") or "")
    if not expected_base_branch:
        raise MergeGrantError("repair base branch is missing")

    if int(pr_state.get("number") or 0) != pr_number:
        raise MergeGrantError("current PR number mismatch")
    if str(pr_state.get("url") or "") != pr_url:
        raise MergeGrantError("current PR URL mismatch")
    if str(pr_state.get("state") or "").upper() != "OPEN":
        raise MergeGrantError("pull request is not open")
    if pr_state.get("is_draft") is not False:
        raise MergeGrantError("pull request must be Ready for review before merge grant")
    if str(pr_state.get("head_sha") or "") != exact_head_sha:
        raise MergeGrantError("pull request head drifted after exact-head certification")
    if str(pr_state.get("base_branch") or "") != expected_base_branch:
        raise MergeGrantError("pull request base branch drifted")
    base_sha = str(pr_state.get("base_sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise MergeGrantError("current base SHA is invalid")

    grant: dict[str, Any] = {
        "schema": MERGE_GRANT_SCHEMA,
        "status": "MERGE_GRANT_ISSUED",
        "repository": exact_head.get("repository"),
        "pull_request_url": pr_url,
        "pull_request_number": pr_number,
        "head_sha": exact_head_sha,
        "base_branch": expected_base_branch,
        "base_sha": base_sha,
        "exact_head_receipt_sha256": exact_head.get("exact_head_receipt_sha256"),
        "governance_sha256": exact_head.get("governance_sha256"),
        "baseline_acceptance_sha256": exact_head.get("baseline_acceptance_sha256"),
        "actor": actor,
        "approval_ref": approval_ref,
        "grant_consumed": False,
        "merge_allowed": True,
        "deploy_allowed": False,
        "production_closed": False,
    }
    grant["merge_grant_sha256"] = _fingerprint(grant)
    return grant


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exact-head-receipt", required=True)
    parser.add_argument("--pr-state", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--repository-owner", required=True)
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        grant = issue_merge_grant(
            _load(Path(args.exact_head_receipt)),
            _load(Path(args.pr_state)),
            actor=args.actor,
            repository_owner=args.repository_owner,
            approval_ref=args.approval_ref,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(grant, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, MergeGrantError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error": str(exc),
                    "merge_allowed": False,
                    "deploy_allowed": False,
                    "production_closed": False,
                },
                sort_keys=True,
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
