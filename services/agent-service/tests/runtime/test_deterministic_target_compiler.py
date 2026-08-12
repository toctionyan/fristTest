from __future__ import annotations

from copy import deepcopy

from agent_core.context.reference_resolution import (
    normalize_reference_expression,
    resolve_reference_expression,
)
from agent_core.kernel.capability import CapabilityTargetContract
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract
from agent_core.runtime.target_compiler import compile_frozen_reference_target


def _frozen_contract(
    *,
    members: list[str] | None = None,
    resource_types: list[str] | None = None,
    include_reference: bool = True,
) -> dict:
    user_text = "查它的物流"
    members = list(members or ["order:10002"])
    resource_types = list(resource_types or ["order"])
    goal = {
        "goal_id": "goal-logistics",
        "description": "查询它的物流",
        "evidence_span": "物流",
        "requested_effect": {
            "domain": "ecommerce",
            "operation": "lookup_logistics",
            "object_type": "order",
            "raw_description": "查它的物流",
        },
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": [],
    }
    if include_reference:
        visible = [
            {
                "result_ref": "result:orders:turn-1",
                "source_turn": 1,
                "shape": "one" if len(members) == 1 else "collection",
                "member_handles": members,
                "canonical_order": members,
                "resource_types": resource_types,
                "member_resource_types": resource_types,
                "discourse_recency_rank": 1,
                "is_latest_visible_turn": True,
            }
        ]
        expression = normalize_reference_expression(
            {
                "reference_type": "temporal_visible_result",
                "evidence_span": "它",
                "object_type": "order",
                "expected_cardinality": "single" if len(members) == 1 else "collection",
                "temporal_relation": "latest",
            },
            user_text=user_text,
            expected_object_type="order",
            expected_cardinality="single" if len(members) == 1 else "collection",
        )
        proof = resolve_reference_expression(expression, visible_result_refs=visible)
        assert proof["resolution_status"] == "UNIQUE"
        goal.update(
            {
                "reference_expression": expression,
                "referent_resolution_proof": proof,
                "resolved_reference": {
                    "result_ref": proof["resolved_result_ref"],
                    "member_handles": list(proof["resolved_member_handles"]),
                    "proof_digest": proof["proof_digest"],
                },
            }
        )
        if len(members) > 1:
            # The historical referent is a collection even though the business
            # result of the logistics Goal is not used by this C1 compiler.
            goal["expected_result_cardinality"] = "collection"
    else:
        goal["target_candidate"] = {
            "mode": "entity_match",
            "entity_type": "order",
            "entity_id": "10002",
        }

    return freeze_semantic_contract(
        turn=2,
        user_text=user_text,
        summary="查询历史唯一订单的物流",
        goals=[goal],
        alignment_proof={"verdict": "pass", "authority": "test"},
    )


def _target_contract(
    *,
    resource_types: tuple[str, ...] = ("order",),
    cardinality: str = "exactly_one",
    binding_sources: tuple[str, ...] = ("target_resolver",),
) -> CapabilityTargetContract:
    return CapabilityTargetContract(
        resource_types=resource_types,
        cardinality=cardinality,
        binding_sources=binding_sources,
    )


def test_unique_frozen_historical_member_compiles_exact_runtime_target() -> None:
    contract = _frozen_contract()

    compiled = compile_frozen_reference_target(
        contract,
        goal_id="goal-logistics",
        target_contract=_target_contract(),
    )

    assert compiled["status"] == "COMPILED"
    assert compiled["authority"] == "deterministic_frozen_target_compiler"
    assert compiled["semantic_digest"] == contract["semantic_digest"]
    assert compiled["binding"] == {
        "binding_source": "target_resolver",
        "binding_kind": "resolved_historical_member",
        "cardinality": "exactly_one",
        "resource_type": "order",
        "result_ref": "result:orders:turn-1",
        "member_handle": "order:10002",
        "referent_resolution_proof_digest": contract["goals"][0]["referent_resolution_proof"]["proof_digest"],
    }
    assert compiled["model_target_reinterpretation_allowed"] is False
    assert compiled["execution_authority_granted"] is False


def test_same_frozen_inputs_compile_identical_target_and_digest() -> None:
    contract = _frozen_contract()
    target_contract = _target_contract()

    first = compile_frozen_reference_target(
        contract,
        goal_id="goal-logistics",
        target_contract=target_contract,
    )
    second = compile_frozen_reference_target(
        deepcopy(contract),
        goal_id="goal-logistics",
        target_contract=target_contract,
    )

    assert first == second
    assert first["compile_digest"] == second["compile_digest"]


def test_model_target_candidate_is_not_a_compiler_authority() -> None:
    contract = _frozen_contract(include_reference=False)

    compiled = compile_frozen_reference_target(
        contract,
        goal_id="goal-logistics",
        target_contract=_target_contract(),
    )

    assert compiled["status"] == "NOT_APPLICABLE"
    assert compiled["reason_code"] == "FROZEN_RESOLVED_REFERENCE_REQUIRED"
    assert compiled["binding"] is None


def test_multi_member_reference_fails_closed_for_single_target() -> None:
    contract = _frozen_contract(members=["order:10002", "order:10004"])

    compiled = compile_frozen_reference_target(
        contract,
        goal_id="goal-logistics",
        target_contract=_target_contract(),
    )

    assert compiled["status"] == "REJECTED"
    assert compiled["reason_code"] == "SINGLE_MEMBER_REFERENCE_REQUIRED"
    assert compiled["binding"] is None


def test_target_resource_type_requires_positive_frozen_reference_proof() -> None:
    contract = _frozen_contract(resource_types=["product"])

    compiled = compile_frozen_reference_target(
        contract,
        goal_id="goal-logistics",
        target_contract=_target_contract(resource_types=("order",)),
    )

    assert compiled["status"] == "REJECTED"
    assert compiled["reason_code"] == "TARGET_RESOURCE_TYPE_NOT_PROVEN"
    assert compiled["binding"] is None


def test_capability_must_accept_target_resolver_binding_source() -> None:
    contract = _frozen_contract()

    compiled = compile_frozen_reference_target(
        contract,
        goal_id="goal-logistics",
        target_contract=_target_contract(binding_sources=("model_argument",)),
    )

    assert compiled["status"] == "REJECTED"
    assert compiled["reason_code"] == "TARGET_RESOLVER_BINDING_NOT_ALLOWED"
    assert compiled["binding"] is None


def test_semantic_digest_mutation_is_rejected_before_compilation() -> None:
    contract = _frozen_contract()
    contract["summary"] = "mutated after freeze"

    compiled = compile_frozen_reference_target(
        contract,
        goal_id="goal-logistics",
        target_contract=_target_contract(),
    )

    assert compiled["status"] == "REJECTED"
    assert compiled["reason_code"] == "SEMANTIC_CONTRACT_DIGEST_INVALID"
    assert compiled["binding"] is None
