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

from governed_repair_contract import GATES, STATE_MACHINE, contract_fingerprint

ROOT = Path(__file__).resolve().parents[1]
MACHINE_FAILURE_SCHEMA = "machine-failure-envelope@1"
CANONICAL_CONTRACT_PATH = "scripts/governed_repair_contract.py"

REQUIRED_FILES = (
    CANONICAL_CONTRACT_PATH,
    "scripts/github_failure_ingest.py",
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

PROJECTION_FILES = tuple(path for path in REQUIRED_FILES if path != CANONICAL_CONTRACT_PATH)

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

REQUIRED_STATES = STATE_MACHINE
REQUIRED_GATES = GATES

FORBIDDEN_STAGE3_COMMANDS = (
    "gh pr merge",
    "/deployments",
    "gh workflow run deploy",
    "kubectl apply",
    "helm upgrade",
    "terraform apply",
)


def _text(path: str, *, root: Path) -> str:
    return (root / path).read_text(encoding="utf-8")


def _function_source(path: str, name: str, *, root: Path) -> str:
    source = _text(path, root=root)
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            if segment:
                return segment
    raise ValueError(f"missing function {name} in {path}")


def _workflow_job_block(source: str, job_name: str) -> str:
    lines = source.splitlines()
    marker = f"  {job_name}:"
    try:
        start = lines.index(marker)
    except ValueError:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            end = index
            break
    return "\n".join(lines[start:end])


def verify(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []

    for path in REQUIRED_FILES:
        if not (root / path).is_file():
            errors.append(f"required_file_missing:{path}")
    for path in FORBIDDEN_FILES:
        if (root / path).exists():
            errors.append(f"legacy_bypass_present:{path}")

    python_files = [path for path in REQUIRED_FILES if path.endswith(".py")]
    for path in python_files:
        if not (root / path).is_file():
            continue
        try:
            ast.parse(_text(path, root=root))
        except (OSError, SyntaxError) as exc:
            errors.append(f"python_parse_failure:{path}:{exc}")

    try:
        stage3_complete = _function_source(
            "scripts/github_repair_stage3.py",
            "complete_publication",
            root=root,
        )
        if "task.complete(" in stage3_complete:
            errors.append("stage3_pre_governance_task_complete_present")
        if "deprecated completion path" not in stage3_complete:
            errors.append("stage3_legacy_complete_not_fail_closed")
    except (OSError, SyntaxError, ValueError) as exc:
        errors.append(f"stage3_complete_guard_unverifiable:{exc}")

    try:
        stage3_workflow = _text(".github/workflows/governed-ci-repair-stage3.yml", root=root)
    except OSError as exc:
        errors.append(f"stage3_workflow_unreadable:{exc}")
        stage3_workflow = ""
    if "github_repair_stage3_record_publication.py" not in stage3_workflow:
        errors.append("stage3_publication_not_governance_handoff")
    if "generate_product_source_baseline.py" in stage3_workflow:
        errors.append("stage3_has_pre_governance_baseline_refresh")
    stage3_low = stage3_workflow.casefold()
    for command in FORBIDDEN_STAGE3_COMMANDS:
        if command in stage3_low:
            errors.append(f"stage3_forbidden_authority:{command}")

    for job_name in ("inspect", "validate"):
        block = _workflow_job_block(stage3_workflow, job_name).casefold()
        if not block:
            errors.append(f"stage3_job_missing:{job_name}")
        elif "contents: write" in block or "pull-requests: write" in block:
            errors.append(f"stage3_readonly_job_has_write_authority:{job_name}")

    publish_block = _workflow_job_block(stage3_workflow, "publish")
    publish_low = publish_block.casefold()
    if not publish_block:
        errors.append("stage3_job_missing:publish")
    else:
        for required in (
            "needs: [inspect, validate]",
            "if: needs.validate.result == 'success'",
            "contents: write",
            "pull-requests: write",
            "github_repair_stage3_publish.py",
            "github_repair_stage3_record_publication.py",
            "gh pr create --draft",
        ):
            if required.casefold() not in publish_low:
                errors.append(f"stage3_publish_contract_missing:{required}")
        for forbidden in (
            "gh pr merge",
            "generate_product_source_baseline.py",
            "github_repair_baseline_acceptance.py",
            "github_repair_governance.py",
            "github_repair_exact_head.py",
            "task.complete(",
        ):
            if forbidden.casefold() in publish_low:
                errors.append(f"stage3_publish_forbidden_authority:{forbidden}")

    try:
        governance_workflow = _text(
            ".github/workflows/governed-ci-repair-governance.yml", root=root
        )
    except OSError as exc:
        errors.append(f"governance_workflow_unreadable:{exc}")
        governance_workflow = ""
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

    try:
        authority = _text("scripts/github_repair_authority.py", root=root)
    except OSError as exc:
        errors.append(f"authority_unreadable:{exc}")
        authority = ""
    for marker in (
        "from governed_repair_contract import",
        "PREWRITE_STATES",
        "PROTECTED_AUTHORITY",
        "contract_fingerprint",
        '"lifecycle_contract_sha256"',
        '"write_scope_mode": "exact_allowlist"',
    ):
        if marker not in authority:
            errors.append(f"authority_marker_missing:{marker}")
    if "PREWRITE_STATES = (" in authority:
        errors.append("authority_duplicates_canonical_prewrite_states")

    rca = _text("scripts/github_repair_rca.py", root=root) if (root / "scripts/github_repair_rca.py").is_file() else ""
    for marker in (
        '"read_only": True',
        '"workspace_mutated": False',
        "write_scope_recommendation",
        "workspace_fingerprint_before",
        "workspace_fingerprint_after",
    ):
        if marker not in rca:
            errors.append(f"rca_marker_missing:{marker}")

    orchestrator = _text("scripts/github_repair_orchestrator.py", root=root) if (root / "scripts/github_repair_orchestrator.py").is_file() else ""
    for marker in (
        "validate_write_grant(",
        "STAGE2_WRITE_REVOKED_REPLAN_REQUIRED",
        "same_deterministic_failure_signature_twice",
        "ARCHITECTURE_REPLAN_AND_NEW_RCA",
        "required_guard_ids",
        "revoke_write_grant(",
    ):
        if marker not in orchestrator:
            errors.append(f"orchestrator_marker_missing:{marker}")

    ingest = _text("scripts/github_failure_ingest.py", root=root) if (root / "scripts/github_failure_ingest.py").is_file() else ""
    for condition in REQUIRED_TASK_CONDITIONS:
        if f'"{condition}"' not in ingest:
            errors.append(f"task_condition_missing:{condition}")
    if MACHINE_FAILURE_SCHEMA not in ingest:
        errors.append("machine_failure_envelope_not_supported")
    if '"protected_baseline_drift"' not in ingest:
        errors.append("protected_baseline_drift_not_classified")

    aggregate = "\n".join(
        _text(path, root=root)
        for path in PROJECTION_FILES
        if (root / path).is_file()
    )
    if "permanent_guard_not_reverified" not in aggregate:
        errors.append("permanent_guard_reverification_missing")
    for state in REQUIRED_STATES:
        if state not in aggregate:
            errors.append(f"canonical_state_not_projected:{state}")
    for gate in REQUIRED_GATES:
        if gate not in aggregate:
            errors.append(f"canonical_gate_not_projected:{gate}")

    exact = _text("scripts/github_repair_exact_head.py", root=root) if (root / "scripts/github_repair_exact_head.py").is_file() else ""
    for marker in (
        'MANDATORY_WORKFLOWS = ("skill-self-validation", "quality")',
        '"merge_allowed": False',
        '"deploy_allowed": False',
        '"production_closed": False',
    ):
        if marker not in exact:
            errors.append(f"exact_head_marker_missing:{marker}")

    baseline = _text("scripts/github_repair_baseline_acceptance.py", root=root) if (root / "scripts/github_repair_baseline_acceptance.py").is_file() else ""
    if "observed_drift != approved" not in baseline:
        errors.append("baseline_exact_drift_equality_missing")
    if 'changed.splitlines() != [BASELINE_PATH]' not in baseline:
        errors.append("baseline_single_file_write_guard_missing")

    return {
        "schema": "governed-repair-architecture-verification@1",
        "status": "PASS" if not errors else "FAIL",
        "canonical_contract_sha256": contract_fingerprint(),
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
