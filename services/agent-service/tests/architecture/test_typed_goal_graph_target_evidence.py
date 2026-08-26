from __future__ import annotations

from copy import deepcopy

import pytest

from agent_core.goal_graph import (
    LEGACY_TARGET_EVIDENCE_VERSION,
    TARGET_EVIDENCE_VERSION,
    target_evidence_proof_payload,
    validate_target_evidence,
    validate_target_evidence_versions,
)
from agent_core.goal_graph.contracts import canonical_digest


SCOPE = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-1"}
SEMANTIC_DIGEST = "a" * 64


def _source(variant: str, cardinality: str) -> dict:
    if variant == "historical_visible_result":
        return {
            "result_ref": "view:orders:1",
            "member_handles": ["artifact:order-1"],
            "reference_kind": "customer_visible",
        }
    if variant == "deterministic_target_resolver_projection":
        projection = {"kind": "verified_member_selection", "member_handle": "artifact:order-1"}
        return {
            "source_evidence_ref": "proof:visible-orders-1",
            "projection": projection,
            "projection_digest": canonical_digest(projection),
        }
    return {
        "goal_output_ref": "goal-output:orders-1",
        "artifact_handle": "artifact:order-1",
        "execution_trace_ref": "trace:turn-8-read-1",
        "permit_ref": "permit:turn-8-read-1",
        "match_proof_ref": "match-proof:turn-8-read-1",
        "producer_goal_id": "g1",
        "producer_output_id": "order.lookup",
    }


def _evidence(variant: str = "historical_visible_result", *, cardinality: str = "exactly_one") -> dict:
    evidence = {
        "version": TARGET_EVIDENCE_VERSION,
        "variant": variant,
        "resource_type": "order",
        "logical_type_name": "VerifiedOrder",
        "cardinality": cardinality,
        "scope": deepcopy(SCOPE),
        "semantic_contract_id": "semantic:8:contract",
        "semantic_digest": SEMANTIC_DIGEST,
        "authority": {
            "historical_visible_result": "runtime_visible_result_ref",
            "deterministic_target_resolver_projection": "runtime_target_resolver_projection",
            "same_turn_verified_capability_output": "runtime_capability_output_issuer",
        }[variant],
        "issuer": {
            "historical_visible_result": "runtime_visible_result_ref",
            "deterministic_target_resolver_projection": "runtime_target_resolver_projection",
            "same_turn_verified_capability_output": "runtime_capability_output_issuer",
        }[variant],
        "issuer_attestation": "proof:runtime-attestation-v2",
        "issued_at": 1000.0,
        "expires_at": 2000.0,
        "evaluated_at": 1500.0,
        "proof_ref": "proof:target-evidence-v2",
        "source": _source(variant, cardinality),
    }
    evidence["proof_digest"] = canonical_digest(target_evidence_proof_payload(evidence))
    return evidence


@pytest.mark.parametrize("variant", sorted({
    "historical_visible_result",
    "deterministic_target_resolver_projection",
    "same_turn_verified_capability_output",
}))
def test_each_target_variant_requires_runtime_issuer_and_can_be_ready(variant: str) -> None:
    evidence = _evidence(variant)

    result = validate_target_evidence(
        evidence,
        expected_scope=SCOPE,
        expected_resource_type="order",
        expected_logical_type_name="VerifiedOrder",
        expected_cardinality="exactly_one",
        expected_semantic_contract_id="semantic:8:contract",
        expected_semantic_digest=SEMANTIC_DIGEST,
        evaluation_time=1500.0,
        issuer_validator=lambda row: row["issuer_attestation"] == "proof:runtime-attestation-v2",
    )

    assert result["ok"] is True
    assert result["status"] == "READY"
    assert result["trusted_issuer"] is True


