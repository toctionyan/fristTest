from __future__ import annotations

from types import SimpleNamespace

from agent_core.context.reference_resolution import normalize_reference_expression, resolve_reference_expression
from agent_core.lifecycle.goal_planning import validate_goal_declaration
from agent_core.lifecycle.protocol import planning_schemas
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract
from agent_core.runtime.capability_effects import canonical_effect_identity
from agent_core.runtime.capability_gate import _semantic_reference_binding_proof
from agent_core.runtime.semantic_capability_verifier import _deterministic_historical_target_authority


def _installed_registry():
    from agent_core.modules.registry import ModuleRegistry, configure_registry_providers
    from agent_modules.ecommerce.module import EcommerceModule

    modules = ModuleRegistry([EcommerceModule()])
    configure_registry_providers(runtime_registry=modules.build_runtime_registry, module_registry=lambda: modules)
    return modules.build_runtime_registry().capabilities


def _historical_goal(goal_id: str = "g1") -> dict:
    user_text = "查之前那批还能退哪些"
    expression = normalize_reference_expression({
        "reference_type": "explicit_result_ref",
        "result_ref": "h_result:previous-orders",
        "object_type": "order",
        "expected_cardinality": "collection",
        "evidence_span": user_text,
    }, user_text=user_text)
    proof = resolve_reference_expression(expression, visible_result_refs=[{
        "result_ref": "h_result:previous-orders",
        "source_turn": 0,
        "shape": "collection",
        "member_handles": ["order:1", "order:2"],
        "canonical_order": ["order:1", "order:2"],
        "resource_types": ["order"],
    }])
    assert proof["resolution_status"] == "UNIQUE"
    return {
        "goal_id": goal_id,
        "description": user_text,
        "evidence_span": user_text,
        "requested_effect": {"domain": "refund", "operation": "list_eligibilities", "object_type": "refund_eligibility"},
        "expected_result_cardinality": "collection",
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


def _historical_state() -> dict:
    goal = _historical_goal()
    contract = freeze_semantic_contract(
        turn=1,
        user_text="查之前那批还能退哪些",
        summary="historical targetless capability",
        goals=[goal],
        alignment_proof={"verdict": "exact"},
        granularity_proof={"verdict": "exact"},
    )
    return {
        "frozen_semantic_contract": contract,
        "current_turn_plan": {"effects": [{"effect_id": "effect:g1", "goal_ids": ["g1"], "execution_kind": "observation"}]},
    }


def test_provider_schema_directly_requires_canonical_semantic_outputs() -> None:
    schema = planning_schemas(semantic_output_ids=["shipment.tracking"])[0]
    effect_schema = schema["function"]["parameters"]["properties"]["goals"]["items"]["properties"]["requested_effect"]
    assert "requested_outputs" in effect_schema["required"]
    assert "allOf" not in effect_schema
    output_id_schema = effect_schema["properties"]["requested_outputs"]["items"]["properties"]["output_id"]
    assert output_id_schema["enum"] == ["shipment.tracking", "open"]


def test_new_goal_declaration_rejects_legacy_effect_as_sole_identity(monkeypatch) -> None:
    import agent_core.lifecycle.goal_planning as planning_module

    exact = SimpleNamespace(
        exact=True,
        verdict="exact",
        as_dict=lambda: {"verdict": "exact", "source": "test"},
    )
    monkeypatch.setattr(planning_module, "verify_goal_alignment", lambda **_: exact)
    monkeypatch.setattr(planning_module, "verify_goal_granularity", lambda **_: exact)
    registry = _installed_registry()
    result, plan = validate_goal_declaration(
        state={"current_user_input": "查下物流到哪了", "turn_index": 1, "artifact_ledger": [], "goal_records": []},
        args={
            "summary": "物流查询",
            "goals": [{
                "goal_id": "g1",
                "description": "查物流",
                "evidence_span": "查下物流到哪了",
                "requested_effect": {"domain": "shipment", "operation": "query_logistics", "object_type": "order"},
                "expected_result_cardinality": "single",
                "required": True,
                "depends_on": [],
            }],
        },
        capability_registry=registry,
    )
    assert plan is None
    assert result["code"] == "GOAL_DECLARATION_INVALID"
    assert any("requested_outputs_required_for_new_turn" in error for error in result["data"]["errors"])


def test_canonical_output_identity_ignores_open_compatibility_domain_wording() -> None:
    effect = {
        "domain": "shipment",
        "operation": "query_logistics",
        "object_type": "order",
        "requested_outputs": [{"output_id": "shipment.tracking", "evidence_span": "物流到哪了"}],
    }
    assert canonical_effect_identity(effect) == "semantic-output:shipment.tracking"


def test_targetless_capability_consumes_unique_reference_as_context_not_target() -> None:
    proof = _semantic_reference_binding_proof(_historical_state(), {}, goal_ids={"g1"}, target_cardinality="none")
    assert proof["complete"] is True
    check = proof["checks"][0]
    assert check["matched"] is True
    assert check["reason_code"] == "capability_target_not_required_reference_context_verified"
    assert check["reference_context_only"] is True
    assert check["actual_target_handles"] == []


def test_target_bearing_capability_still_requires_exact_verified_handle() -> None:
    proof = _semantic_reference_binding_proof(_historical_state(), {}, goal_ids={"g1"}, target_cardinality="collection")
    assert proof["complete"] is False
    assert "semantic_reference_binding:g1:resolved_reference_must_use_verified_handle_target" in proof["errors"]


def test_targetless_semantic_verifier_authority_does_not_invent_target() -> None:
    state = _historical_state()
    registry = _installed_registry()
    contract = registry.contract_for_tool("list_active_eligibilities")
    assert contract is not None
    assert contract.planning_contract is not None
    assert contract.planning_contract.target.cardinality == "none"
    authority = _deterministic_historical_target_authority(state, effect_id="effect:g1", args={}, contract=contract)
    assert authority["historical_reference_binding_required"] is True
    assert authority["historical_reference_binding_authoritative"] is True
    assert authority["reference_context_only"] is True
    assert authority["target_mode"] is None
