#!/usr/bin/env python3
from __future__ import annotations

"""Fail-closed contract for adopting a pre-governance Draft candidate.

The trusted profile binds the exact candidate blobs and fixed guards.  Adoption
has zero candidate write authority.  Full Python verification must delegate to
the repository's single standard-suite owner instead of duplicating its runtime
environment.  Test evidence is written outside the candidate worktree.
"""

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "governance/adoption-profiles/release56-dependency-basis.json"
CONTROLLER = "scripts/github_existing_candidate_adoption.py"
PYTHON_SUITE_RUNNER = "scripts/run_python_test_suites.py"
INGEST_BRIDGE = "scripts/github_failure_ingest_control_plane.py"
ADOPTION_WORKFLOW = ".github/workflows/governed-ci-existing-candidate-adoption.yml"
SOLO_WORKFLOW = ".github/workflows/governed-ci-existing-candidate-solo-governance.yml"

EXPECTED_RELEASE56_BLOBS = {
    "contracts/generated/dependency-basis-projections.json": "c25682abc81775c31c5b542a9dbf1698e50645cd",
    "scripts/verify_dependency_basis_contract.py": "a9b9f40710f4a96cfcdf394065aa290c6f55a6fb",
    "scripts/verify_dependency_basis_mutation_proof.py": "0da7d1af9d88b295838a1d24969b19b49125be40",
    "services/agent-service/src/agent_core/goal_graph/dependency_basis_contract.py": "6c3c45f9d80fd4e7d13c87f439987b02011506f3",
    "services/agent-service/src/agent_core/lifecycle/goal_planning.py": "09e279d24641b3f7a5df7d4d0811bc6c267faa07",
    "services/agent-service/tests/runtime/test_release56_dependency_basis_contract.py": "11cd67e1ade8de78f05f8af9fb95628c90c3e7b1",
    "skill-system/profiles/skill-unit.json": "9c8ba0bd94def7f4e8902ad63f2995003f429458",
    "skill-system/tests/test_dependency_basis_contract_guard.py": "f4ef23af7fbc33645d98ebea1310fad289f088c6",
}

TARGETED_RUNTIME_ARGV = [
    "/usr/bin/env", "PYTHONPATH=src", "PYTHONNOUSERSITE=1", "{agent_python}",
    "-B", "-m", "pytest", "-q", "-ra", "-p", "no:cacheprovider",
    "tests/runtime/test_release56_dependency_basis_contract.py",
]

CANONICAL_SUITE_ARGV = [
    "/usr/bin/env",
    "-u", "QUALITY_AGENT_PYTHON",
    "-u", "QUALITY_BUSINESS_PYTHON",
    "PYTHONNOUSERSITE=1",
    "{python}", "-B",
    "../control/scripts/run_python_test_suites.py", ".",
    "--mode", "standard",
    "--junit-dir", "../adoption/profile-runtime/junit",
    "--coverage-dir", "../adoption/profile-runtime/coverage",
]


class ContractError(RuntimeError):
    pass


def _text(path: str, root: Path) -> str:
    return (root / path).read_text(encoding="utf-8")


def _json(path: str, root: Path) -> dict[str, Any]:
    value = json.loads(_text(path, root))
    if not isinstance(value, dict):
        raise ContractError(f"JSON object required: {path}")
    return value


def _command_map(profile: dict[str, Any], errors: list[str]) -> dict[str, dict[str, Any]]:
    rows = profile.get("verification_commands")
    if not isinstance(rows, list):
        errors.append("profile_verification_commands_missing")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            errors.append("profile_verification_command_not_object")
            continue
        command_id = str(row.get("id") or "")
        if not command_id or command_id in result:
            errors.append("profile_verification_command_id_invalid_or_duplicate")
            continue
        result[command_id] = row
    return result


