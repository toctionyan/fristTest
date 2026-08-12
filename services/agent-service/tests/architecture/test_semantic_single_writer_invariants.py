from __future__ import annotations

import inspect
import json


def test_planning_schema_is_requested_output_based_and_has_no_legacy_deployed_identity_fields() -> None:
    from agent_core.lifecycle.protocol import planning_schemas

    schema = planning_schemas(semantic_output_ids=["shipment.current_status", "courier.contact.phone"])[0]
    effect = (
        schema["function"]["parameters"]["properties"]["goals"]["items"]
        ["properties"]["requested_effect"]
    )
    assert set(effect["required"]) == {"effect_kind", "subject_type", "requested_outputs", "raw_description"}
    assert not {"domain", "operation", "object_type"}.intersection(effect["properties"])
    output_id = effect["properties"]["requested_outputs"]["items"]["properties"]["output_id"]
    assert output_id["enum"] == ["shipment.current_status", "courier.contact.phone", "open"]
    assert "当前部署登记的业务效果身份" not in json.dumps(schema, ensure_ascii=False)


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
    from types import SimpleNamespace
    from agent_core.lifecycle.goal_planning import (
        GoalAlignmentVerdict,
        _alignment_repair_feedback,
        _granularity_repair_feedback,
    )

    alignment = GoalAlignmentVerdict(
        "incomplete",
        ("然后退款",),
        (),
        "goal_alignment_dependency_graph_mismatch",
        "model",
        True,
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": False,
            "dependency_edges": [{
                "dependent_goal_id": "g2",
                "requires_result_of_goal_id": "g1",
                "basis_kind": "result_reference",
                "basis_span": "然后退款",
            }],
        },
    )
    alignment_feedback = _alignment_repair_feedback(alignment)
    alignment_keys = _mapping_keys(alignment_feedback)
    assert "dependency_edges" not in alignment_keys
    assert "requires_result_of_goal_id" not in alignment_keys
    assert alignment_feedback["independent_verifier_feedback"]["authority"] == "read_only_violation_evidence"

    granularity = SimpleNamespace(
        verdict="under_split",
        reason_code="blind_inventory_has_more_outcomes_than_declared_goals",
        findings=({
            "goal_id": None,
            "reason": "blind_inventory_outcome_not_covered",
            "recommended_role": "goal",
            "evidence_span": "快递员手机号",
        },),
        details={},
    )
    granularity_feedback = _granularity_repair_feedback(granularity)
    granularity_keys = _mapping_keys(granularity_feedback)
    encoded = json.dumps(granularity_feedback, ensure_ascii=False)
    assert "recommended_role" not in granularity_keys
    assert "dependency_edges" not in granularity_keys
    assert "快递员手机号" in encoded
