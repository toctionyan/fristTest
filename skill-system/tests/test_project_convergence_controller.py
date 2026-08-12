from __future__ import annotations

import copy
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
PROFILE_REQUIREMENTS = {
    "project-quick": ["REQ-A", "REQ-B"],
    "project-integration": ["REQ-A", "REQ-B", "REQ-C"],
    "project-product": ["REQ-A", "REQ-B", "REQ-C", "REQ-D"],
    "project-release": ["REQ-A", "REQ-B", "REQ-C", "REQ-D", "REQ-E"],
}
PROFILE_OWN_CLAIMS = {
    "project-quick": ["REQ-A", "REQ-B"],
    "project-integration": ["REQ-C"],
    "project-product": ["REQ-D"],
    "project-release": ["REQ-E"],
}


def _catalog() -> dict:
    return {
        "schema_version": 2,
        "profiles": copy.deepcopy(PROFILE_REQUIREMENTS),
        "requirements": [
            {
                "id": requirement_id,
                "risk": "P0" if requirement_id == "REQ-A" else "P1",
                "statement": f"{requirement_id} must hold",
                "owner": f"owner-{requirement_id.lower()}",
            }
            for requirement_id in ["REQ-A", "REQ-B", "REQ-C", "REQ-D", "REQ-E"]
        ],
    }


def _summary(*, profile: str = "project-quick") -> dict:
    own_claims = PROFILE_OWN_CLAIMS[profile]
    gate_ids = [f"gate-{claim_id.lower()}" for claim_id in own_claims]
    claims = [
        {
            "id": claim_id,
            "status": "VERIFIED",
            "risk": "P1",
            "gate_statuses": {f"gate-{claim_id.lower()}": "PASS"},
            "environment_blocked_gates": [],
            "evidence_refs": [f"test:{claim_id.lower()}"],
        }
        for claim_id in own_claims
    ]
    if profile != "project-quick":
        claims.insert(
            0,
            {
                "id": f"{profile.upper()}-CUMULATIVE-BASE",
                "status": "VERIFIED",
                "risk": "P1",
                "gate_statuses": {gate_ids[0]: "PASS"},
                "environment_blocked_gates": [],
                "evidence_refs": ["gate-log:cumulative-base"],
            },
        )
    return {
        "schema_version": 6,
        "run_kind": "verification",
        "mode": profile.removeprefix("project-"),
        "requirement_profile": profile,
        "requirement_catalog": CATALOG_PATH,
        "target_identity": {"change_ref": REF},
        "required_gate_ids": gate_ids,
        "results": [
            {"id": gate_id, "status": "PASS", "owner": "quality-owner"}
            for gate_id in gate_ids
        ],
        "claim_results": claims,
        "decision": "PASS",
        "loop_status": "CI_VERIFIED",
        "completion_eligible": True,
        "workspace_snapshot_fingerprint": "workspace-fp",
        "claim_manifest_fingerprint": f"claim-fp-{profile}",
        "requirement_catalog_fingerprint": "catalog-fp",
    }


def _assess_quick(summary: dict | None = None):
    return controller.assess(
        summary=summary or _summary(),
        catalog=_catalog(),
        profile="project-quick",
        expected_ref=REF,
        catalog_path=CATALOG_PATH,
    )


def _assess_cumulative(profile: str, summaries: list[dict]):
    return controller.assess_cumulative(
        summaries=summaries,
        catalog=_catalog(),
        profile=profile,
        expected_ref=REF,
        catalog_path=CATALOG_PATH,
    )


def test_quick_clean_advances_without_claiming_project_convergence() -> None:
    state, findings = _assess_quick()
    assert state["status"] == "ITERATION_CLEAN"
    assert state["next_profile"] == "project-integration"
    assert state["project_converged"] is False
    assert state["production_closed"] is False
    assert state["open_finding_count"] == 0
    assert findings["findings"] == []


def test_integration_combines_same_ref_quick_and_incremental_claims() -> None:
    state, findings = _assess_cumulative(
        "project-integration",
        [_summary(profile="project-quick"), _summary(profile="project-integration")],
    )
    assert state["status"] == "INTEGRATION_CLEAN"
    assert state["next_profile"] == "project-release"
    assert state["verified_requirement_count"] == 3
    assert [row["profile"] for row in state["quality"]["evidence_profiles"]] == [
        "project-quick",
        "project-integration",
    ]
    assert findings["findings"] == []


def test_integration_without_quick_evidence_is_rejected_not_reported_as_many_missing_claims() -> None:
    with pytest.raises(controller.ConvergenceError, match="missing profiles"):
        _assess_cumulative(
            "project-integration",
            [_summary(profile="project-integration")],
        )


