from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one replacement in {path}, found {count}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


protocol = "services/agent-service/src/agent_core/lifecycle/protocol.py"
replace_once(
    protocol,
    '''                                "required": ["domain", "operation", "object_type"],
                                "allOf": [{"required": ["requested_outputs"]}],
''',
    '''                                "required": ["domain", "operation", "object_type", "requested_outputs"],
''',
)

planning = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
replace_once(
    planning,
    '''            requested_effect = normalize_requested_effect(raw_effect, description=description)
            errors.extend(_validate_semantic_output_effect(
                requested_effect,
                user_text=user_text,
                goal_evidence_span=evidence_span,
                goal_id=goal_id,
            ))
            effect_source = (
                "model_semantic_output_effect"
                if "requested_outputs" in requested_effect
                else "legacy_direct_compatibility_effect"
            )
''',
    '''            requested_effect = normalize_requested_effect(raw_effect, description=description)
            if not isinstance(requested_effect.get("requested_outputs"), list):
                raise ValueError("requested_effect.requested_outputs_required_for_new_turn")
            errors.extend(_validate_semantic_output_effect(
                requested_effect,
                user_text=user_text,
                goal_evidence_span=evidence_span,
                goal_id=goal_id,
            ))
            effect_source = "model_semantic_output_effect"
''',
)

gate = "services/agent-service/src/agent_core/runtime/capability_gate.py"
replace_once(
    gate,
    '''def _semantic_reference_binding_proof(
    state: dict[str, Any],
    args: dict[str, Any],
    *,
    goal_ids: set[str],
) -> dict[str, Any]:
''',
    '''def _semantic_reference_binding_proof(
    state: dict[str, Any],
    args: dict[str, Any],
    *,
    goal_ids: set[str],
    target_cardinality: str = "",
) -> dict[str, Any]:
''',
)
replace_once(
    gate,
    '''        if resolution_status != "UNIQUE" or not resolved:
            matched = False
            reason = "frozen_reference_not_unique"
        elif mode in {"entity_match", "all_orders", ""}:
            matched = False
            reason = "resolved_reference_must_use_verified_handle_target"
''',
    '''        if resolution_status != "UNIQUE" or not resolved:
            matched = False
            reason = "frozen_reference_not_unique"
        elif target_cardinality == "none":
            # The registered capability contract is the execution-target authority.
            # A targetless capability may consume a verified frozen historical
            # reference as semantic context without inventing an impossible target.
            matched = True
            reason = "capability_target_not_required_reference_context_verified"
        elif mode in {"entity_match", "all_orders", ""}:
            matched = False
            reason = "resolved_reference_must_use_verified_handle_target"
''',
)
replace_once(
    gate,
    '''            "target_mode": mode or None,
            "actual_target_handles": sorted(actual_handles),
            "canonical_scope": canonical_scope or None,
''',
    '''            "target_mode": mode or None,
            "target_cardinality": target_cardinality or None,
            "reference_context_only": target_cardinality == "none",
            "actual_target_handles": sorted(actual_handles),
            "canonical_scope": canonical_scope or None,
''',
)
replace_once(
    gate,
    '''    semantic_reference_binding = _semantic_reference_binding_proof(
        state, normalized_args, goal_ids=goal_ids
    )
''',
    '''    planning_target = getattr(getattr(contract, "planning_contract", None), "target", None)
    target_cardinality = str(getattr(planning_target, "cardinality", "") or "")
    semantic_reference_binding = _semantic_reference_binding_proof(
        state,
        normalized_args,
        goal_ids=goal_ids,
        target_cardinality=target_cardinality,
    )
''',
)

verifier = "services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py"
replace_once(
    verifier,
    '''def _deterministic_historical_target_authority(
    state: dict[str, Any],
    *,
    effect_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
''',
    '''def _deterministic_historical_target_authority(
    state: dict[str, Any],
    *,
    effect_id: str,
    args: dict[str, Any],
    contract: ToolCapabilityContract,
) -> dict[str, Any]:
''',
)
replace_once(
    verifier,
    '''    target = args.get("target") if isinstance(args.get("target"), dict) else {}
    target_mode = str(target.get("mode") or "")
''',
    '''    planning_target = getattr(getattr(contract, "planning_contract", None), "target", None)
    target_cardinality = str(getattr(planning_target, "cardinality", "") or "")
    target = args.get("target") if isinstance(args.get("target"), dict) else {}
    target_mode = str(target.get("mode") or "")
''',
)
replace_once(
    verifier,
    '''        resolved = goal.get("resolved_reference") if isinstance(goal.get("resolved_reference"), dict) else None
        reference = goal.get("reference_expression") if isinstance(goal.get("reference_expression"), dict) else {}
        if resolved is None:
''',
    '''        resolved = goal.get("resolved_reference") if isinstance(goal.get("resolved_reference"), dict) else None
        proof = goal.get("referent_resolution_proof") if isinstance(goal.get("referent_resolution_proof"), dict) else {}
        reference = goal.get("reference_expression") if isinstance(goal.get("reference_expression"), dict) else {}
        if resolved is None:
''',
)
replace_once(
    verifier,
    '''        matched = bool(
            target_mode not in {"", "all_orders", "entity_match"}
            and expected_handles
            and actual_handles.intersection(expected_handles)
        )
        complete = complete and matched
''',
    '''        if target_cardinality == "none":
            matched = bool(
                str(proof.get("resolution_status") or "") == "UNIQUE"
                and expected_handles
            )
        else:
            matched = bool(
                target_mode not in {"", "all_orders", "entity_match"}
                and expected_handles
                and actual_handles.intersection(expected_handles)
            )
        complete = complete and matched
''',
)
replace_once(
    verifier,
    '''                "target_mode": target_mode or None,
            }
        )
''',
    '''                "target_mode": target_mode or None,
                "target_cardinality": target_cardinality or None,
                "reference_context_only": target_cardinality == "none",
            }
        )
''',
)
replace_once(
    verifier,
    '''        "target_mode": target_mode or None,
        "checks": checks,
''',
    '''        "target_mode": target_mode or None,
        "target_cardinality": target_cardinality or None,
        "reference_context_only": target_cardinality == "none",
        "checks": checks,
''',
)
replace_once(
    verifier,
    '''    deterministic_target_authority = _deterministic_historical_target_authority(
        state,
        effect_id=effect_id,
        args=dict(args),
    )
''',
    '''    deterministic_target_authority = _deterministic_historical_target_authority(
        state,
        effect_id=effect_id,
        args=dict(args),
        contract=contract,
    )
''',
)

test = Path("services/agent-service/tests/runtime/test_wp08_attempt3_single_authority_repairs.py")
test.write_text(r'''from __future__ import annotations

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


def test_new_goal_declaration_rejects_legacy_effect_as_sole_identity() -> None:
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
''', encoding="utf-8")
