#!/usr/bin/env python3
from __future__ import annotations

"""Static fail-closed guard for the human G6 governance boundary.

The protected Environment is the human authorization boundary. Exact-head CI is
also part of G6 authority: only pull-request-triggered mandatory workflows bound
to the exact Draft PR and exact accepted baseline SHA may satisfy certification.
A push/manual run on the same SHA must never substitute for the PR checks.
"""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_REL = ".github/workflows/governed-ci-repair-governance.yml"
EXACT_HEAD_REL = "scripts/github_repair_exact_head.py"

WORKFLOW_REQUIRED_MARKERS = (
    "environment:\n      name: governed-repair-governance",
    "Verify protected governance environment policy",
    "environments/governed-repair-governance",
    '.type == "required_reviewers"',
    ".prevent_self_review",
    "any(. == true)",
    "governance environment must have required reviewers and prevent self-review",
    "environment:governed-repair-governance",
    "PR_NUMBER: ${{ steps.pr.outputs.pr_number }}",
    "--json number,isDraft,state,headRefName,headRefOid,baseRefName,url",
    '.event == "pull_request"',
    ".pull_requests[]?",
    "pr_number:($pr_number|tonumber)",
    '.workflows.quality.event == "pull_request"',
    '.workflows["skill-self-validation"].event == "pull_request"',
    "exact-head pull-request CI",
)
EXACT_HEAD_REQUIRED_MARKERS = (
    "def _pr_number_from_url(",
    'if row.get("event") != "pull_request"',
    'int(row.get("pr_number") or 0) != expected_pr_number',
    'if int(ci.get("pr_number") or 0) != expected_pr_number',
    'if str(ci.get("pr_url") or "") != draft_pr_url',
    'if str(ci.get("pr_head_sha") or "") != exact_sha',
    "pull-request:{expected_pr_number}",
)
FORBIDDEN_MARKERS = (
    "gh pr merge",
    "gh pr review --approve",
    "production_closed=true",
)


def _read(root: Path, relative: str, missing_error: str, errors: list[str]) -> str:
    path = root / relative
    if not path.is_file():
        errors.append(missing_error)
        return ""
    return path.read_text(encoding="utf-8")


def verify(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    workflow = _read(root, WORKFLOW_REL, "governance_workflow_missing", errors)
    exact_head = _read(root, EXACT_HEAD_REL, "exact_head_controller_missing", errors)

    for marker in WORKFLOW_REQUIRED_MARKERS:
        if marker not in workflow:
            errors.append(f"governance_environment_contract_missing:{marker}")
    for marker in EXACT_HEAD_REQUIRED_MARKERS:
        if marker not in exact_head:
            errors.append(f"exact_pr_ci_contract_missing:{marker}")
    for marker in FORBIDDEN_MARKERS:
        if marker in workflow or marker in exact_head:
            errors.append(f"governance_environment_forbidden_authority:{marker}")

    # Human intent text is not authorization. The dispatch string is only an
    # acknowledgement; the protected Environment performs the authorization.
    if "description: Type CLOSE_GOVERNANCE_AND_ACCEPT_BASELINE to acknowledge" not in workflow:
        errors.append("approval_token_still_claims_authorization_authority")

    # The workflow must not query same-SHA CI and then accept arbitrary event
    # types. Both mandatory workflow selectors have to filter pull_request and
    # the exact PR number before evidence is materialized.
    if workflow.count('.event == "pull_request"') < 2:
        errors.append("exact_pr_ci_pull_request_filter_incomplete")
    if workflow.count(".pull_requests[]?") < 2:
        errors.append("exact_pr_ci_pr_number_filter_incomplete")

    errors = list(dict.fromkeys(errors))
    return {
        "schema": "governed-repair-environment-contract@2",
        "status": "PASS" if not errors else "FAIL",
        "environment": "governed-repair-governance",
        "requires_required_reviewers": True,
        "requires_prevent_self_review": True,
        "requires_exact_pr_pull_request_ci": True,
        "push_or_manual_ci_can_satisfy_g6": False,
        "dispatch_token_authority": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
        "errors": errors,
    }


def main() -> int:
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