def verify(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    for path in (
        PROFILE, CONTROLLER, PYTHON_SUITE_RUNNER, INGEST_BRIDGE,
        ADOPTION_WORKFLOW, SOLO_WORKFLOW,
    ):
        if not (root / path).is_file():
            errors.append(f"required_file_missing:{path}")

    for path in (CONTROLLER, PYTHON_SUITE_RUNNER, INGEST_BRIDGE):
        if not (root / path).is_file():
            continue
        try:
            ast.parse(_text(path, root))
        except (OSError, SyntaxError) as exc:
            errors.append(f"python_parse_failure:{path}:{exc}")

    try:
        profile = _json(PROFILE, root)
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        errors.append(f"profile_unreadable:{exc}")
        profile = {}

    expected_profile = {
        "schema": "governed-existing-candidate-adoption-profile@1",
        "profile_id": "release56-dependency-basis",
        "source_pr_number": 1348,
        "base_branch": "main",
        "authority_effect": False,
        "existing_candidate_only": True,
        "production_closed": False,
    }
    for key, value in expected_profile.items():
        if profile.get(key) != value:
            errors.append(f"profile_binding_drift:{key}")
    if profile.get("allowed_changed_files") != EXPECTED_RELEASE56_BLOBS:
        errors.append("profile_exact_blob_allowlist_drift")
    if profile.get("required_guard_ids") != [
        "dependency-basis-contract",
        "dependency-basis-contract-mutation-proof",
        "python-test-suites",
    ]:
        errors.append("profile_required_guard_drift")
    if "skill-system/registry/product-source-baseline.json" not in set(profile.get("forbidden_changed_exact") or []):
        errors.append("profile_baseline_not_forbidden")
    if not {".github/", "governance/", "deployment/"}.issubset(set(profile.get("forbidden_changed_prefixes") or [])):
        errors.append("profile_control_plane_prefix_not_forbidden")

    commands = _command_map(profile, errors)
    targeted = commands.get("dependency-basis-runtime-regression")
    if not isinstance(targeted, dict) or targeted.get("cwd") != "services/agent-service" or targeted.get("argv") != TARGETED_RUNTIME_ARGV:
        errors.append("profile_targeted_runtime_environment_drift")
    suite = commands.get("python-test-suites")
    if not isinstance(suite, dict) or suite.get("cwd") != "." or suite.get("argv") != CANONICAL_SUITE_ARGV:
        errors.append("profile_python_suite_authority_drift")
    if isinstance(suite, dict):
        argv = [str(item) for item in suite.get("argv") or []]
        if "-m" in argv and "pytest" in argv:
            errors.append("profile_python_suite_bypasses_canonical_runner")
        if "../control/scripts/run_python_test_suites.py" not in argv:
            errors.append("profile_python_suite_not_control_owned")
        if "../adoption/profile-runtime/junit" not in argv or "../adoption/profile-runtime/coverage" not in argv:
            errors.append("profile_python_suite_evidence_not_external")
        if "--mode" not in argv or "standard" not in argv:
            errors.append("profile_python_suite_not_standard_mode")
        for override in ("QUALITY_AGENT_PYTHON", "QUALITY_BUSINESS_PYTHON"):
            if override not in argv:
                errors.append(f"profile_python_suite_override_not_cleared:{override}")

    try:
        runner = _text(PYTHON_SUITE_RUNNER, root)
    except OSError as exc:
        errors.append(f"python_suite_runner_unreadable:{exc}")
        runner = ""
    for marker in (
        "STANDARD_CONFIG_PREFIXES",
        "STANDARD_ENV = {",
        '"APP_PROFILE": "local"',
        '"PYTHONPATH"] = "src:."',
        'selector = "integration" if args.mode == "integration" else "not integration and not preprod"',
        '"skip_policy": "selected tests must not skip; integration tests are excluded rather than skipped in standard mode"',
    ):
        if marker not in runner:
            errors.append(f"python_suite_runner_contract_missing:{marker}")

    try:
        controller = _text(CONTROLLER, root)
    except OSError as exc:
        errors.append(f"controller_unreadable:{exc}")
        controller = ""
    for marker in (
        'PROFILE_SCHEMA = "governed-existing-candidate-adoption-profile@1"',
        'AUTHORITY_SCHEMA = "governed-existing-candidate-no-write-authority@1"',
        'PUBLICATION_SCHEMA = "github-governed-repair-draft-publication@1"',
        '"write_authority_effect": False',
        '"source_writes_allowed": False',
        '"test_or_oracle_writes_allowed": False',
        '"workflow_writes_allowed": False',
        '"baseline_writes_allowed": False',
        '"merge_allowed": False',
        '"deploy_allowed": False',
        '"production_closed": False',
        '"candidate_origin": "existing_pr_adoption"',
        'blob = _git(workspace, "rev-parse", f"HEAD:{path}")',
        'set(actual_paths) != set(expected)',
        '"G6_GOVERNANCE_EXACT_HEAD": {"status": "PENDING"',
    ):
        if marker not in controller:
            errors.append(f"controller_authority_marker_missing:{marker}")
    for forbidden in (
        '"git", "apply"', '"git", "commit"', '"git", "push"',
        "gh pr merge", "gh pr ready",
        "github_repair_baseline_acceptance.py", "github_repair_governance.py",
    ):
        if forbidden in controller:
            errors.append(f"controller_forbidden_authority:{forbidden}")

    try:
        ingest = _text(INGEST_BRIDGE, root)
    except OSError as exc:
        errors.append(f"ingest_bridge_unreadable:{exc}")
        ingest = ""
    for marker in (
        "BASELINE_TEST_MARKER", "BASELINE_COUNT_ASSERTION",
        '"failure_kind": "protected_baseline_drift"',
        '"implicated_paths": []',
        '"scripts/github_existing_candidate_adoption.py"',
    ):
        if marker not in ingest:
            errors.append(f"baseline_classifier_marker_missing:{marker}")

    try:
        adoption = _text(ADOPTION_WORKFLOW, root)
    except OSError as exc:
        errors.append(f"adoption_workflow_unreadable:{exc}")
        adoption = ""
    adoption_low = adoption.casefold()
    for marker in (
        "issue_comment:", "github.actor == github.repository_owner", "/governed-adopt",
        "ref: main", "path: control", "path: candidate", "mkdir -p adoption",
        "cd candidate/services/agent-service && uv sync --locked --all-groups",
        "cd ../business-service && uv sync --locked --all-groups",
        "github_existing_candidate_adoption.py inspect",
        "github_existing_candidate_adoption.py run-profile",
        "id: fixed_profile_validation",
        "failure() && steps.fixed_profile_validation.outcome == 'failure'",
        "profile-failure-summary.json", "profile_validation_present", '"diagnostic_only": True',
        "governed-ci-existing-candidate-adoption-failure-",
        "github_existing_candidate_adoption.py finalize",
        "SKILL_JUDGE_ROOT: ${{ github.workspace }}/control",
        "SKILL_JUDGE_TRUST_MODE: external-readonly",
        "governed-ci-existing-candidate-adoption-published-",
        ".write_authority_effect == false", ".merge_allowed == false",
        ".deploy_allowed == false", ".production_closed == false",
    ):
        if marker.casefold() not in adoption_low:
            errors.append(f"adoption_workflow_marker_missing:{marker}")
    failure_condition = "failure() && steps.fixed_profile_validation.outcome == 'failure'"
    if adoption_low.count(failure_condition.casefold()) != 2:
        errors.append("adoption_failure_status_check_count_drift")
    for forbidden in (
        "contents: write", "pull-requests: write", "continue-on-error",
        "github_repair_baseline_acceptance.py", "github_repair_governance.py",
        "gh pr merge", "gh pr ready", "git push",
    ):
        if forbidden.casefold() in adoption_low:
            errors.append(f"adoption_workflow_forbidden_authority:{forbidden}")

    try:
        solo = _text(SOLO_WORKFLOW, root)
    except OSError as exc:
        errors.append(f"solo_workflow_unreadable:{exc}")
        solo = ""
    solo_low = solo.casefold()
    for marker in (
        "CLOSE_SOLO_GOVERNANCE_AND_ACCEPT_BASELINE", "GITHUB_ACTOR", "GITHUB_REPOSITORY_OWNER",
        '"governed-ci-existing-candidate-adoption"', "governed-ci-existing-candidate-adoption-published-",
        '.candidate_origin == "existing_pr_adoption"', ".write_authority_effect == false",
        "github_repair_governance.py", "github_repair_baseline_acceptance.py",
        "verify_product_source_baseline.py", "github_repair_exact_head.py",
        '.event == "pull_request"', ".pr_is_draft == true",
    ):
        if marker.casefold() not in solo_low:
            errors.append(f"solo_workflow_marker_missing:{marker}")
    if solo.find("github_repair_governance.py") > solo.find("github_repair_baseline_acceptance.py"):
        errors.append("solo_baseline_precedes_governance")
    if solo.find("github_repair_baseline_acceptance.py") > solo.find("github_repair_exact_head.py"):
        errors.append("solo_exact_head_precedes_baseline")
    for forbidden in ("gh pr merge", "gh pr ready", "kubectl apply", "helm upgrade", "terraform apply"):
        if forbidden.casefold() in solo_low:
            errors.append(f"solo_workflow_forbidden_authority:{forbidden}")

    suite_errors = [item for item in errors if item.startswith("profile_python_suite_")]
    return {
        "schema": "governed-existing-candidate-adoption-contract@4",
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "profile_id": profile.get("profile_id"),
        "source_pr_number": profile.get("source_pr_number"),
        "exact_blob_count": len(profile.get("allowed_changed_files") or {}),
        "candidate_write_authority": False,
        "targeted_runtime_import_environment_bound": "profile_targeted_runtime_environment_drift" not in errors,
        "canonical_python_suite_authority_bound": not suite_errors,
        "canonical_python_suite_owner": PYTHON_SUITE_RUNNER,
        "python_suite_evidence_outside_candidate": not any("evidence_not_external" in item for item in errors),
        "failed_profile_evidence_required": True,
        "failed_profile_evidence_diagnostic_only": True,
        "failed_profile_can_be_converted_to_success": False,
        "baseline_before_governance_allowed": False,
        "automatic_merge_allowed": False,
        "production_closed": False,
    }


def main() -> int:
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
