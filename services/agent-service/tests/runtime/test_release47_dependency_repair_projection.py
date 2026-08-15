from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from agent_core.lifecycle.dialogue_runtime import _semantic_writer_declaration_result_projection
from agent_core.lifecycle.goal_dependency_proof import (
    DependencyGraphObservation,
    DependencyObservationRole,
    dependency_premise_digest,
    dependency_proof_metadata,
    reduce_dependency_graph_proof,
)
from agent_core.lifecycle.goal_planning import GoalAlignmentVerdict, _alignment_repair_feedback


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_preprod_conversation_smoke.py"
SPEC = importlib.util.spec_from_file_location("release47_preprod_semantic_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


def _edge() -> dict[str, str]:
    return {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "它",
    }


def _authority_details(*, verified: list[dict], declared: list[dict], user_text: str) -> dict:
    first_span, second_span = user_text.split("，", 1)
    declared_g2 = [
        str(row.get("requires_result_of_goal_id") or "")
        for row in declared
        if row.get("dependent_goal_id") == "g2"
    ]
    goals = [
        {"goal_id": "g1", "evidence_span": first_span, "depends_on": []},
        {"goal_id": "g2", "evidence_span": second_span, "depends_on": declared_g2},
    ]
    premise = dependency_premise_digest(user_text=user_text, goals=goals)
    pairs = tuple(
        sorted(
            (str(row["dependent_goal_id"]), str(row["requires_result_of_goal_id"]))
            for row in verified
        )
    )
    provisional = DependencyGraphObservation(
        premise_digest=premise,
        edges=pairs,
        complete=True,
        graph_matches_declaration=False,
        expected_pair_count=1,
        observed_pair_count=1,
        source="candidate_blind_dependency_reaudit",
        role=DependencyObservationRole.PROVISIONAL,
        evidence_digest="release47-provisional",
    )
    proof = reduce_dependency_graph_proof(None, provisional)
    closure = DependencyGraphObservation(
        premise_digest=premise,
        edges=pairs,
        complete=True,
        graph_matches_declaration=False,
        expected_pair_count=1,
        observed_pair_count=1,
        source="candidate_blind_dependency_authority_closure",
        role=DependencyObservationRole.ADVERSARIAL_CLOSURE,
        evidence_digest="release47-closure",
    )
    proof = reduce_dependency_graph_proof(proof, closure)
    return dependency_proof_metadata(
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": False,
            "dependency_edges": verified,
            "declared_dependency_edges": declared,
            "verifier_repair_attempted": True,
            "verifier_repair_kind": "candidate_blind_dependency_authority_closure",
        },
        proof,
    )


def _mismatch_result(*, verified: list[dict], declared: list[dict], user_text: str) -> dict:
    alignment = GoalAlignmentVerdict(
        "incomplete",
        (),
        (),
        "goal_alignment_dependency_graph_mismatch",
        "model",
        True,
        _authority_details(verified=verified, declared=declared, user_text=user_text),
    )
    return {
        "ok": False,
        "code": "GOAL_DECLARATION_INCOMPLETE",
        "message": "redeclare",
        "data": {
            "alignment_proof": alignment.as_dict(),
            **_alignment_repair_feedback(alignment),
            "current_user_input": user_text,
            "repair_contract": {
                "authority": "current_user_input_only",
                "required_action": "redeclaration",
            },
        },
    }


def test_release47_missing_true_dependency_keeps_polarity_without_leaking_edge_ids():
    raw = _mismatch_result(
        verified=[_edge()],
        declared=[],
        user_text="查一下键盘订单，再看看它能不能退款",
    )
    projected = _semantic_writer_declaration_result_projection(raw)
    feedback = projected["data"]["independent_verifier_feedback"]

    assert feedback["authority"] == "read_only_violation_evidence"
    assert feedback["required_action"] == "redeclaration_from_current_user_input"
    assert feedback["violation"] == {
        "field": "depends_on",
        "reason_code": "goal_alignment_dependency_graph_mismatch",
        "evidence_spans": ["它"],
        "dependency_delta_kind": "missing_grounded_relation",
    }
    assert (
        "candidate_is_missing_at_least_one_grounded_relation_rederive_and_add_only_relations_proved_by_current_user_input"
        in feedback["constraints"]
    )
    assert "explicit_same_turn_result_reference_result_condition_or_result_value_input_requires_depends_on" in feedback["constraints"]

    serialized = json.dumps(projected, ensure_ascii=False, sort_keys=True)
    assert "dependent_goal_id" not in serialized
    assert "requires_result_of_goal_id" not in serialized
    assert "candidate_declared_dependency_edges" not in serialized
    assert '"g1"' not in serialized
    assert '"g2"' not in serialized


def test_release47_false_declared_dependency_keeps_opposite_polarity_without_replacement_graph():
    raw = _mismatch_result(
        verified=[],
        declared=[{"dependent_goal_id": "g2", "requires_result_of_goal_id": "g1"}],
        user_text="查一下鼠标订单，然后帮我申请退款",
    )
    projected = _semantic_writer_declaration_result_projection(raw)
    feedback = projected["data"]["independent_verifier_feedback"]

    assert feedback["violation"] == {
        "field": "depends_on",
        "reason_code": "goal_alignment_dependency_graph_mismatch",
        "evidence_spans": [],
        "dependency_delta_kind": "unproved_declared_relation",
    }
    assert (
        "candidate_contains_at_least_one_unproved_relation_rederive_and_remove_only_relations_not_proved_by_current_user_input"
        in feedback["constraints"]
    )
    assert "sequence_shared_topic_zero_anaphora_and_execution_support_dataflow_do_not_create_depends_on" in feedback["constraints"]
    serialized = json.dumps(projected, ensure_ascii=False, sort_keys=True)
    assert "dependent_goal_id" not in serialized
    assert "requires_result_of_goal_id" not in serialized
    assert '"g1"' not in serialized
    assert '"g2"' not in serialized


def test_release47_internal_certification_diagnostic_no_longer_renders_alignment_edges_as_blank_span_rows():
    raw = _mismatch_result(
        verified=[_edge()],
        declared=[],
        user_text="查一下键盘订单，再看看它能不能退款",
    )
    diagnostic = HARNESS._sanitized_goal_rejection_diagnostic(raw)

    assert diagnostic["independent_verifier_feedback"]["dependency_edges"] == [_edge()]
    assert diagnostic["alignment"]["dependency_edges"] == [_edge()]
