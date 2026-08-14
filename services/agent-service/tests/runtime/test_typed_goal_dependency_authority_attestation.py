from __future__ import annotations

from copy import deepcopy

from agent_core.goal_graph.dependency_authority import (
    build_dependency_authority_attestation,
    dependency_authority_attestation_integrity,
)
from agent_core.lifecycle.pretool_execution_policy import (
    build_pretool_execution_policy,
    execution_policy_prompt_projection,
)
from tests.runtime.test_pretool_execution_policy import _contract, _goal, _registry


_SCOPE = {
    "current_tenant_id": "tenant-1",
    "current_user_id": "u001",
    "current_thread_id": "web-u001-stage2d",
}


def _state(contract: dict, **extra) -> dict:
    return {
        "frozen_semantic_contract": contract,
        **_SCOPE,
        **extra,
    }


def test_matching_dependency_shadow_is_sealed_as_evidence_only() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details")
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract),
        capability_registry=_registry(),
    )

    attestation = policy["typed_dependency_authority_attestation"]
    assert attestation["eligibility_status"] == "ELIGIBLE_EVIDENCE_ONLY"
    assert attestation["cutover_authority_granted"] is False
    assert attestation["cutover_performed"] is False
    assert attestation["changes_current_dependency_blocking"] is False
    assert attestation["changes_allowed_capability_tools"] is False
    assert attestation["blocks_execution"] is False
    assert attestation["creates_permit"] is False
    assert attestation["semantic_contract_id"] == contract["semantic_contract_id"]
    assert attestation["semantic_digest"] == contract["semantic_digest"]
    assert attestation["capability_registry_version"] == _registry().version
    assert dependency_authority_attestation_integrity(attestation)["ok"] is True


def test_open_dataflow_produces_not_eligible_attestation_without_changing_blocking() -> None:
    contract = _contract([
        _goal("refund", domain="refund", operation="create"),
        _goal("invoice", domain="invoice", operation="create", depends_on=("refund",)),
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract, goal_records=[]),
        capability_registry=_registry(),
    )
    by_goal = {row["goal_id"]: row for row in policy["goal_policies"]}

    assert by_goal["invoice"]["status"] == "BLOCKED_BY_GOAL_DEPENDENCY"
    attestation = policy["typed_dependency_authority_attestation"]
    assert attestation["eligibility_status"] == "NOT_ELIGIBLE"
    assert attestation["source_dependency_shadow_status"] == "NOT_READY_DATAFLOW_OPEN"
    assert attestation["cutover_authority_granted"] is False
    assert dependency_authority_attestation_integrity(attestation)["ok"] is True


def test_tampered_dependency_shadow_cannot_be_sealed_as_eligible() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details")
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract),
        capability_registry=_registry(),
    )
    shadow = deepcopy(policy["typed_dependency_authority_shadow"])
    shadow["cutover_eligible"] = False

    attestation = build_dependency_authority_attestation(
        dependency_shadow=shadow,
        semantic_contract_id=contract["semantic_contract_id"],
        semantic_digest=contract["semantic_digest"],
        capability_registry_version=_registry().version,
        completed_goal_ids=(),
    )

    assert attestation["eligibility_status"] == "EVIDENCE_INVALID"
    assert "DEPENDENCY_SHADOW_DIGEST_INVALID" in attestation["evidence_errors"]
    assert attestation["cutover_authority_granted"] is False


def test_attestation_digest_detects_post_build_tampering() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details")
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract),
        capability_registry=_registry(),
    )
    tampered = deepcopy(policy["typed_dependency_authority_attestation"])
    tampered["typed_graph_digest"] = "0" * 64

    integrity = dependency_authority_attestation_integrity(tampered)
    assert integrity["ok"] is False
    assert "ATTESTATION_DIGEST_INVALID" in integrity["errors"]


def test_completion_snapshot_is_identity_bound_and_changes_attestation_digest() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details")
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract),
        capability_registry=_registry(),
    )
    shadow = policy["typed_dependency_authority_shadow"]
    base = build_dependency_authority_attestation(
        dependency_shadow=shadow,
        semantic_contract_id=contract["semantic_contract_id"],
        semantic_digest=contract["semantic_digest"],
        capability_registry_version=_registry().version,
        completed_goal_ids=(),
    )
    changed = build_dependency_authority_attestation(
        dependency_shadow=shadow,
        semantic_contract_id=contract["semantic_contract_id"],
        semantic_digest=contract["semantic_digest"],
        capability_registry_version=_registry().version,
        completed_goal_ids=("details",),
    )

    assert base["completion_snapshot_digest"] != changed["completion_snapshot_digest"]
    assert base["attestation_digest"] != changed["attestation_digest"]
    assert base["cutover_authority_granted"] is False
    assert changed["cutover_authority_granted"] is False


def test_attestation_is_not_projected_into_model_prompt() -> None:
    contract = _contract([
        _goal("details", domain="order", operation="query_details")
    ])
    policy = build_pretool_execution_policy(
        state=_state(contract),
        capability_registry=_registry(),
    )
    projection = execution_policy_prompt_projection(policy)

    assert "typed_dependency_authority_attestation" in policy
    assert "typed_dependency_authority_attestation" not in projection
    assert "typed_dependency_authority_shadow" not in projection
