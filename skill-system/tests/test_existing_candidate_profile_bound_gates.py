from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "scripts" / "github_existing_candidate_adoption.py"
STAGE9_PROFILE = ROOT / "governance" / "adoption-profiles" / "stage9-transaction-authority-reconciliation.json"
LEGACY_PROFILE = ROOT / "governance" / "adoption-profiles" / "release56-dependency-basis.json"


def _load_controller():
    spec = importlib.util.spec_from_file_location("github_existing_candidate_adoption_under_test", CONTROLLER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load existing-candidate adoption controller")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _profile(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_stage9_profile_binds_semantic_gates_to_stage9_guards() -> None:
    module = _load_controller()
    profile = _profile(STAGE9_PROFILE)
    module._validate_profile(profile)
    assert profile["source_pr_number"] == 1710
    assert len(profile["allowed_changed_files"]) == 22
    assert module._gate_guard_ids(profile) == {
        "G1_CONTRACT_PROJECTION": [
            "transaction-authority-contract",
            "runtime-authority-foundations",
        ],
        "G2_SEMANTIC_INVARIANT": ["transaction-recovery-projection"],
        "G3_MUTATION": [
            "transaction-authority-adversarial",
            "python-test-suites",
        ],
    }


def test_nonlegacy_profile_cannot_omit_gate_guard_binding() -> None:
    module = _load_controller()
    profile = _profile(STAGE9_PROFILE)
    profile.pop("gate_guard_ids")
    with pytest.raises(module.AdoptionError, match="gate_guard_ids are required"):
        module._validate_profile(profile)


def test_profile_cannot_claim_unknown_guard_as_gate_evidence() -> None:
    module = _load_controller()
    profile = _profile(STAGE9_PROFILE)
    profile["gate_guard_ids"]["G2_SEMANTIC_INVARIANT"] = ["not-executed"]
    with pytest.raises(module.AdoptionError, match="unknown verification commands"):
        module._validate_profile(profile)


def test_permanent_guard_cannot_be_orphaned_from_semantic_gates() -> None:
    module = _load_controller()
    profile = _profile(STAGE9_PROFILE)
    profile["gate_guard_ids"]["G1_CONTRACT_PROJECTION"] = ["transaction-authority-contract"]
    with pytest.raises(module.AdoptionError, match="permanent guards must be represented"):
        module._validate_profile(profile)


def test_g3_must_keep_canonical_python_suite_guard() -> None:
    module = _load_controller()
    profile = _profile(STAGE9_PROFILE)
    profile["gate_guard_ids"]["G3_MUTATION"] = ["transaction-authority-adversarial"]
    with pytest.raises(module.AdoptionError):
        module._validate_profile(profile)


def test_semantic_gate_never_projects_pass_from_failed_named_guard() -> None:
    module = _load_controller()
    profile = _profile(STAGE9_PROFILE)
    command_status = {
        "transaction-authority-contract": "PASS",
        "runtime-authority-foundations": "PASS",
        "transaction-recovery-projection": "FAIL",
        "transaction-authority-adversarial": "PASS",
    }
    with pytest.raises(module.AdoptionError, match="semantic gate guard not PASS"):
        module._semantic_gates(
            profile,
            command_status=command_status,
            quick_statuses={"python-test-suites": "PASS"},
        )


def test_semantic_gate_evidence_is_profile_bound_not_dependency_hardcoded() -> None:
    module = _load_controller()
    profile = _profile(STAGE9_PROFILE)
    gates = module._semantic_gates(
        profile,
        command_status={
            "transaction-authority-contract": "PASS",
            "runtime-authority-foundations": "PASS",
            "transaction-recovery-projection": "PASS",
            "transaction-authority-adversarial": "PASS",
        },
        quick_statuses={"python-test-suites": "PASS"},
    )
    evidence = [item for gate in gates.values() for item in gate["evidence"]]
    assert "guard:transaction-recovery-projection" in evidence
    assert "guard:dependency-basis-runtime-regression" not in evidence
    assert "invariant:TX-AUTHORITY-SINGLE-SOURCE-001" in evidence


def test_legacy_release56_profile_keeps_its_exact_existing_gate_mapping() -> None:
    module = _load_controller()
    profile = _profile(LEGACY_PROFILE)
    module._validate_profile(profile)
    assert module._gate_guard_ids(profile) == {
        "G1_CONTRACT_PROJECTION": ["dependency-basis-contract"],
        "G2_SEMANTIC_INVARIANT": ["dependency-basis-runtime-regression"],
        "G3_MUTATION": [
            "dependency-basis-contract-mutation-proof",
            "python-test-suites",
        ],
    }
