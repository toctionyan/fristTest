#!/usr/bin/env python3
from __future__ import annotations

"""Mechanical architecture gate for governed repair.

This gate protects the authority/state-machine design from future prompt or code
refactors. It does not prove product semantics; it proves that repair cannot
silently regain write, baseline, merge, deployment, or completion authority by
bypassing the governed RCA/G0-G6 path.
"""

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MACHINE_FAILURE_SCHEMA = "machine-failure-envelope@1"

REQUIRED_FILES = (
    "scripts/github_repair_authority.py",
    "scripts/github_repair_rca.py",
    "scripts/github_repair_orchestrator_control_plane.py",
    "scripts/github_repair_orchestrator.py",
    "scripts/github_agent_fixer.py",
    "scripts/github_stage2_handoff.py",
    "scripts/github_repair_stage3.py",
    "scripts/github_repair_stage3_tree.py",
    "scripts/github_repair_stage3_publish.py",
    "scripts/github_repair_stage3_record_publication.py",
    "scripts/github_repair_governance.py",
    "scripts/github_repair_baseline_acceptance.py",
    "scripts/github_repair_exact_head.py",
    "scripts/verify_product_source_baseline.py",
    ".github/workflows/governed-ci-repair-stage3.yml",
    ".github/workflows/governed-ci-repair-governance.yml",
)

FORBIDDEN_FILES = (
    "scripts/github_repair_stage3_complete.py",
    ".github/workflows/b28-product-baseline-refresh.yml",
)

REQUIRED_TASK_CONDITIONS = (
    "failure_ingested",
    "classification_complete",
    "source_changed",
    "validation_passed",
    "draft_pr_published",
    "governance_closed",
    "baseline_accepted",
    "exact_head_certified",
    "ready_for_review",
)

REQUIRED_STATES = (
    "EVIDENCE_FROZEN",
    "RCA_READ_ONLY",
    "WRITE_GRANTED",
    "PATCHING",
    "LOCAL_VERIFICATION",
    "INDEPENDENT_REVIEW",
    "PR_CERTIFICATION",
    "GOVERNANCE_REQUIRED",
    "GOVERNANCE_CLOSED",
    "BASELINE_ACCEPTED",
    "READY_FOR_REVIEW",
)

REQUIRED_GATES = (
    "G0_SCOPE_AUTHORITY",
    "G1_CONTRACT_PROJECTION",
    "G2_SEMANTIC_INVARIANT",
    "G3_MUTATION",
    "G4_FINAL_AUTHORITY",
    "G5_INTEGRATION_CERTIFICATION",
    "G6_GOVERNANCE_EXACT_HEAD",
)

