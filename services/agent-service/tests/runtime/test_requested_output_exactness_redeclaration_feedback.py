from __future__ import annotations

from agent_core.lifecycle.dialogue_runtime import _semantic_writer_declaration_result_projection
from agent_core.lifecycle.goal_planning import (
    GoalAlignmentVerdict,
    _alignment_repair_feedback,
    _dependency_blind_goal_projection,
    _semantic_vocabulary_for_alignment,
)


class _Registry:
    def semantic_vocabulary_snapshot(self):
        return {
            "version": "semantic-output-vocabulary@1",
            "authority": "domain_semantics_only_capability_independent",
            "availability_exposed": True,
            "tool_names_exposed": True,
            "outputs": [
                {
                    "output_id": "refund.status",
                    "subject_type": "refund",
                    "effect_kinds": ["read"],
                    "description": "读取已经存在的退款申请记录以及当前处理状态。",
                    "legacy_effect_aliases": ["refund.query_status:refund"],
                    "tool_name": "list_refunds",
                    "available": True,
                },
                {
                    "output_id": "invoice.status",
                    "subject_type": "invoice",
                    "effect_kinds": ["read"],
                    "description": "发票申请或开具状态。",
                },
            ],
        }


def test_alignment_vocabulary_is_meaning_only_and_strips_execution_signals(monkeypatch):
    import agent_core.modules.registry as registry_module

    monkeypatch.setattr(registry_module, "current_module_registry", lambda: _Registry())
    snapshot = _semantic_vocabulary_for_alignment()
    assert snapshot["authority"] == "domain_semantics_only_capability_independent"
    assert snapshot["availability_exposed"] is False
    assert snapshot["tool_names_exposed"] is False
    refund = next(row for row in snapshot["outputs"] if row["output_id"] == "refund.status")
    assert refund == {
        "output_id": "refund.status",
        "subject_type": "refund",
        "effect_kinds": ["read"],
        "description": "读取已经存在的退款申请记录以及当前处理状态。",
    }
    assert "tool_name" not in refund
    assert "available" not in refund
    assert "legacy_effect_aliases" not in refund


def _requested_output_mismatch() -> GoalAlignmentVerdict:
    return GoalAlignmentVerdict(
        "incomplete",
        ("退款什么时候到账",),
        ("退款什么时候到账",),
        "requested_effect_semantic_fidelity",
        "model",
        True,
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": True,
            "dependency_edges": [],
            "verifier_repair_attempted": True,
            "verifier_repair_kind": "candidate_blind_dependency_requested_effect_reaudit",
        },
    )


def test_requested_output_mismatch_becomes_field_specific_redeclaration_feedback():
    row = _alignment_repair_feedback(_requested_output_mismatch())["independent_verifier_feedback"]
    assert row["authority"] == "independent_goal_alignment"
    assert row["required_action"] == "redeclaration_rederiving_requested_outputs"
    assert row["violation_field"] == "requested_effect.requested_outputs"
    assert row["invalid_requested_output_spans"] == ["退款什么时候到账"]
    assert "use_open_when_no_registered_output_description_exactly_represents_the_requested_user_visible_outcome" in row["constraints"]


def test_writer_projection_exposes_only_violation_not_replacement_output_identity():
    alignment = _requested_output_mismatch()
    result = {
        "ok": False,
        "code": "GOAL_DECLARATION_INCOMPLETE",
        "message": "redeclare",
        "data": {
            "alignment_proof": alignment.as_dict(),
            **_alignment_repair_feedback(alignment),
            "current_user_input": "鼠标订单的退款什么时候到账？",
            "repair_contract": {"authority": "current_user_input_only", "required_action": "redeclaration"},
        },
    }
    projected = _semantic_writer_declaration_result_projection(result)
    feedback = projected["data"]["independent_verifier_feedback"]
    assert feedback["authority"] == "read_only_violation_evidence"
    assert feedback["violation"]["field"] == "requested_effect.requested_outputs"
    assert feedback["violation"]["evidence_spans"] == ["退款什么时候到账"]
    assert "rederive_requested_outputs_from_current_user_input_and_semantic_vocabulary_use_open_when_no_exact_registered_meaning_exists" in feedback["constraints"]
    payload = projected["data"]
    assert "alignment_proof" not in payload
    assert "requested_effect" not in payload
    assert "replacement_output_id" not in str(projected)


def test_candidate_blind_audit_keeps_canonical_output_identity_for_exactness_review():
    goals = [{
        "goal_id": "g1",
        "evidence_span": "退款什么时候到账",
        "requested_effect": {
            "domain": "refund",
            "operation": "query_status",
            "object_type": "refund",
            "requested_outputs": [{
                "output_id": "refund.status",
                "evidence_span": "退款什么时候到账",
            }],
        },
        "depends_on": [],
    }]
    projection = _dependency_blind_goal_projection(goals)
    assert projection[0]["requested_effect"]["requested_outputs"] == [{
        "output_id": "refund.status",
        "evidence_span": "退款什么时候到账",
    }]



def _release45_semantic_substitution_mismatch() -> GoalAlignmentVerdict:
    return GoalAlignmentVerdict(
        "incomplete",
        ("鼠标订单的退款什么时候到账",),
        ("鼠标订单的退款什么时候到账",),
        "semantic_substitution",
        "model",
        True,
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": True,
            "dependency_edges": [],
            "verifier_repair_attempted": False,
            "verifier_repair_kind": "",
        },
    )


def test_release45_semantic_substitution_reason_routes_to_requested_output_redeclaration():
    alignment = _release45_semantic_substitution_mismatch()
    row = _alignment_repair_feedback(alignment)["independent_verifier_feedback"]
    assert row["required_action"] == "redeclaration_rederiving_requested_outputs"
    assert row["violation_field"] == "requested_effect.requested_outputs"
    assert row["invalid_requested_output_spans"] == ["鼠标订单的退款什么时候到账"]

    result = {
        "ok": False,
        "code": "GOAL_DECLARATION_INCOMPLETE",
        "message": "redeclare",
        "data": {
            "alignment_proof": alignment.as_dict(),
            **_alignment_repair_feedback(alignment),
            "current_user_input": "鼠标订单的退款什么时候到账",
            "repair_contract": {"authority": "current_user_input_only", "required_action": "redeclaration"},
        },
    }
    projected = _semantic_writer_declaration_result_projection(result)
    feedback = projected["data"]["independent_verifier_feedback"]
    assert feedback["authority"] == "read_only_violation_evidence"
    assert feedback["violation"] == {
        "field": "requested_effect.requested_outputs",
        "reason_code": "semantic_substitution",
        "evidence_spans": ["鼠标订单的退款什么时候到账"],
    }
    assert "rederive_requested_outputs_from_current_user_input_and_semantic_vocabulary_use_open_when_no_exact_registered_meaning_exists" in feedback["constraints"]
    assert "refund.status" not in str(projected)


def test_release45_unrelated_semantic_reason_is_not_reclassified_as_requested_output_mismatch():
    alignment = GoalAlignmentVerdict(
        "incomplete",
        ("鼠标订单",),
        ("鼠标订单",),
        "semantic_scope_mismatch",
        "model",
        True,
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": True,
            "dependency_edges": [],
        },
    )
    assert _alignment_repair_feedback(alignment) == {}
