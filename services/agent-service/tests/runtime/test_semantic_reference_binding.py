from __future__ import annotations

import pytest

from agent_core.context.reference_resolution import (
    normalize_reference_expression,
    resolve_reference_expression,
)
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract
from agent_core.runtime.capability_gate import _semantic_reference_binding_proof


def _state(*goals: dict) -> dict:
    contract = freeze_semantic_contract(
        turn=1,
        user_text="g1 g2",
        summary="test",
        goals=list(goals),
        alignment_proof={"verdict": "exact"},
        granularity_proof={"verdict": "exact"},
    )
    return {"frozen_semantic_contract": contract}


def _goal(goal_id: str, *, result_ref: str, members: list[str], cardinality: str = "collection") -> dict:
    expression = normalize_reference_expression(
        {
            "reference_type": "explicit_result_ref",
            "result_ref": result_ref,
            "object_type": "order",
            "expected_cardinality": cardinality,
            "evidence_span": goal_id,
        },
        user_text="g1 g2",
    )
    proof = resolve_reference_expression(
        expression,
        visible_result_refs=[
            {
                "result_ref": result_ref,
                "source_turn": 0,
                "shape": "collection" if cardinality == "collection" else "one",
                "member_handles": members,
                "canonical_order": members,
                "resource_types": ["order"],
            }
        ],
    )
    assert proof["resolution_status"] == "UNIQUE"
    return {
        "goal_id": goal_id,
        "description": goal_id,
        "evidence_span": goal_id,
        "requested_effect": {"domain": "order", "operation": "query", "object_type": "order"},
        "expected_result_cardinality": cardinality,
        "required": True,
        "depends_on": [],
        "reference_expression": expression,
        "referent_resolution_proof": proof,
        "resolved_reference": {
            "result_ref": proof["resolved_result_ref"],
            "member_handles": proof["resolved_member_handles"],
            "proof_digest": proof["proof_digest"],
        },
    }


def test_frozen_collection_reference_must_be_consumed_exactly() -> None:
    state = _state(_goal("g1", result_ref="view:old", members=["order:1", "order:2"]))
    mismatch = _semantic_reference_binding_proof(
        state,
        {"target": {"mode": "collection", "left_handle": "view:new"}},
        goal_ids={"g1"},
    )
    assert mismatch["complete"] is False
    assert any("resolved_collection_reference_target_mismatch" in error for error in mismatch["errors"])

    exact = _semantic_reference_binding_proof(
        state,
        {"target": {"mode": "pipeline", "source_kind": "collection", "source_handle": "view:old", "steps": []}},
        goal_ids={"g1"},
    )
    assert exact["complete"] is True


def test_single_member_reference_may_use_exact_artifact_handle() -> None:
    state = _state(_goal("g1", result_ref="view:one", members=["order:1"], cardinality="single"))
    proof = _semantic_reference_binding_proof(
        state,
        {"target": {"mode": "artifact", "left_handle": "order:1"}},
        goal_ids={"g1"},
    )
    assert proof["complete"] is True
    assert proof["checks"][0]["canonical_scope"] == "member:order:1"


def test_multi_goal_single_call_rejects_different_resolved_targets() -> None:
    state = _state(
        _goal("g1", result_ref="view:one", members=["order:1"], cardinality="single"),
        _goal("g2", result_ref="view:two", members=["order:2"], cardinality="single"),
    )
    proof = _semantic_reference_binding_proof(
        state,
        {"target": {"mode": "artifact", "left_handle": "order:1"}},
        goal_ids={"g1", "g2"},
    )
    assert proof["complete"] is False
    assert "multi_goal_resolved_reference_incompatible" in proof["errors"]


def test_freeze_rejects_tampered_resolution_proof() -> None:
    goal = _goal("g1", result_ref="view:old", members=["order:1"], cardinality="single")
    goal["referent_resolution_proof"]["resolved_member_handles"] = ["order:other"]

    with pytest.raises(ValueError, match="REFERENT_RESOLUTION_PROOF_DIGEST_INVALID"):
        _state(goal)


def test_goal_target_compatibility_uses_exact_resolved_reference_identity() -> None:
    from agent_core.lifecycle.semantic_contract import prove_goal_target_compatibility

    same = prove_goal_target_compatibility([
        _goal("g1", result_ref="view:old", members=["order:1"], cardinality="single"),
        _goal("g2", result_ref="view:old", members=["order:1"], cardinality="single"),
    ])
    different = prove_goal_target_compatibility([
        _goal("g1", result_ref="view:old", members=["order:1"], cardinality="single"),
        _goal("g2", result_ref="view:new", members=["order:2"], cardinality="single"),
    ])

    assert same["status"] == "SAME"
    assert different["status"] == "DIFFERENT"
    assert same["auto_substitution_used"] is False
    assert same["similarity_used"] is False
