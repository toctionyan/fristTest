from __future__ import annotations

from copy import deepcopy

from agent_core.composition import get_runtime_registry
from agent_core.context.reference_resolution import normalize_reference_expression, resolve_reference_expression
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract
from agent_core.runtime.target_compiler import compile_runtime_target_arguments


def _frozen_contract(*, members: list[str] | None = None, include_reference: bool = True) -> dict:
    user_text = "查它的物流"
    members = list(members or ["artifact:order:10002"])
    goal = {
        "goal_id": "goal-logistics",
        "description": "查询它的物流",
        "evidence_span": "物流",
        "requested_effect": {
            "domain": "ecommerce",
            "operation": "lookup_logistics",
            "object_type": "order",
            "raw_description": user_text,
        },
        "expected_result_cardinality": "collection" if len(members) > 1 else "single",
        "required": True,
        "depends_on": [],
    }
    if include_reference:
        expected_cardinality = "collection" if len(members) > 1 else "single"
        expression = normalize_reference_expression(
            {
                "reference_type": "temporal_visible_result",
                "evidence_span": "它",
                "object_type": "order",
                "expected_cardinality": expected_cardinality,
                "temporal_relation": "latest",
            },
            user_text=user_text,
            expected_object_type="order",
            expected_cardinality=expected_cardinality,
        )
        proof = resolve_reference_expression(
            expression,
            visible_result_refs=[{
                "result_ref": "result:orders:turn-1",
                "source_turn": 1,
                "shape": "collection" if len(members) > 1 else "one",
                "member_handles": members,
                "canonical_order": members,
                "resource_types": ["order"],
                "member_resource_types": ["order"],
                "discourse_recency_rank": 1,
                "is_latest_visible_turn": True,
            }],
        )
        assert proof["resolution_status"] == "UNIQUE"
        goal.update({
            "reference_expression": expression,
            "referent_resolution_proof": proof,
            "resolved_reference": {
                "result_ref": proof["resolved_result_ref"],
                "member_handles": list(proof["resolved_member_handles"]),
                "proof_digest": proof["proof_digest"],
            },
        })
    else:
        goal["target_candidate"] = {"mode": "entity_match", "entity_type": "order", "entity_id": "10002"}
    return freeze_semantic_contract(
        turn=2,
        user_text=user_text,
        summary="查询历史订单的物流",
        goals=[goal],
        alignment_proof={"verdict": "pass", "authority": "test"},
    )


def _logistics_target_contract():
    contract = get_runtime_registry().capabilities.contract_for_tool("get_order_logistics")
    assert contract is not None and contract.planning_contract is not None
    return contract.planning_contract.target


def test_registered_target_resolver_capabilities_publish_deterministic_projection() -> None:
    registry = get_runtime_registry().capabilities
    governed = []
    for tool_name in sorted(registry.tool_names()):
        contract = registry.contract_for_tool(tool_name)
        planning = contract.planning_contract if contract is not None else None
        if planning is None or planning.target.cardinality == "none" or "target_resolver" not in set(planning.target.binding_sources):
            continue
        governed.append(tool_name)
        projection = planning.target.argument_projection
        assert projection is not None, tool_name
        assert projection.argument_name == "target"
        assert projection.constant_fields == (("mode", "artifact"),)
        assert projection.binding_fields == (("left_handle", "member_handle"),)
    assert governed


def test_runtime_replaces_wrong_model_target_with_frozen_member() -> None:
    candidate = {
        "target": {"mode": "artifact", "left_handle": "artifact:order:WRONG"},
        "expected_shape": "one",
        "reference_span": "它",
    }
    projected, evidence = compile_runtime_target_arguments(
        _frozen_contract(),
        goal_ids=["goal-logistics"],
        target_contract=_logistics_target_contract(),
        arguments=candidate,
    )
    assert evidence["status"] == "COMPILED"
    assert evidence["candidate_target_replaced"] is True
    assert evidence["model_target_selection_authority"] is False
    assert evidence["execution_authority_granted"] is False
    assert projected["target"] == {"mode": "artifact", "left_handle": "artifact:order:10002"}
    assert candidate["target"]["left_handle"] == "artifact:order:WRONG"


def test_fresh_uncompiled_target_remains_on_existing_candidate_path() -> None:
    candidate = {
        "target": {"mode": "entity_match", "attribute_span": "机械键盘"},
        "expected_shape": "one",
        "reference_span": "机械键盘",
    }
    projected, evidence = compile_runtime_target_arguments(
        _frozen_contract(include_reference=False),
        goal_ids=["goal-logistics"],
        target_contract=_logistics_target_contract(),
        arguments=candidate,
    )
    assert evidence["status"] == "NOT_APPLICABLE"
    assert projected == candidate


def test_corrupted_frozen_reference_fails_closed_without_candidate_fallback() -> None:
    contract = _frozen_contract()
    contract["summary"] = "mutated after freeze"
    candidate = {
        "target": {"mode": "artifact", "left_handle": "artifact:order:WRONG"},
        "expected_shape": "one",
        "reference_span": "它",
    }
    projected, evidence = compile_runtime_target_arguments(
        contract,
        goal_ids=["goal-logistics"],
        target_contract=_logistics_target_contract(),
        arguments=candidate,
    )
    assert evidence["status"] == "REJECTED"
    assert evidence["reason_code"] == "FROZEN_TARGET_COMPILATION_REJECTED"
    assert projected == candidate
    assert evidence["per_goal"][0]["reason_code"] == "SEMANTIC_CONTRACT_DIGEST_INVALID"


def test_multi_member_frozen_reference_cannot_be_salvaged_by_model_candidate() -> None:
    candidate = {
        "target": {"mode": "artifact", "left_handle": "artifact:order:10002"},
        "expected_shape": "one",
        "reference_span": "它们",
    }
    projected, evidence = compile_runtime_target_arguments(
        _frozen_contract(members=["artifact:order:10002", "artifact:order:10004"]),
        goal_ids=["goal-logistics"],
        target_contract=_logistics_target_contract(),
        arguments=candidate,
    )
    assert evidence["status"] == "REJECTED"
    assert projected == candidate
    assert evidence["per_goal"][0]["reason_code"] == "SINGLE_MEMBER_REFERENCE_REQUIRED"


def test_projection_is_deterministic_and_does_not_mutate_candidate() -> None:
    candidate = {
        "target": {"mode": "artifact", "left_handle": "artifact:order:WRONG"},
        "expected_shape": "one",
        "reference_span": "它",
    }
    frozen = _frozen_contract()
    target_contract = _logistics_target_contract()
    first_args, first = compile_runtime_target_arguments(
        frozen, goal_ids=["goal-logistics"], target_contract=target_contract, arguments=candidate
    )
    second_args, second = compile_runtime_target_arguments(
        deepcopy(frozen), goal_ids=["goal-logistics"], target_contract=target_contract, arguments=deepcopy(candidate)
    )
    assert first_args == second_args
    assert first == second
    assert candidate["target"]["left_handle"] == "artifact:order:WRONG"
