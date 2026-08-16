#!/usr/bin/env python3
from __future__ import annotations

"""Static fail-closed guard for governed-repair G6 authorization modes.

Multi-user mode uses a protected GitHub Environment with required reviewers and
prevent-self-review. Solo-owner mode is intentionally weaker only on independent
human review: it requires an explicit repository-owner acknowledgement, preserves
all machine gates and exact-PR/exact-SHA CI, and must leave the PR Draft. The two
modes are separate workflows so solo operation cannot silently weaken the
multi-user environment boundary.
"""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MULTI_WORKFLOW_REL = ".github/workflows/governed-ci-repair-governance.yml"
SOLO_WORKFLOW_REL = ".github/workflows/governed-ci-repair-solo-governance.yml"
EXACT_HEAD_REL = "scripts/github_repair_exact_head.py"

MULTI_REQUIRED_MARKERS = (
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

SOLO_REQUIRED_MARKERS = (
    "CLOSE_SOLO_GOVERNANCE_AND_ACCEPT_BASELINE",
    "Require explicit repository-owner acknowledgement",
    "GITHUB_ACTOR: ${{ github.actor }}",
    "GITHUB_REPOSITORY_OWNER: ${{ github.repository_owner }}",
    'if [[ "${GITHUB_ACTOR}" != "${GITHUB_REPOSITORY_OWNER}" ]]; then',
    "solo governance may only be acknowledged by the repository owner",
    "solo-owner-explicit-acknowledgement",
    "github_repair_governance.py",
    "github_repair_baseline_acceptance.py",
    "verify_product_source_baseline.py",
    "github_repair_exact_head.py",
    'governance_mode:"solo_owner"',
    "independent_human_review:false",
    "machine_gates_remain_mandatory:true",
    "pr_remains_draft:true",
    "Finalize G6 but keep solo-owner PR Draft",
    '.state == "OPEN" and .isDraft == true and .headRefOid == $sha',
    '.event == "pull_request"',
    ".pull_requests[]?",
    "pr_number:($pr_number|tonumber)",
    '.workflows.quality.event == "pull_request"',
    '.workflows["skill-self-validation"].event == "pull_request"',
    "no independent human review claim",
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

GLOBAL_FORBIDDEN_MARKERS = (
    "gh pr merge",
    "gh pr review --approve",
    "production_closed=true",
    "|| true",
)

SOLO_FORBIDDEN_MARKERS = (
    "environment:\n      name: governed-repair-governance",
    "gh pr ready",
)


def _read(root: Path, relative: str, missing_error: str, errors: list[str]) -> str:
    path = root / relative
    if not path.is_file():
        errors.append(missing_error)
        return ""
    return path.read_text(encoding="utf-8")


def _require_markers(
    *, source: str, markers: tuple[str, ...], prefix: str, errors: list[str]
) -> None:
    for marker in markers:
        if marker not in source:
            errors.append(f"{prefix}:{marker}")


def verify(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    multi = _read(root, MULTI_WORKFLOW_REL, "multi_user_governance_workflow_missing", errors)
    solo = _read(root, SOLO_WORKFLOW_REL, "solo_owner_governance_workflow_missing", errors)
    exact_head = _read(root, EXACT_HEAD_REL, "exact_head_controller_missing", errors)

    _require_markers(
        source=multi,
        markers=MULTI_REQUIRED_MARKERS,
        prefix="multi_user_governance_contract_missing",
        errors=errors,
    )
    _require_markers(
        source=solo,
        markers=SOLO_REQUIRED_MARKERS,
        prefix="solo_owner_governance_contract_missing",
        errors=errors,
    )
    _require_markers(
        source=exact_head,
        markers=EXACT_HEAD_REQUIRED_MARKERS,
        prefix="exact_pr_ci_contract_missing",
        errors=errors,
    )

    for marker in GLOBAL_FORBIDDEN_MARKERS:
        if marker in multi or marker in solo or marker in exact_head:
            errors.append(f"governance_forbidden_authority:{marker}")
    for marker in SOLO_FORBIDDEN_MARKERS:
        if marker in solo:
            errors.append(f"solo_owner_forbidden_authority:{marker}")

    # Human intent text is not authorization. In multi-user mode the dispatch
    # string is only acknowledgement; the protected Environment authorizes.
    if "description: Type CLOSE_GOVERNANCE_AND_ACCEPT_BASELINE to acknowledge" not in multi:
        errors.append("multi_user_approval_token_still_claims_authorization_authority")

    # Solo mode must explicitly state that the owner acknowledgement is not an
    # independent-human-review claim and must not make the PR Ready automatically.
    if "independent_human_review:false" not in solo:
        errors.append("solo_owner_independence_disclaimer_missing")
    if "gh pr ready" in solo:
        errors.append("solo_owner_pr_auto_ready_present")

    for mode, workflow in (("multi_user", multi), ("solo_owner", solo)):
        if workflow.count('.event == "pull_request"') < 2:
            errors.append(f"{mode}_exact_pr_ci_pull_request_filter_incomplete")
        if workflow.count(".pull_requests[]?") < 2:
            errors.append(f"{mode}_exact_pr_ci_pr_number_filter_incomplete")
        governance_index = workflow.find("github_repair_governance.py")
        baseline_index = workflow.find("github_repair_baseline_acceptance.py")
        exact_index = workflow.find("github_repair_exact_head.py")
        if min(governance_index, baseline_index, exact_index) < 0:
            errors.append(f"{mode}_g6_transition_order_unverifiable")
        elif not governance_index < baseline_index < exact_index:
            errors.append(f"{mode}_g6_transition_order_drift")

    owner_check = solo.find('if [[ "${GITHUB_ACTOR}" != "${GITHUB_REPOSITORY_OWNER}" ]]; then')
    solo_close = solo.find("github_repair_governance.py")
    if owner_check < 0 or solo_close < 0 or owner_check > solo_close:
        errors.append("solo_owner_acknowledgement_not_before_governance_close")

    errors = list(dict.fromkeys(errors))
    return {
        "schema": "governed-repair-environment-contract@3",
        "status": "PASS" if not errors else "FAIL",
        "multi_user": {
            "environment": "governed-repair-governance",
            "requires_required_reviewers": True,
            "requires_prevent_self_review": True,
            "independent_human_review": True,
        },
        "solo_owner": {
            "requires_repository_owner": True,
            "requires_explicit_acknowledgement": True,
            "independent_human_review": False,
            "machine_gates_remain_mandatory": True,
            "pr_remains_draft": True,
        },
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