def test_same_turn_without_runtime_owned_issuer_never_becomes_ready() -> None:
    result = validate_target_evidence(
        _evidence("same_turn_verified_capability_output"),
        expected_scope=SCOPE,
        evaluation_time=1500.0,
    )

    assert result["ok"] is False
    assert result["readiness"] == "DIAGNOSTIC_ONLY"
    assert any(row["code"] == "SAME_TURN_TRUSTED_ISSUER_UNAVAILABLE" for row in result["errors"])


def test_unknown_fields_and_mixed_variant_fields_fail_closed() -> None:
    evidence = _evidence()
    evidence["source"]["permit_ref"] = "permit:forged"
    evidence["unexpected"] = "business payload"

    result = validate_target_evidence(evidence, expected_scope=SCOPE)

    codes = {row["code"] for row in result["errors"]}
    assert "TARGET_EVIDENCE_UNKNOWN_FIELD" in codes
    assert "TARGET_EVIDENCE_UNKNOWN_SOURCE_FIELD" in codes
    assert result["ok"] is False


@pytest.mark.parametrize("cardinality", ["none", "one", "unknown"])
def test_target_evidence_cardinality_is_strict(cardinality: str) -> None:
    evidence = _evidence(cardinality=cardinality)

    result = validate_target_evidence(
        evidence,
        expected_scope=SCOPE,
        expected_cardinality="none" if cardinality == "none" else None,
    )

    assert result["ok"] is False
    assert any(row["code"] == "TARGET_EVIDENCE_CARDINALITY_INVALID" for row in result["errors"])


def test_collection_cardinality_requires_collection_members() -> None:
    evidence = _evidence(cardinality="collection")
    evidence["source"]["member_handles"] = ["artifact:order-1", "artifact:order-2"]
    evidence["proof_digest"] = canonical_digest(target_evidence_proof_payload(evidence))

    result = validate_target_evidence(
        evidence,
        expected_scope=SCOPE,
        issuer_validator=lambda row: True,
    )

    assert result["ok"] is True


def test_scope_expiry_and_evaluation_time_are_rechecked() -> None:
    evidence = _evidence()
    evidence["scope"]["thread_id"] = "other-thread"
    evidence["evaluated_at"] = 2000.0
    evidence["proof_digest"] = canonical_digest(target_evidence_proof_payload(evidence))

    result = validate_target_evidence(
        evidence,
        expected_scope=SCOPE,
        evaluation_time=1999.0,
        max_age_seconds=500.0,
        issuer_validator=lambda row: True,
    )

    codes = {row["code"] for row in result["errors"]}
    assert "TARGET_EVIDENCE_SCOPE_MISMATCH" in codes
    assert "TARGET_EVIDENCE_TIME_WINDOW_INVALID" in codes
    assert "TARGET_EVIDENCE_MAX_AGE_EXCEEDED" in codes
    assert result["ok"] is False


def test_recomputed_digest_does_not_replace_issuer_attestation() -> None:
    evidence = _evidence()
    evidence["source"]["result_ref"] = "view:orders:forged"
    evidence["proof_digest"] = canonical_digest(target_evidence_proof_payload(evidence))

    result = validate_target_evidence(
        evidence,
        expected_scope=SCOPE,
        issuer_validator=lambda row: row["source"]["result_ref"] == "view:orders:1",
    )

    assert result["proof_digest_valid"] is True
    assert result["trusted_issuer"] is False
    assert any(row["code"] == "TARGET_EVIDENCE_ISSUER_REJECTED" for row in result["errors"])


def test_v1_and_mixed_versions_are_diagnostic_only() -> None:
    legacy = {"version": LEGACY_TARGET_EVIDENCE_VERSION}
    current = {"version": TARGET_EVIDENCE_VERSION}

    assert validate_target_evidence_versions([legacy])["status"] == "LEGACY_DIAGNOSTIC_ONLY"
    assert validate_target_evidence_versions([legacy, current])["status"] == "UNSUPPORTED_MIXED_VERSION"
    assert validate_target_evidence_versions([current])["ok"] is True
