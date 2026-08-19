#!/usr/bin/env python3
from __future__ import annotations

"""Fail-closed static verifier for the bounded engineering autonomy handoff chain.

The verifier does not grant authority and performs no GitHub mutation. It proves
that one owner-issued bounded TaskRun authorization can mechanically continue
through the existing wakeup/G6/final-merge path without reintroducing routine
manual clicks, while preserving the true Human Gates documented by policy.
"""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "engineering-bounded-autonomy-closure@1"

WORKFLOWS = {
    "authorize": ".github/workflows/engineering-autonomy-authorize.yml",
    "autonomy_wakeup": ".github/workflows/engineering-autonomy-wakeup.yml",
    "solo_wakeup": ".github/workflows/engineering-solo-governance-wakeup.yml",
    "resume_wakeup": ".github/workflows/engineering-exact-head-resume-wakeup.yml",
    "authorized_merge": ".github/workflows/engineering-authorized-merge.yml",
}

REQUIRED: dict[str, tuple[str, ...]] = {
    "authorize": (
        "merge_policy:",
        "options:\n          - disabled\n          - bounded-auto-merge",
        "inputs.merge_policy == 'bounded-auto-merge'",
        "github.actor == github.repository_owner",
        "Compile independent bounded final merge grant",
        "Upload independent bounded merge grant",
        "steps.merge-grant.outputs.artifact_name",
    ),
    "autonomy_wakeup": (
        "workflows:\n      - engineering-autonomy-authorize",
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.actor.login == github.repository_owner",
        "Check durable dispatch idempotency marker",
        "Execute exactly one bounded network request",
    ),
    "solo_wakeup": (
        "workflows:\n      - governed-ci-repair-stage3",
        "Preserve true independent-review Human Gate",
        "independent_human_gate=true",
        "prevent_self_review",
        "engineering-merge-grant-${TASK_FP}",
        "Dispatch preauthorized solo G6 once",
        "gh workflow run governed-ci-repair-solo-governance.yml --ref main",
    ),
    "resume_wakeup": (
        "- quality",
        "- skill-self-validation",
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'pull_request'",
        "EXACT_HEAD_CI_AWAITING_APPROVAL",
        "engineering-merge-grant-${task_fp}",
        "gh workflow run governed-ci-repair-exact-head-resume.yml --ref main",
    ),
    "authorized_merge": (
        "- governed-ci-repair-governance",
        "- governed-ci-repair-solo-governance",
        "- governed-ci-repair-exact-head-resume",
        "Checkout trusted final-merge control plane",
        "ref: main",
        "Resolve owner-issued independent merge grant",
        "engineering-merge-grant-${TASK_FP}",
        "Evaluate exact final merge gate",
        "Mark exact governed PR Ready",
        "Re-read exact PR and compile CAS merge request",
        "Execute exact-head merge-commit request",
        '[[ "${expected_head}" == "${{ steps.g6.outputs.exact_head }}" && "${method}" == "merge" ]]',
    ),
}

FORBIDDEN_ANYWHERE = (
    "gh pr review --approve",
    "approve_workflow",
    "/approve",
    "/deployments",
    "production_closed=true",
)


def _read(root: Path, relative: str, errors: list[str]) -> str:
    path = root / relative
    if not path.is_file():
        errors.append(f"workflow_missing:{relative}")
        return ""
    return path.read_text(encoding="utf-8")


def _contains_shell_echo_value(source: str, label: str, value: str) -> bool:
    """Match the workflow source representation rather than rendered summary text."""

    escaped = f'{label}: \\`{value}\\`'
    plain = f"{label}: `{value}`"
    return escaped in source or plain in source


def verify(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    sources = {
        name: _read(root, relative, errors)
        for name, relative in WORKFLOWS.items()
    }

    for name, markers in REQUIRED.items():
        source = sources[name]
        for marker in markers:
            if marker not in source:
                errors.append(f"closure_contract_missing:{name}:{marker}")

    combined = "\n".join(sources.values()).lower()
    for marker in FORBIDDEN_ANYWHERE:
        if marker.lower() in combined:
            errors.append(f"forbidden_human_gate_bypass_or_production_authority:{marker}")

    # The final merge authority must be independent from the ordinary
    # AutonomyGrant and must remain exact-head/single-task scoped. These checks
    # inspect workflow source, where Markdown backticks are shell-escaped.
    authorize = sources["authorize"]
    merge = sources["authorized_merge"]
    if not _contains_shell_echo_value(authorize, "AutonomyGrant merge_allowed", "false"):
        errors.append("ordinary_autonomy_grant_merge_boundary_missing")
    if "Task merge grant found" not in merge:
        errors.append("final_merge_grant_observability_missing")
    if not _contains_shell_echo_value(merge, "Merge method", "merge"):
        errors.append("merge_method_observability_missing")
    if not _contains_shell_echo_value(merge, "Deploy", "false") or not _contains_shell_echo_value(
        merge, "Production", "false"
    ):
        errors.append("final_merge_production_boundary_missing")

    # A real independent-review policy must stop solo automation rather than be
    # converted into self-approval. An unavailable policy read is also a Human Gate.
    solo = sources["solo_wakeup"]
    if solo.count("independent_human_gate=true") < 2:
        errors.append("solo_human_gate_fail_closed_paths_incomplete")
    if "environment_inspection_unavailable=true" not in solo:
        errors.append("solo_policy_read_failure_not_human_gate")

    # Post-approval resume may observe only genuine successful PR workflow runs;
    # it cannot synthesize or invoke an approval endpoint.
    resume = sources["resume_wakeup"]
    if "action_required" in resume.lower():
        errors.append("resume_wakeup_must_not_treat_action_required_as_success")

    # The final workflow must Ready the exact governed Draft and re-read it before
    # compiling the CAS-bound merge request. Order is part of the contract.
    ready_index = merge.find("Mark exact governed PR Ready")
    reread_index = merge.find("Re-read exact PR and compile CAS merge request")
    execute_index = merge.find("Execute exact-head merge-commit request")
    if min(ready_index, reread_index, execute_index) < 0 or not ready_index < reread_index < execute_index:
        errors.append("ready_reread_merge_order_drift")

    errors = list(dict.fromkeys(errors))
    return {
        "schema": SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "routine_manual_clicks_required_after_bounded_owner_authorization": False if not errors else None,
        "bounded_merge_authority": "engineering-merge-grant@1",
        "ordinary_autonomy_grant_merge_allowed": False,
        "true_human_gates": [
            "goal_or_acceptance_change",
            "scope_or_privilege_expansion",
            "destructive_or_production_boundary",
            "independent_review_policy",
            "real_github_workflow_approval",
            "uncertain_or_conflicting_authority_state",
        ],
        "deploy_allowed": False,
        "production_closed": False,
        "errors": errors,
    }


def main() -> int:
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
