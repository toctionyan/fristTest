from __future__ import annotations

from hashlib import sha256
import inspect
import json

from agent_core.goal_graph.cutover_gate import (
    DEPENDENCY_CUTOVER_GRANT_AUTHORITY,
    DEPENDENCY_CUTOVER_GRANT_VERSION,
    LEGACY_DEPENDENCY_AUTHORITY,
    TYPED_DEPENDENCY_AUTHORITY,
    build_dependency_authority_rollback_contract,
    dependency_authority_rollback_integrity,
    dependency_cutover_gate_integrity,
    evaluate_dependency_cutover_gate,
)
from agent_core.goal_graph.dependency_authority import build_dependency_authority_attestation


_IDENTITY_FIELDS = (
    "semantic_contract_id",
    "semantic_digest",
    "typed_graph_id",
    "typed_graph_digest",
    "typed_coverage_digest",
    "capability_registry_version",
    "completion_snapshot_digest",
)


def _digest(value: dict) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _attestation(*, completed_goal_ids: tuple[str, ...] = ()) -> dict:
    shadow = {
        "version": "typed-dependency-authority-shadow@1",
        "authority": "audit_only_current_dependency_enforcement_unchanged",
        "status": "MATCHED",
        "current_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "candidate_dependency_authority": TYPED_DEPENDENCY_AUTHORITY,
        "typed_coverage_status": "COMPLETE",
        "typed_dataflow_status": "GOAL_GRAPH_DATAFLOW_CLOSED",
        "typed_coverage_digest": "c" * 64,
        "typed_graph_id": "goal-graph:1:abc",
        "typed_graph_digest": "g" * 64,
        "evidence_errors": [],
        "comparisons": [],
        "divergence_codes": [],
        "cutover_eligible": True,
        "cutover_performed": False,
        "changes_current_dependency_blocking": False,
        "changes_allowed_capability_tools": False,
        "blocks_execution": False,
        "creates_permit": False,
        "mutates_semantics": False,
        "mutates_business_state": False,
    }
    shadow["shadow_digest"] = _digest(shadow)
    return build_dependency_authority_attestation(
        dependency_shadow=shadow,
        semantic_contract_id="semantic:1:abc",
        semantic_digest="s" * 64,
        capability_registry_version="registry@1",
        completed_goal_ids=completed_goal_ids,
    )


def _identity(attestation: dict) -> dict:
    return {field: attestation[field] for field in _IDENTITY_FIELDS}


def _grant(attestation: dict, *, expires_at: float = 2000.0) -> dict:
    grant = {
        "version": DEPENDENCY_CUTOVER_GRANT_VERSION,
        "authority": DEPENDENCY_CUTOVER_GRANT_AUTHORITY,
        "immutable": True,
        "status": "GRANTED",
        "external_authority_verified": True,
        "grant_id": "grant:stage4b:test",
        "issued_by": "governance:test",
        "attestation_digest": attestation["attestation_digest"],
        **_identity(attestation),
        "expires_at": expires_at,
    }
    grant["grant_digest"] = _digest(grant)
    return grant


def _gate(
    attestation: dict,
    *,
    grant: dict | None = None,
    current_identity: dict | None = None,
    evaluation_time: float | None = 1000.0,
) -> dict:
    return evaluate_dependency_cutover_gate(
        attestation=attestation,
        grant=grant,
        current_identity=current_identity if current_identity is not None else _identity(attestation),
        evaluation_time=evaluation_time,
    )


