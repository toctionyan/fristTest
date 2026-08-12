from __future__ import annotations

import inspect
import json


def test_planning_schema_keeps_compat_shape_but_exact_output_is_capability_blind() -> None:
    from agent_core.lifecycle.protocol import planning_schemas

    schema = planning_schemas(semantic_output_ids=["shipment.current_status", "courier.contact.phone"])[0]
    effect = (
        schema["function"]["parameters"]["properties"]["goals"]["items"]
        ["properties"]["requested_effect"]
    )
    assert set(effect["required"]) == {"domain", "operation", "object_type"}
    assert effect["allOf"] == [{"required": ["requested_outputs"]}]
    assert "requested_outputs" in effect["properties"]
    assert "enum" not in effect["properties"]["operation"]
    output_id = effect["properties"]["requested_outputs"]["items"]["properties"]["output_id"]
    assert output_id["enum"] == ["shipment.current_status", "courier.contact.phone", "open"]
    encoded = json.dumps(schema, ensure_ascii=False)
    assert "当前部署登记的业务效果身份" not in encoded
    assert "能力无关" in encoded


def test_pre_freeze_prompt_source_never_renders_capability_effect_index() -> None:
    from agent_core.lifecycle import dialogue_runtime

    runtime_source = inspect.getsource(dialogue_runtime._loop_runtime_prompt)
    static_prompt = dialogue_runtime._loop_static_system_prompt()
    assert "capability_effect_index" not in runtime_source
    assert "当前部署登记的业务效果身份" not in runtime_source
    assert "当前部署登记的业务效果身份" not in static_prompt
    assert "能力无关语义输出词汇" in runtime_source
    assert "requested_outputs" in static_prompt


def _mapping_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_mapping_keys(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            keys.update(_mapping_keys(nested))
    return keys


def test_alignment_and_granularity_feedback_are_violation_only() -> None:
    from agent_core.lifecycle.dialogue_runtime import _semantic_writer_declaration_result_projection

    alignment_result = {
        "ok": False,
        "code": "GOAL_DECLARATION_INCOMPLETE",
        "message": "rejected",
        "data": {
            "current_user_input": "查订单，然后退款",
            "alignment_proof": {
                "verdict": "incomplete",
                "reason_code": "goal_alignment_dependency_graph_mismatch",
                "missing_spans": [],
            },
            "independent_verifier_feedback": {
                "authority": "independent_goal_alignment",
                "required_action": "redeclaration_preserving_grounded_dependency_graph",
                "dependency_edges": [{
                    "dependent_goal_id": "g2",
                    "requires_result_of_goal_id": "g1",
                    "basis_kind": "result_reference",
                    "basis_span": "然后退款",
                }],
                "candidate_declared_dependency_edges": [],
            },
        },
    }
    projected = _semantic_writer_declaration_result_projection(alignment_result)
    keys = _mapping_keys(projected)
    assert "dependency_edges" not in keys
    assert "requires_result_of_goal_id" not in keys
    feedback = projected["data"]["independent_verifier_feedback"]
    assert feedback["authority"] == "read_only_violation_evidence"
    assert feedback["violation"]["evidence_spans"] == ["然后退款"]

    granularity_result = {
        "ok": False,
        "code": "GOAL_DECLARATION_UNDER_SPLIT",
        "message": "rejected",
        "data": {
            "current_user_input": "查物流，再告诉我快递员手机号",
            "granularity_proof": {
                "verdict": "under_split",
                "reason_code": "blind_inventory_has_more_outcomes_than_declared_goals",
                "findings": [{
                    "reason": "blind_inventory_outcome_not_covered",
                    "recommended_role": "goal",
                    "evidence_span": "快递员手机号",
                }],
            },
            "independent_verifier_feedback": {
                "authority": "candidate_blind_goal_inventory",
                "uncovered_outcome_spans": ["快递员手机号"],
            },
        },
    }
    projected = _semantic_writer_declaration_result_projection(granularity_result)
    keys = _mapping_keys(projected)
    encoded = json.dumps(projected, ensure_ascii=False)
    assert "recommended_role" not in keys
    assert "dependency_edges" not in keys
    assert "快递员手机号" in encoded