def test_cumulative_profiles_must_describe_one_workspace_snapshot() -> None:
    integration = _summary(profile="project-integration")
    integration["workspace_snapshot_fingerprint"] = "other-workspace"
    with pytest.raises(controller.ConvergenceError, match="one immutable workspace snapshot"):
        _assess_cumulative("project-integration", [_summary(), integration])


def test_release_clean_is_the_only_project_converged_status() -> None:
    summaries = [
        _summary(profile=profile)
        for profile in (
            "project-quick",
            "project-integration",
            "project-product",
            "project-release",
        )
    ]
    state, _ = _assess_cumulative("project-release", summaries)
    assert state["status"] == "CONVERGED"
    assert state["project_converged"] is True
    assert state["next_action"] == "DONE"
    assert state["production_closed"] is False
    assert state["release_authorized"] is False


def test_failed_incremental_requirement_becomes_governed_repair_finding() -> None:
    integration = _summary(profile="project-integration")
    gate_id = "gate-req-c"
    claim = next(row for row in integration["claim_results"] if row["id"] == "REQ-C")
    integration["decision"] = "FAIL"
    integration["loop_status"] = "CI_FAILED"
    integration["completion_eligible"] = False
    integration["results"][0]["status"] = "FAIL"
    claim["status"] = "UNVERIFIED"
    claim["gate_statuses"][gate_id] = "FAIL"
    state, findings = _assess_cumulative("project-integration", [_summary(), integration])
    assert state["status"] == "REPAIR_REQUIRED"
    assert state["next_action"] == "WAIT_GOVERNED_REPAIR"
    assert state["next_finding"]["requirement_id"] == "REQ-C"
    assert state["next_finding"]["evidence_profile"] == "project-integration"
    assert state["next_finding"]["disposition"] == "GOVERNED_REPAIR"
    assert findings["findings"][0]["failed_gates"] == [gate_id]


def test_missing_active_requirement_evidence_requires_replan_not_source_guessing() -> None:
    summary = _summary()
    summary["claim_results"] = [
        row for row in summary["claim_results"] if row["id"] != "REQ-A"
    ]
    state, findings = _assess_quick(summary)
    assert state["status"] == "REPLAN_REQUIRED"
    assert state["next_action"] == "REPLAN_REQUIREMENTS"
    finding = findings["findings"][0]
    assert finding["kind"] == "REQUIREMENT_EVIDENCE_MISSING"
    assert finding["disposition"] == "REPLAN_REQUIRED"


def test_environment_block_does_not_route_to_source_repair() -> None:
    summary = _summary()
    gate_id = "gate-req-a"
    claim = next(row for row in summary["claim_results"] if row["id"] == "REQ-A")
    summary["decision"] = "BLOCKED_BY_ENVIRONMENT"
    summary["loop_status"] = "BLOCKED_BY_ENVIRONMENT"
    summary["completion_eligible"] = False
    summary["results"][0]["status"] = "BLOCKED_BY_ENVIRONMENT"
    claim["status"] = "UNVERIFIED"
    claim["gate_statuses"][gate_id] = "BLOCKED_BY_ENVIRONMENT"
    claim["environment_blocked_gates"] = [gate_id]
    state, findings = _assess_quick(summary)
    assert state["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert state["next_action"] == "RETRY_ASSESSMENT"
    assert findings["findings"][0]["disposition"] == "RETRY_ENVIRONMENT"


def test_unmapped_required_gate_failure_is_not_hidden_by_verified_claims() -> None:
    summary = _summary()
    summary["required_gate_ids"].append("unmapped-gate")
    summary["results"].append(
        {"id": "unmapped-gate", "status": "FAIL", "owner": "quality-owner"}
    )
    summary["decision"] = "FAIL"
    summary["loop_status"] = "CI_FAILED"
    summary["completion_eligible"] = False
    state, findings = _assess_quick(summary)
    assert state["status"] == "REPAIR_REQUIRED"
    assert any(
        row["kind"] == "UNMAPPED_REQUIRED_GATE_FAILURE"
        for row in findings["findings"]
    )


def test_stale_summary_ref_in_any_cumulative_layer_is_rejected_fail_closed() -> None:
    integration = _summary(profile="project-integration")
    integration["target_identity"]["change_ref"] = "different-ref"
    with pytest.raises(controller.ConvergenceError, match="stale or foreign Quality evidence"):
        _assess_cumulative("project-integration", [_summary(), integration])


def test_catalog_profile_cannot_omit_its_requirement_definition() -> None:
    catalog = _catalog()
    catalog["requirements"] = [
        row for row in catalog["requirements"] if row["id"] != "REQ-B"
    ]
    with pytest.raises(controller.ConvergenceError, match="profile references missing requirements"):
        controller.assess(
            summary=_summary(),
            catalog=catalog,
            profile="project-quick",
            expected_ref=REF,
            catalog_path=CATALOG_PATH,
        )