# Match concrete shell-level authority, not incidental path fragments such as
# deployment/ci/uv-requirements-*.txt. workflow_dispatch itself is not deploy
# authority; a deploy/merge command or write permission in Stage-3 is.
FORBIDDEN_STAGE3_COMMANDS = (
    "gh pr merge",
    "/deployments",
    "gh workflow run deploy",
    "kubectl apply",
    "helm upgrade",
    "terraform apply",
)


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _python_functions(path: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    source = _text(path)
    tree = ast.parse(source)
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _function_source(path: str, name: str) -> str:
    source = _text(path)
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            if segment:
                return segment
    raise ValueError(f"missing function {name} in {path}")


def verify() -> dict[str, Any]:
    errors: list[str] = []

    for path in REQUIRED_FILES:
        if not (ROOT / path).is_file():
            errors.append(f"required_file_missing:{path}")
    for path in FORBIDDEN_FILES:
        if (ROOT / path).exists():
            errors.append(f"legacy_bypass_present:{path}")

    python_files = [path for path in REQUIRED_FILES if path.endswith(".py")]
    for path in python_files:
        try:
            ast.parse(_text(path))
        except (OSError, SyntaxError) as exc:
            errors.append(f"python_parse_failure:{path}:{exc}")

    try:
        stage3_complete = _function_source(
            "scripts/github_repair_stage3.py",
            "complete_publication",
        )
        if "task.complete(" in stage3_complete:
            errors.append("stage3_pre_governance_task_complete_present")
        if "deprecated completion path" not in stage3_complete:
            errors.append("stage3_legacy_complete_not_fail_closed")
    except (OSError, SyntaxError, ValueError) as exc:
        errors.append(f"stage3_complete_guard_unverifiable:{exc}")

    stage3_workflow = _text(".github/workflows/governed-ci-repair-stage3.yml")
    if "github_repair_stage3_record_publication.py" not in stage3_workflow:
        errors.append("stage3_publication_not_governance_handoff")
    if "generate_product_source_baseline.py" in stage3_workflow:
        errors.append("stage3_has_pre_governance_baseline_refresh")
    stage3_low = stage3_workflow.casefold()
    for command in FORBIDDEN_STAGE3_COMMANDS:
        if command in stage3_low:
            errors.append(f"stage3_forbidden_authority:{command}")
    if "contents: write" in stage3_low:
        errors.append("stage3_has_contents_write_authority")

    governance_workflow = _text(".github/workflows/governed-ci-repair-governance.yml")
    for required in (
        "CLOSE_GOVERNANCE_AND_ACCEPT_BASELINE",
        "github_repair_governance.py",
        "github_repair_baseline_acceptance.py",
        "verify_product_source_baseline.py",
        "github_repair_exact_head.py",
        "gh pr ready",
    ):
        if required not in governance_workflow:
            errors.append(f"governance_workflow_missing:{required}")
    for forbidden in ("|| true", "gh pr merge", "gh pr review --approve"):
        if forbidden in governance_workflow:
            errors.append(f"governance_workflow_forbidden:{forbidden}")
    if governance_workflow.find("github_repair_governance.py") > governance_workflow.find(
        "github_repair_baseline_acceptance.py"
    ):
        errors.append("baseline_acceptance_precedes_governance")
    if governance_workflow.find("github_repair_baseline_acceptance.py") > governance_workflow.find(
        "github_repair_exact_head.py"
    ):
        errors.append("exact_head_precedes_baseline_acceptance")

    authority = _text("scripts/github_repair_authority.py")
    for marker in (
        '"write_scope_mode": "exact_allowlist"',
        '"scope_expansion_allowed": False',
        '"baseline_write_allowed": False',
        '"merge_allowed": False',
        '"deploy_allowed": False',
        '"production_close_allowed": False',
    ):
        if marker not in authority:
            errors.append(f"authority_marker_missing:{marker}")

    rca = _text("scripts/github_repair_rca.py")
    for marker in (
        '"read_only": True',
        '"workspace_mutated": False',
        "write_scope_recommendation",
        "workspace_fingerprint_before",
        "workspace_fingerprint_after",
    ):
        if marker not in rca:
            errors.append(f"rca_marker_missing:{marker}")

    orchestrator = _text("scripts/github_repair_orchestrator.py")
    for marker in (
        "validate_write_grant(",
        "STAGE2_WRITE_REVOKED_REPLAN_REQUIRED",
        "same_deterministic_failure_signature_twice",
        "ARCHITECTURE_REPLAN_AND_NEW_RCA",
        "revoke_write_grant(",
    ):
        if marker not in orchestrator:
            errors.append(f"orchestrator_marker_missing:{marker}")

    ingest = _text("scripts/github_failure_ingest.py")
    for condition in REQUIRED_TASK_CONDITIONS:
        if f'"{condition}"' not in ingest:
            errors.append(f"task_condition_missing:{condition}")
    if MACHINE_FAILURE_SCHEMA not in ingest:
        errors.append("machine_failure_envelope_not_supported")
    if '"protected_baseline_drift"' not in ingest:
        errors.append("protected_baseline_drift_not_classified")

    aggregate = "\n".join(
        _text(path)
        for path in REQUIRED_FILES
        if (ROOT / path).is_file()
    )
    for state in REQUIRED_STATES:
        if state not in aggregate:
            errors.append(f"state_missing:{state}")
    for gate in REQUIRED_GATES:
        if gate not in aggregate:
            errors.append(f"gate_missing:{gate}")

    exact = _text("scripts/github_repair_exact_head.py")
    for marker in (
        'MANDATORY_WORKFLOWS = ("skill-self-validation", "quality")',
        '"merge_allowed": False',
        '"deploy_allowed": False',
        '"production_closed": False',
    ):
        if marker not in exact:
            errors.append(f"exact_head_marker_missing:{marker}")

    baseline = _text("scripts/github_repair_baseline_acceptance.py")
    if "observed_drift != approved" not in baseline:
        errors.append("baseline_exact_drift_equality_missing")
    if 'changed.splitlines() != [BASELINE_PATH]' not in baseline:
        errors.append("baseline_single_file_write_guard_missing")

    return {
        "schema": "governed-repair-architecture-verification@1",
        "status": "PASS" if not errors else "FAIL",
        "required_file_count": len(REQUIRED_FILES),
        "forbidden_file_count": len(FORBIDDEN_FILES),
        "required_task_conditions": list(REQUIRED_TASK_CONDITIONS),
        "required_states": list(REQUIRED_STATES),
        "required_gates": list(REQUIRED_GATES),
        "errors": errors,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }


def main() -> int:
    try:
        result = verify()
    except (OSError, SyntaxError, ValueError) as exc:
        result = {
            "schema": "governed-repair-architecture-verification@1",
            "status": "FAIL",
            "errors": [str(exc)],
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
