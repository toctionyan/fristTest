from __future__ import annotations

from copy import deepcopy

from agent_core.goal_graph import (
    VERIFIED_INPUT_EVIDENCE_VERSION,
    TARGET_EVIDENCE_VERSION,
    target_evidence_proof_payload,
    validate_target_evidence,
    validate_verified_input_evidence,
)
from agent_core.goal_graph.capability_closure import (
    _contract_effect_compatible,
    _digest,
    _target_evidence,
    replay_typed_goal_capability_coverage,
)
from agent_core.goal_graph.contracts import canonical_digest
from agent_core.kernel.capability import ToolCapabilityContract, CapabilityTargetContract
from agent_core.runtime.capability_effects import (
    canonical_semantic_effect_identity,
)


def _scope() -> dict[str, str]:
    return {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-1"}


def _input_evidence() -> dict:
    row = {
        "version": VERIFIED_INPUT_EVIDENCE_VERSION,
        "source_type": "verified_context",
        "type_name": "VerifiedConversationScope",
        "resource_type": "conversation",
        "cardinality": "exactly_one",
        "authority": "authoritative",
        "scope": _scope(),
        "semantic_contract_id": "semantic:8:contract",
        "semantic_digest": "a" * 64,
        "issuer": "runtime.context",
        "issuer_attestation": "proof:attestation-1",
        "issued_at": 10.0,
        "expires_at": 100.0,
        "evaluated_at": 20.0,
        "proof_ref": "context:snapshot-1",
    }
    row["proof_digest"] = canonical_digest(row)
    return row


def test_verified_input_evidence_is_closed_world_and_requires_issuer() -> None:
    evidence = _input_evidence()
    diagnostic = validate_verified_input_evidence(
        evidence,
        expected_scope=_scope(),
        expected_type_name="VerifiedConversationScope",
        expected_semantic_contract_id="semantic:8:contract",
        expected_semantic_digest="a" * 64,
    )
    assert diagnostic["ok"] is False
    assert diagnostic["readiness"] == "DIAGNOSTIC_ONLY"
    assert "VERIFIED_INPUT_EVIDENCE_TRUSTED_ISSUER_UNAVAILABLE" in diagnostic["errors"]

    ready = validate_verified_input_evidence(
        evidence,
        expected_scope=_scope(),
        expected_type_name="VerifiedConversationScope",
        expected_semantic_contract_id="semantic:8:contract",
        expected_semantic_digest="a" * 64,
        issuer_validator=lambda row: row["issuer"] == "runtime.context",
    )
    assert ready["ok"] is True
    assert ready["readiness"] == "READY"


def test_verified_input_evidence_rejects_tampering_expiry_and_unknown_fields() -> None:
    tampered = _input_evidence()
    tampered["expires_at"] = 19.0
    tampered["unexpected"] = True
    result = validate_verified_input_evidence(
        tampered,
        expected_scope=_scope(),
        evaluation_time=20.0,
        issuer_validator=lambda _: True,
    )
    assert result["ok"] is False
    assert "VERIFIED_INPUT_EVIDENCE_UNKNOWN_FIELD:unexpected" in result["errors"]
    assert "VERIFIED_INPUT_EVIDENCE_EXPIRED" in result["errors"]
    assert "VERIFIED_INPUT_EVIDENCE_PROOF_DIGEST_INVALID" in result["errors"]


def test_target_evidence_requires_v2_digest_and_trusted_issuer() -> None:
    evidence = {
        "version": TARGET_EVIDENCE_VERSION,
        "variant": "historical_visible_result",
        "resource_type": "order",
        "logical_type_name": "ResolvedResourceBinding",
        "cardinality": "exactly_one",
        "scope": _scope(),
        "semantic_contract_id": "semantic:8:contract",
        "semantic_digest": "b" * 64,
        "authority": "runtime_visible_result_ref",
        "issuer": "runtime_visible_result_ref",
        "issuer_attestation": "proof:attestation-1",
        "issued_at": 10.0,
        "expires_at": 100.0,
        "evaluated_at": 20.0,
        "proof_ref": "result:visible-1",
        "source": {
            "result_ref": "view:order:1",
            "member_handles": ["artifact:order-1"],
            "reference_kind": "customer_visible",
        },
    }
    evidence["proof_digest"] = canonical_digest(target_evidence_proof_payload(evidence))
    result = validate_target_evidence(
        evidence,
        expected_scope=_scope(),
        expected_resource_type="order",
        expected_logical_type_name="ResolvedResourceBinding",
        expected_cardinality="exactly_one",
        expected_semantic_contract_id="semantic:8:contract",
        expected_semantic_digest="b" * 64,
        evaluation_time=20.0,
        issuer_validator=lambda row: row["issuer"] == "runtime_visible_result_ref",
    )
    assert result["ok"] is True
    assert result["readiness"] == "READY"


def test_v2_effect_identity_does_not_collapse_same_output_id() -> None:
    first = {
        "effect_kind": "completion",
        "domain": "orders",
        "operation": "read",
        "object_type": "order",
        "subject_type": "customer",
        "requested_outputs": [{"output_id": "order.status"}],
    }
    second = deepcopy(first)
    second["operation"] = "refund"
    assert canonical_semantic_effect_identity(first) != canonical_semantic_effect_identity(second)


def test_legacy_effect_alias_cannot_enter_v2_compatibility() -> None:
    requested = {
        "effect_kind": "read",
        "domain": "order",
        "operation": "list",
        "object_type": "order",
        "subject_type": "order",
        "requested_outputs": [{"output_id": "order.collection"}],
    }
    contract = ToolCapabilityContract(
        key="demo.legacy-only",
        tool_name="legacy_only",
        category="query",
        writes_business_data=False,
        evidence_sources=("demo",),
        planner_rule="demo",
        unavailable_response="unavailable",
        completion_effects=("order.list:order",),
    )
    assert _contract_effect_compatible({"requested_effect": requested}, contract) is False


def test_v2_target_cannot_fall_back_to_legacy_binding() -> None:
    scope = _scope()
    result = _target_evidence(
        {
            "goal_id": "g1",
            "input_ports": [{
                "name": "target",
                "type_name": "ResolvedOrderBinding",
                "cardinality": "exactly_one",
            }],
            "target_binding": {
                "verified": True,
                "status": "VERIFIED",
                "resource_type": "order",
                "cardinality": "exactly_one",
            },
        },
        {"scope": scope},
        target_contract=CapabilityTargetContract(
            resource_types=("order",),
            cardinality="exactly_one",
            logical_type_name="ResolvedOrderBinding",
        ),
        strict_v2=True,
    )
    assert result["status"] == "UNRESOLVED"
    assert result["validation"]["errors"] == ["TYPED_TARGET_EVIDENCE_REQUIRED"]


def test_v2_replay_rejects_recomputed_digest_with_unknown_nested_field() -> None:
    coverage = {
        "version": "typed-goal-capability-coverage@2",
        "authority": "read_only_typed_compatibility_not_execution_authority",
        "matching": "exact_effect_plus_typed_target_and_input_contracts",
        "graph_id": "graph:1",
        "graph_digest": "graph-digest",
        "semantic_contract_id": "semantic:1",
        "semantic_digest": "a" * 64,
        "capability_registry_version": "registry@1",
        "capability_registry_snapshot_digest": "b" * 64,
        "target_evidence_version": TARGET_EVIDENCE_VERSION,
        "verified_input_evidence_version": VERIFIED_INPUT_EVIDENCE_VERSION,
        "exact_effect_identity_version": "semantic-effect@2",
        "evaluation_time": None,
        "coverage_status": "INCOMPLETE",
        "dataflow_status": "GOAL_GRAPH_DATAFLOW_CLOSED",
        "dataflow_errors": [],
        "derived_dependencies": {},
        "required_goal_ids": ["g1"],
        "uncovered_goal_ids": ["g1"],
        "ready_goal_ids": [],
        "interaction_goal_ids": [],
        "goals": [{
            "goal_id": "g1",
            "required": True,
            "requested_effect_identity": None,
            "status": "UNCOVERED",
            "closed_capability_tools": [],
            "collectable_capability_tools": [],
            "candidate_proofs": [],
        }],
        "must_not_dispatch": True,
        "creates_permit": False,
        "mutates_graph": False,
        "mutates_semantics": False,
        "model_target_selection_authority": False,
        "execution_authority_granted": False,
    }
    coverage["goals"][0]["nested_unknown"] = True
    coverage["coverage_digest"] = _digest(coverage)
    result = replay_typed_goal_capability_coverage(coverage)
    assert result["ok"] is False
    assert "COVERAGE_GOAL_UNKNOWN_FIELD:nested_unknown" in result["errors"]
