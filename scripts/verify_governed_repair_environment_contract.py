#!/usr/bin/env python3
from __future__ import annotations

"""Static fail-closed guard for the human G6 governance boundary."""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-repair-governance.yml"

REQUIRED_MARKERS = (
    "environment:\n      name: governed-repair-governance",
    "Verify protected governance environment policy",
    "environments/governed-repair-governance",
    '.type == "required_reviewers"',
    ".prevent_self_review",
    "any(. == true)",
    "governance environment must have required reviewers and prevent self-review",
    "environment:governed-repair-governance",
)
FORBIDDEN_MARKERS = (
    "gh pr merge",
    "gh pr review --approve",
    "production_closed=true",
)


def verify(root: Path = ROOT) -> dict[str, Any]:
    workflow = root / ".github" / "workflows" / "governed-ci-repair-governance.yml"
    errors: list[str] = []
    if not workflow.is_file():
        errors.append("governance_workflow_missing")
        text = ""
    else:
        text = workflow.read_text(encoding="utf-8")

    for marker in REQUIRED_MARKERS:
        if marker not in text:
            errors.append(f"governance_environment_contract_missing:{marker}")
    for marker in FORBIDDEN_MARKERS:
        if marker in text:
            errors.append(f"governance_environment_forbidden_authority:{marker}")

    # Human intent text is not authorization.  The workflow must describe the
    # dispatch string as acknowledgement while the protected Environment is the
    # actual execution gate.
    if "description: Type CLOSE_GOVERNANCE_AND_ACCEPT_BASELINE to acknowledge" not in text:
        errors.append("approval_token_still_claims_authorization_authority")

    return {
        "schema": "governed-repair-environment-contract@1",
        "status": "PASS" if not errors else "FAIL",
        "environment": "governed-repair-governance",
        "requires_required_reviewers": True,
        "requires_prevent_self_review": True,
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
