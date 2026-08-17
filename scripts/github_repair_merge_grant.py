#!/usr/bin/env python3
from __future__ import annotations

"""Issue an exact candidate/live-base merge grant from a completed G6 receipt.

The G6 receipt certifies one immutable candidate head. GitHub may later require
that candidate branch to be brought up to the current base before merge. A
merge grant may therefore bind either the certified head itself or one
machine-verified base-sync wrapper whose tree is the canonical conflict-free
merge of the certified head and the live base.

The grant remains merge-only authority. It cannot authorize deployment,
production certification, or production closure. The consuming workflow must
re-check the same immutable PR merge head and the same live base tip
immediately before merge and consume the grant in the same run.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

EXACT_HEAD_SCHEMA = "governed-repair-exact-head@1"
MERGE_GRANT_SCHEMA = "governed-repair-merge-grant@1"
HEAD_AUTHORITY_EXACT = "certified_exact_head"
HEAD_AUTHORITY_BASE_SYNC = "verified_base_sync"


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


def _sha(raw: object, label: str) -> str:
    value = str(raw or "")
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise MergeGrantError(f"{label} is invalid")
    return value


def issue_merge_grant(
    exact_head: Mapping[str, Any],
    pr_state: Mapping[str, Any],
    base_state: Mapping[str, Any],
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

    certified_head_sha = _sha(exact_head.get("baseline_commit_sha"), "exact head SHA")
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
    if str(pr_state.get("base_branch") or "") != expected_base_branch:
        raise MergeGrantError("pull request base branch drifted")
    if pr_state.get("mergeable") is not True:
        raise MergeGrantError("pull request is not mergeable")
    if str(pr_state.get("mergeable_state") or "") == "behind":
        raise MergeGrantError("pull request is behind the live base")

    live_base_branch = str(base_state.get("branch") or "")
    if live_base_branch != expected_base_branch:
        raise MergeGrantError("live base branch does not match certified repair base branch")
    live_base_sha = _sha(base_state.get("sha"), "live base SHA")

    current_head_sha = _sha(pr_state.get("head_sha"), "current PR head SHA")
    head_authority = str(pr_state.get("head_sha_authority") or "")
    if current_head_sha == certified_head_sha:
        if head_authority not in ("", HEAD_AUTHORITY_EXACT):
            raise MergeGrantError("exact PR head has conflicting head authority")
        head_authority = HEAD_AUTHORITY_EXACT
    else:
        if head_authority != HEAD_AUTHORITY_BASE_SYNC:
            raise MergeGrantError("pull request head drifted after exact-head certification")
        if str(pr_state.get("certified_head_sha") or "") != certified_head_sha:
            raise MergeGrantError("base-sync head is not bound to the certified exact head")
        if str(pr_state.get("integration_base_sha") or "") != live_base_sha:
            raise MergeGrantError("base-sync head is not bound to the live base")
        if pr_state.get("integration_parents_verified") is not True:
            raise MergeGrantError("base-sync parent identity was not verified")
        if pr_state.get("integration_tree_verified") is not True:
            raise MergeGrantError("base-sync canonical merge tree was not verified")

    grant: dict[str, Any] = {
        "schema": MERGE_GRANT_SCHEMA,
        "status": "MERGE_GRANT_ISSUED",
        "repository": exact_head.get("repository"),
        "pull_request_url": pr_url,
        "pull_request_number": pr_number,
        "certified_head_sha": certified_head_sha,
        "head_sha": current_head_sha,
        "head_sha_authority": head_authority,
        "base_branch": expected_base_branch,
        "base_sha": live_base_sha,
        "base_sha_authority": "live_branch_tip",
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
    parser.add_argument("--base-state", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--repository-owner", required=True)
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        grant = issue_merge_grant(
            _load(Path(args.exact_head_receipt)),
            _load(Path(args.pr_state)),
            _load(Path(args.base_state)),
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
