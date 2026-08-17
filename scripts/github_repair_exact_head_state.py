#!/usr/bin/env python3
from __future__ import annotations

"""Classify exact-head PR CI without mutating governance or baseline state.

This is the single authority for the external-CI wait state after protected
baseline acceptance. It distinguishes a real terminal CI failure from GitHub's
`action_required` approval wait. It never accepts a baseline, finalizes G6,
marks a PR Ready, merges, deploys, or closes production.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "governed-repair-exact-head-ci-state@1"
CI_SCHEMA = "governed-repair-exact-head-ci@1"
MANDATORY_WORKFLOWS = ("skill-self-validation", "quality")

STATE_PENDING = "EXACT_HEAD_CI_PENDING"
STATE_AWAITING_APPROVAL = "EXACT_HEAD_CI_AWAITING_APPROVAL"
STATE_FAILED = "EXACT_HEAD_CI_FAILED"
STATE_PASSED = "EXACT_HEAD_CI_PASSED"


class ExactHeadStateError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ExactHeadStateError(f"JSON object required: {path}")
    return payload


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _pr_number_from_url(raw: object) -> int:
    value = str(raw or "").strip().rstrip("/")
    match = re.search(r"/pull/(\d+)$", value)
    if match is None:
        raise ExactHeadStateError("Draft PR URL does not contain a valid pull request number")
    number = int(match.group(1))
    if number <= 0:
        raise ExactHeadStateError("Draft PR number is invalid")
    return number


def _validate_binding(
    ci: Mapping[str, Any],
    *,
    exact_sha: str,
    draft_pr_url: str,
) -> tuple[int, dict[str, Mapping[str, Any]]]:
    if ci.get("schema") != CI_SCHEMA:
        raise ExactHeadStateError("unsupported exact-head CI evidence")
    if not re.fullmatch(r"[0-9a-f]{40}", exact_sha):
        raise ExactHeadStateError("exact SHA is invalid")
    if str(ci.get("head_sha") or "") != exact_sha:
        raise ExactHeadStateError("CI evidence is not bound to the accepted baseline commit")
    if str(ci.get("pr_url") or "") != draft_pr_url:
        raise ExactHeadStateError("CI evidence PR binding mismatch")

    expected_pr_number = _pr_number_from_url(draft_pr_url)
    if int(ci.get("pr_number") or 0) != expected_pr_number:
        raise ExactHeadStateError("CI evidence pull request number mismatch")
    if ci.get("pr_is_draft") is not True:
        raise ExactHeadStateError("PR must remain Draft while G6 exact-head CI is pending")
    if str(ci.get("pr_head_sha") or "") != exact_sha:
        raise ExactHeadStateError("Draft PR head is not the accepted baseline commit")

    raw_workflows = ci.get("workflows")
    if not isinstance(raw_workflows, dict):
        raise ExactHeadStateError("exact-head workflow evidence is missing")

    workflows: dict[str, Mapping[str, Any]] = {}
    for workflow in MANDATORY_WORKFLOWS:
        row = raw_workflows.get(workflow)
        if not isinstance(row, dict):
            raise ExactHeadStateError(f"mandatory workflow evidence is missing: {workflow}")
        if str(row.get("head_sha") or "") != exact_sha:
            raise ExactHeadStateError(f"workflow {workflow} ran on the wrong SHA")
        if row.get("event") != "pull_request":
            raise ExactHeadStateError(f"workflow {workflow} is not a pull_request run")
        if int(row.get("pr_number") or 0) != expected_pr_number:
            raise ExactHeadStateError(f"workflow {workflow} ran for the wrong pull request")
        if not str(row.get("run_id") or "").isdigit():
            raise ExactHeadStateError(f"workflow {workflow} lacks a valid run ID")
        status = str(row.get("status") or "")
        if status not in {"queued", "in_progress", "completed", "pending", "waiting", "requested"}:
            raise ExactHeadStateError(f"workflow {workflow} has unsupported status: {status}")
        workflows[workflow] = row
    return expected_pr_number, workflows


def classify_exact_head_ci(
    ci: Mapping[str, Any],
    *,
    exact_sha: str,
    draft_pr_url: str,
) -> dict[str, Any]:
    pr_number, workflows = _validate_binding(
        ci,
        exact_sha=exact_sha,
        draft_pr_url=draft_pr_url,
    )

    terminal_bad: list[str] = []
    awaiting_approval: list[str] = []
    unfinished: list[str] = []
    successful: list[str] = []

    for name in MANDATORY_WORKFLOWS:
        row = workflows[name]
        status = str(row.get("status") or "")
        conclusion = str(row.get("conclusion") or "")
        if status != "completed":
            unfinished.append(name)
            continue
        if conclusion == "success":
            successful.append(name)
        elif conclusion == "action_required":
            awaiting_approval.append(name)
        else:
            terminal_bad.append(f"{name}:{conclusion or 'missing'}")

    if terminal_bad:
        state = STATE_FAILED
    elif awaiting_approval:
        state = STATE_AWAITING_APPROVAL
    elif len(successful) == len(MANDATORY_WORKFLOWS):
        state = STATE_PASSED
    else:
        state = STATE_PENDING

    result: dict[str, Any] = {
        "schema": SCHEMA,
        "status": state,
        "head_sha": exact_sha,
        "pr_url": draft_pr_url,
        "pr_number": pr_number,
        "workflow_run_ids": {
            name: str(workflows[name].get("run_id")) for name in MANDATORY_WORKFLOWS
        },
        "successful_workflows": sorted(successful),
        "unfinished_workflows": sorted(unfinished),
        "approval_required_workflows": sorted(awaiting_approval),
        "failed_workflows": sorted(terminal_bad),
        "resume_required": state == STATE_AWAITING_APPROVAL,
        "finalize_allowed": state == STATE_PASSED,
        "baseline_mutation_allowed": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    result["exact_head_ci_state_sha256"] = _fingerprint(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ci-evidence", required=True)
    parser.add_argument("--exact-sha", required=True)
    parser.add_argument("--draft-pr-url", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = classify_exact_head_ci(
            _load(Path(args.ci_evidence)),
            exact_sha=args.exact_sha,
            draft_pr_url=args.draft_pr_url,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, ExactHeadStateError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error": str(exc),
                    "resume_required": False,
                    "finalize_allowed": False,
                    "baseline_mutation_allowed": False,
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
