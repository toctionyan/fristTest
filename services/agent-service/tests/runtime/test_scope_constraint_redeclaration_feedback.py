from __future__ import annotations

from agent_core.lifecycle.dialogue_runtime import _semantic_writer_declaration_result_projection
from agent_core.lifecycle.goal_planning import (
    GoalAlignmentVerdict,
    _alignment_repair_feedback,
    validate_goal_declaration,
)


def _scope_adjudication(*, missing_spans=("蓝色订单", "红色订单")) -> GoalAlignmentVerdict:
    return GoalAlignmentVerdict(
        "incomplete",
        ("蓝色订单的退款记录", "红色订单有没有发票"),
        tuple(missing_spans),
        "target-scope-constraint fidelity: supplied scope entries are ordinary target identity",
        "model",
        True,
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": True,
            "dependency_edges": [],
            "verifier_repair_attempted": True,
            "verifier_repair_kind": "candidate_blind_dependency_scope_constraint_adjudication",
        },
    )


def test_scope_adjudication_becomes_field_specific_redeclaration_feedback():
    feedback = _alignment_repair_feedback(_scope_adjudication())
    row = feedback["independent_verifier_feedback"]
    assert row["authority"] == "independent_goal_alignment"
    assert row["required_action"] == "redeclaration_removing_rejected_scope_constraints"
    assert row["violation_field"] == "target_candidate.scope_constraints"
    assert row["invalid_scope_constraint_spans"] == ["蓝色订单", "红色订单"]
    assert "preserve_literal_population_narrowing_filters_status_thresholds_and_comparisons" in row["constraints"]


def test_writer_projection_exposes_only_scope_violation_not_replacement_semantics():
    alignment = _scope_adjudication()
    result = {
        "ok": False,
        "code": "GOAL_DECLARATION_INCOMPLETE",
        "message": "redeclare",
        "data": {
            "alignment_proof": alignment.as_dict(),
            **_alignment_repair_feedback(alignment),
            "current_user_input": "查下蓝色订单的退款记录，再看看红色订单有没有发票",
            "repair_contract": {"authority": "current_user_input_only", "required_action": "redeclaration"},
        },
    }
    projected = _semantic_writer_declaration_result_projection(result)
    feedback = projected["data"]["independent_verifier_feedback"]
    assert feedback["authority"] == "read_only_violation_evidence"
    assert feedback["violation"]["field"] == "target_candidate.scope_constraints"
    assert feedback["violation"]["evidence_spans"] == ["蓝色订单", "红色订单"]
    assert "remove_only_listed_invalid_scope_constraint_entries_and_preserve_other_literal_population_narrowing_constraints" in feedback["constraints"]
    projected_data = projected["data"]
    assert "alignment_proof" not in projected_data
    assert "dependency_edges" not in projected_data
    assert "requested_effect" not in projected_data


def test_scope_removal_feedback_requires_final_inverse_role_adjudication():
    verdict = GoalAlignmentVerdict(
        "incomplete",
        ("只看已签收订单",),
        ("已签收",),
        "target-scope-constraint coverage",
        "model",
        True,
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": True,
            "verifier_repair_kind": "candidate_blind_dependency_scope_constraint_reaudit",
        },
    )
    assert _alignment_repair_feedback(verdict) == {}


def test_validate_goal_declaration_carries_scope_redeclaration_feedback_without_rewriting_candidate():
    user_text = "查下蓝色订单的退款记录，再看看红色订单有没有发票"
    state = {
        "current_user_input": user_text,
        "goal_alignment_verifier": lambda **_: _scope_adjudication().as_dict(),
    }
    goals = [
        {
            "goal_id": "g1",
            "description": "查询退款记录",
            "evidence_span": "蓝色订单的退款记录",
            "requested_effect": {"domain": "refund", "operation": "query_status", "object_type": "refund"},
            "depends_on": [],
            "target_candidate": {"scope_constraints": [{"evidence_span": "蓝色订单"}]},
        },
        {
            "goal_id": "g2",
            "description": "查询发票",
            "evidence_span": "红色订单有没有发票",
            "requested_effect": {"domain": "invoice", "operation": "query_status", "object_type": "invoice"},
            "depends_on": [],
            "target_candidate": {"scope_constraints": [{"evidence_span": "红色订单"}]},
        },
    ]
    result, plan = validate_goal_declaration(
        state=state,
        args={"goals": goals},
        capability_registry=None,
    )
    assert plan is None
    assert result["code"] == "GOAL_DECLARATION_INCOMPLETE"
    feedback = result["data"]["independent_verifier_feedback"]
    assert feedback["violation_field"] == "target_candidate.scope_constraints"
    assert feedback["invalid_scope_constraint_spans"] == ["蓝色订单", "红色订单"]
    assert goals[0]["target_candidate"]["scope_constraints"] == [{"evidence_span": "蓝色订单"}]
