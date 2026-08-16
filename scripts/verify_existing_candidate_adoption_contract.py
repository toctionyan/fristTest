#!/usr/bin/env python3
from __future__ import annotations

"""Fail-closed static contract for migration of pre-governance Draft candidates.

The adoption path may certify an *already existing* candidate only when a trusted
main-branch profile binds its exact PR, file set and Git blob identities.  It must
never become an alternate repair writer, baseline shortcut, merge path or way to
let candidate-owned tests authorize themselves.
"""

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "governance/adoption-profiles/release56-dependency-basis.json"
CONTROLLER = "scripts/github_existing_candidate_adoption.py"
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
    "skill-system/profiles/skill-unit.json": "74c583dfc40003df1abacdccdb0fd7b39f564bd3",
    "skill-system/tests/test_dependency_basis_contract_guard.py": "f4ef23af7fbc33645d98ebea1310fad289f088c6",
}


class ContractError(RuntimeError):
    pass


def _text(path: str, root: Path) -> str:
    return (root / path).read_text(encoding="utf-8")


def _json(path: str, root: Path) -> dict[str, Any]:
    value = json.loads(_text(path, root))
    if not isinstance(value, dict):
        raise ContractError(f"JSON object required: {path}")
    return value


def verify(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    for path in (PROFILE, CONTROLLER, INGEST_BRIDGE, ADOPTION_WORKFLOW, SOLO_WORKFLOW):
        if not (root / path).is_file():
            errors.append(f"required_file_missing:{path}")

    for path in (CONTROLLER, INGEST_BRIDGE):
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
    required_guards = profile.get("required_guard_ids")
    if required_guards != [
        "dependency-basis-contract",
        "dependency-basis-contract-mutation-proof",
        "python-test-suites",
    ]:
        errors.append("profile_required_guard_drift")
    if "skill-system/registry/product-source-baseline.json" not in set(
        profile.get("forbidden_changed_exact") or []
    ):
        errors.append("profile_baseline_not_forbidden")
    prefixes = set(profile.get("forbidden_changed_prefixes") or [])
    if not {".github/", "governance/", "deployment/"}.issubset(prefixes):
        errors.append("profile_control_plane_prefix_not_forbidden")

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
        '"git", "apply"',
        '"git", "commit"',
        '"git", "push"',
        "gh pr merge",
        "gh pr ready",
        "github_repair_baseline_acceptance.py",
        "github_repair_governance.py",
    ):
        if forbidden in controller:
            errors.append(f"controller_forbidden_authority:{forbidden}")

    try:
        ingest = _text(INGEST_BRIDGE, root)
    except OSError as exc:
        errors.append(f"ingest_bridge_unreadable:{exc}")
        ingest = ""
    for marker in (
        "BASELINE_TEST_MARKER",
        "BASELINE_COUNT_ASSERTION",
        '"failure_kind": "protected_baseline_drift"',
        '"implicated_paths": []',
        "existing-candidate adoption",
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
        "issue_comment:",
        "github.actor == github.repository_owner",
        "/governed-adopt",
        "ref: main",
        "github_existing_candidate_adoption.py inspect",
        "github_existing_candidate_adoption.py run-profile",
        "github_existing_candidate_adoption.py finalize",
        "SKILL_JUDGE_ROOT: ${{ github.workspace }}/control",
        "SKILL_JUDGE_TRUST_MODE: external-readonly",
        "governed-ci-existing-candidate-adoption-published-",
        ".write_authority_effect == false",
        ".merge_allowed == false",
        ".deploy_allowed == false",
        ".production_closed == false",
    ):
        if marker.casefold() not in adoption_low:
            errors.append(f"adoption_workflow_marker_missing:{marker}")
    for forbidden in (
        "contents: write",
        "pull-requests: write",
        "github_repair_baseline_acceptance.py",
        "github_repair_governance.py",
        "gh pr merge",
        "gh pr ready",
        "git push",
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
        "CLOSE_SOLO_GOVERNANCE_AND_ACCEPT_BASELINE",
        "GITHUB_ACTOR",
        "GITHUB_REPOSITORY_OWNER",
        '"governed-ci-existing-candidate-adoption"',
        "governed-ci-existing-candidate-adoption-published-",
        ".candidate_origin == "existing_pr_adoption"",
        ".write_authority_effect == false",
        "github_repair_governance.py",
        "github_repair_baseline_acceptance.py",
        "verify_product_source_baseline.py",
        "github_repair_exact_head.py",
        ".event == \"pull_request\"",
        ".pr_is_draft == true",
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

    return {
        "schema": "governed-existing-candidate-adoption-contract@1",
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "profile_id": profile.get("profile_id"),
        "source_pr_number": profile.get("source_pr_number"),
        "exact_blob_count": len(profile.get("allowed_changed_files") or {}),
        "candidate_write_authority": False,
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
