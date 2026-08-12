from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import project_convergence_controller as controller  # noqa: E402

REF = "abc123"
CATALOG_PATH = controller.DEFAULT_REQUIREMENTS_PATH


def _catalog(*, profile: str = "project-quick") -> dict:
    return {
        "schema_version": 2,
        "profiles": {profile: ["REQ-A", "REQ-B"]},
        "requirements": [
            {"id": "REQ-A", "risk": "P0", "statement": "A must hold", "owner": "owner-a"},
            {"id": "REQ-B", "risk": "P1", "statement": "B must hold", "owner": "owner-b"},
        ],
    }


def _summary(*, profile: str = "project-quick") -> dict:
    return {
        "schema_version": 6,
        "run_kind": "verification",
        "mode": profile.removeprefix("project-"),
        "requirement_profile": profile,
        "requirement_catalog": CATALOG_PATH,
        "target_identity": {"change_ref": REF},
        "required_gate_ids": ["gate-a", "gate-b"],
        "results": [
            {"id": "gate-a", "status": "PASS", "owner": "owner-a"},
            {"id": "gate-b", "status": "PASS", "owner": "owner-b"},
        ],
        "claim_results": [
            {
                "id": "REQ-A",
                "status": "VERIFIED",
                "risk": "P0",
                "gate_statuses": {"gate-a": "PASS"},
                "environment_blocked_gates": [],
                "evidence_refs": ["test:a"],
            },
            {
                "id": "REQ-B",
                "status": "VERIFIED",
                "risk": "P1",
                "gate_statuses": {"gate-b": "PASS"},
                "environment_blocked_gates": [],
                "evidence_refs": ["test:b"],
            },
        ],
        "decision": "PASS",
        "loop_status": "CI_VERIFIED",
        "completion_eligible": True,
        "workspace_snapshot_fingerprint": "workspace-fp",
        "claim_manifest_fingerprint": "claim-fp",
        "requirement_catalog_fingerprint": "catalog-fp",
    }


def _assess(summary: dict, catalog: dict, *, profile: str = "project-quick"):
    return controller.assess(
        summary=summary,
        catalog=catalog,
        profile=profile,
        expected_ref=REF,
        catalog_path=CATALOG_PATH,
    )


def test_quick_clean_advances_without_claiming_project_convergence() -> None:
    state, findings = _assess(_summary(), _catalog())
    assert state["status"] == "ITERATION_CLEAN"
    assert state["next_profile"] == "project-integration"
    assert state["project_converged"] is False
    assert state["production_closed"] is False
    assert state["open_finding_count"] == 0
    assert findings["findings"] == []


def test_release_clean_is_the_only_project_converged_status() -> None:
    profile = "project-release"
    state, _ = _assess(_summary(profile=profile), _catalog(profile=profile), profile=profile)
    assert state["status"] == "CONVERGED"
    assert state["project_converged"] is True
    assert state["next_action"] == "DONE"
    assert state["production_closed"] is False
    assert state["release_authorized"] is False


def test_failed_requirement_becomes_governed_repair_finding() -> None:
    summary = _summary()
    summary["decision"] = "FAIL"
    summary["loop_status"] = "CI_FAILED"
    summary["completion_eligible"] = False
    summary["results"][0]["status"] = "FAIL"
    summary["claim_results"][0]["status"] = "UNVERIFIED"
    summary["claim_results"][0]["gate_statuses"]["gate-a"] = "FAIL"
    state, findings = _assess(summary, _catalog())
    assert state["status"] == "REPAIR_REQUIRED"
    assert state["next_action"] == "WAIT_GOVERNED_REPAIR"
    assert state["next_finding"]["requirement_id"] == "REQ-A"
    assert state["next_finding"]["disposition"] == "GOVERNED_REPAIR"
    assert findings["findings"][0]["failed_gates"] == ["gate-a"]


def test_missing_active_requirement_evidence_requires_replan_not_source_guessing() -> None:
    summary = _summary()
    summary["claim_results"] = [
        row for row in summary["claim_results"] if row["id"] != "REQ-A"
    ]
    state, findings = _assess(summary, _catalog())
    assert state["status"] == "REPLAN_REQUIRED"
    assert state["next_action"] == "REPLAN_REQUIREMENTS"
    finding = findings["findings"][0]
    assert finding["kind"] == "REQUIREMENT_EVIDENCE_MISSING"
    assert finding["disposition"] == "REPLAN_REQUIRED"


def test_environment_block_does_not_route_to_source_repair() -> None:
    summary = _summary()
    summary["decision"] = "BLOCKED_BY_ENVIRONMENT"
    summary["loop_status"] = "BLOCKED_BY_ENVIRONMENT"
    summary["completion_eligible"] = False
    summary["results"][0]["status"] = "BLOCKED_BY_ENVIRONMENT"
    summary["claim_results"][0]["status"] = "UNVERIFIED"
    summary["claim_results"][0]["gate_statuses"]["gate-a"] = "BLOCKED_BY_ENVIRONMENT"
    summary["claim_results"][0]["environment_blocked_gates"] = ["gate-a"]
    state, findings = _assess(summary, _catalog())
    assert state["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert state["next_action"] == "RETRY_ASSESSMENT"
    assert findings["findings"][0]["disposition"] == "RETRY_ENVIRONMENT"


def test_unmapped_required_gate_failure_is_not_hidden_by_verified_claims() -> None:
    summary = _summary()
    summary["decision"] = "FAIL"
    summary["loop_status"] = "CI_FAILED"
    summary["completion_eligible"] = False
    summary["results"][1]["status"] = "FAIL"
    state, findings = _assess(summary, _catalog())
    assert state["status"] == "REPAIR_REQUIRED"
    assert any(
        row["kind"] == "UNMAPPED_REQUIRED_GATE_FAILURE"
        for row in findings["findings"]
    )


def test_stale_summary_ref_is_rejected_fail_closed() -> None:
    with pytest.raises(controller.ConvergenceError, match="stale or foreign Quality evidence"):
        controller.assess(
            summary=_summary(),
            catalog=_catalog(),
            profile="project-quick",
            expected_ref="different-ref",
            catalog_path=CATALOG_PATH,
        )


def test_catalog_profile_cannot_omit_its_requirement_definition() -> None:
    catalog = _catalog()
    catalog["requirements"] = catalog["requirements"][:1]
    with pytest.raises(controller.ConvergenceError, match="profile references missing requirements"):
        _assess(_summary(), catalog)