def test_no_grant_stays_blocked_on_legacy_authority() -> None:
    attestation = _attestation()
    gate = _gate(attestation)

    assert gate["status"] == "BLOCKED"
    assert gate["cutover_candidate_ready"] is False
    assert gate["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert gate["runtime_activation_authority_granted"] is False
    assert gate["cutover_performed"] is False
    assert gate["creates_permit"] is False
    assert any(code.startswith("GRANT:") for code in gate["errors"])
    assert dependency_cutover_gate_integrity(gate)["ok"] is True


def test_exact_external_grant_can_only_make_an_inert_candidate_ready() -> None:
    attestation = _attestation()
    gate = _gate(attestation, grant=_grant(attestation))

    assert gate["status"] == "CANDIDATE_READY"
    assert gate["cutover_candidate_ready"] is True
    assert gate["grant_shape_and_binding_accepted"] is True
    assert gate["candidate_dependency_authority"] == TYPED_DEPENDENCY_AUTHORITY
    assert gate["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert gate["runtime_activation_authority_granted"] is False
    assert gate["cutover_performed"] is False
    assert gate["changes_current_dependency_blocking"] is False
    assert gate["changes_allowed_capability_tools"] is False
    assert gate["errors"] == []


def test_tampered_grant_digest_fails_closed() -> None:
    attestation = _attestation()
    grant = _grant(attestation)
    grant["issued_by"] = "attacker"

    gate = _gate(attestation, grant=grant)

    assert gate["status"] == "BLOCKED"
    assert "GRANT:CUTOVER_GRANT_DIGEST_INVALID" in gate["errors"]
    assert gate["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY


def test_expired_grant_fails_closed() -> None:
    attestation = _attestation()
    gate = _gate(
        attestation,
        grant=_grant(attestation, expires_at=1000.0),
        evaluation_time=1000.0,
    )

    assert gate["status"] == "BLOCKED"
    assert "CUTOVER_GRANT_EXPIRED" in gate["errors"]
    assert gate["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY


def test_missing_evaluation_time_cannot_make_candidate_ready() -> None:
    attestation = _attestation()
    gate = _gate(attestation, grant=_grant(attestation), evaluation_time=None)

    assert gate["status"] == "BLOCKED"
    assert "CUTOVER_EVALUATION_TIME_REQUIRED" in gate["errors"]


def test_tampered_attestation_is_rejected_even_with_matching_grant_shape() -> None:
    attestation = _attestation()
    grant = _grant(attestation)
    attestation["typed_graph_digest"] = "0" * 64

    gate = _gate(attestation, grant=grant, current_identity=_identity(attestation))

    assert gate["status"] == "BLOCKED"
    assert "ATTESTATION:ATTESTATION_DIGEST_INVALID" in gate["errors"]
    assert gate["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY


def test_current_graph_identity_drift_fails_closed() -> None:
    attestation = _attestation()
    current = _identity(attestation)
    current["typed_graph_digest"] = "1" * 64

    gate = _gate(attestation, grant=_grant(attestation), current_identity=current)

    assert gate["status"] == "BLOCKED"
    assert "CURRENT_TYPED_GRAPH_DIGEST_MISMATCH" in gate["errors"]


def test_current_registry_identity_drift_fails_closed() -> None:
    attestation = _attestation()
    current = _identity(attestation)
    current["capability_registry_version"] = "registry@2"

    gate = _gate(attestation, grant=_grant(attestation), current_identity=current)

    assert gate["status"] == "BLOCKED"
    assert "CURRENT_CAPABILITY_REGISTRY_VERSION_MISMATCH" in gate["errors"]


def test_completion_snapshot_drift_fails_closed() -> None:
    original = _attestation()
    later = _attestation(completed_goal_ids=("g1",))
    current = _identity(original)
    current["completion_snapshot_digest"] = later["completion_snapshot_digest"]

    gate = _gate(original, grant=_grant(original), current_identity=current)

    assert gate["status"] == "BLOCKED"
    assert "CURRENT_COMPLETION_SNAPSHOT_DIGEST_MISMATCH" in gate["errors"]


def test_grant_bound_to_a_different_attestation_is_rejected() -> None:
    first = _attestation()
    second = _attestation(completed_goal_ids=("g1",))
    grant = _grant(first)

    gate = _gate(second, grant=grant)

    assert gate["status"] == "BLOCKED"
    assert "CUTOVER_GRANT_ATTESTATION_MISMATCH" in gate["errors"]
    assert "CUTOVER_GRANT_COMPLETION_SNAPSHOT_DIGEST_MISMATCH" in gate["errors"]


def test_explicit_rollback_contract_targets_legacy_without_mutating_runtime() -> None:
    attestation = _attestation()
    gate = _gate(attestation, grant=_grant(attestation))
    rollback = build_dependency_authority_rollback_contract(
        gate=gate,
        rollback_requested=True,
        reason_code="OPERATOR_REVERT",
    )

    assert rollback["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert rollback["reversion_target"] == LEGACY_DEPENDENCY_AUTHORITY
    assert rollback["would_revert_if_typed_active"] is True
    assert rollback["runtime_reversion_required_now"] is False
    assert rollback["reversion_performed"] is False
    assert rollback["mutates_runtime_authority"] is False
    assert rollback["reason_codes"] == ["OPERATOR_REVERT"]
    assert dependency_authority_rollback_integrity(rollback)["ok"] is True


def test_tampered_gate_drives_fail_closed_reversion_contract() -> None:
    attestation = _attestation()
    gate = _gate(attestation, grant=_grant(attestation))
    gate["selected_runtime_dependency_authority"] = TYPED_DEPENDENCY_AUTHORITY

    rollback = build_dependency_authority_rollback_contract(gate=gate)

    assert rollback["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert rollback["reversion_target"] == LEGACY_DEPENDENCY_AUTHORITY
    assert rollback["would_revert_if_typed_active"] is True
    assert any(code.startswith("CUTOVER_GATE:") for code in rollback["reason_codes"])
    assert dependency_authority_rollback_integrity(rollback)["ok"] is True


def test_stage4b_gate_module_has_no_runtime_or_domain_import_authority() -> None:
    import agent_core.goal_graph.cutover_gate as module

    source = inspect.getsource(module)
    assert "agent_core.lifecycle" not in source
    assert "agent_core.runtime" not in source
    assert "agent_modules" not in source
    assert "dispatch" not in source.casefold()
    assert "permit_created" not in source
