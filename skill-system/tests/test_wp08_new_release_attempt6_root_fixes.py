from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services/agent-service/src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {
        "goal_id": goal_id,
        "evidence_span": span,
        "depends_on": depends_on,
    }


def test_candidate_blind_dependency_graph_rejects_spurious_unsupported_sibling_dependency() -> None:
    from agent_core.lifecycle.goal_granularity import (
        _blind_dependency_graph_matches,
        _literal_dependency_edges,
        _maximum_outcome_goal_matching,
    )

    user_text = "查一下鼠标物流，再告诉我快递员手机号"
    spans = ("查一下鼠标物流", "快递员手机号")
    edges, error = _literal_dependency_edges(user_text, spans, [])
    assert error is None
    assert edges == ()

    wrong = [
        _goal("g1", spans[0], []),
        _goal("g2", spans[1], ["g1"]),
    ]
    matched, mapping = _maximum_outcome_goal_matching(spans, wrong)
    assert matched == 2
    assert _blind_dependency_graph_matches(
        outcome_count=2,
        dependency_edges=edges,
        goals=wrong,
        goal_to_outcome=mapping,
    ) is False

    correct = [
        _goal("g1", spans[0], []),
        _goal("g2", spans[1], []),
    ]
    matched, mapping = _maximum_outcome_goal_matching(spans, correct)
    assert matched == 2
    assert _blind_dependency_graph_matches(
        outcome_count=2,
        dependency_edges=edges,
        goals=correct,
        goal_to_outcome=mapping,
    ) is True


def test_candidate_blind_dependency_graph_preserves_true_same_turn_result_dependency() -> None:
    from agent_core.lifecycle.goal_granularity import (
        _blind_dependency_graph_matches,
        _literal_dependency_edges,
        _maximum_outcome_goal_matching,
    )

    user_text = "查一下键盘订单，再看看它能不能退款"
    spans = ("查一下键盘订单", "它能不能退款")
    edges, error = _literal_dependency_edges(
        user_text,
        spans,
        [{
            "dependent_span": "它能不能退款",
            "requires_result_of_span": "查一下键盘订单",
        }],
    )
    assert error is None
    assert edges == ((1, 0),)

    correct = [
        _goal("g1", spans[0], []),
        _goal("g2", spans[1], ["g1"]),
    ]
    matched, mapping = _maximum_outcome_goal_matching(spans, correct)
    assert matched == 2
    assert _blind_dependency_graph_matches(
        outcome_count=2,
        dependency_edges=edges,
        goals=correct,
        goal_to_outcome=mapping,
    ) is True

    wrong = [
        _goal("g1", spans[0], []),
        _goal("g2", spans[1], []),
    ]
    matched, mapping = _maximum_outcome_goal_matching(spans, wrong)
    assert _blind_dependency_graph_matches(
        outcome_count=2,
        dependency_edges=edges,
        goals=wrong,
        goal_to_outcome=mapping,
    ) is False


def test_dependency_repair_feedback_is_candidate_blind_literal_relation() -> None:
    from agent_core.lifecycle.goal_granularity import GoalGranularityVerdict
    from agent_core.lifecycle.goal_planning import _granularity_repair_feedback

    verdict = GoalGranularityVerdict(
        "mixed",
        "blind_inventory_dependency_graph_mismatch",
        (),
        "model_blind_inventory",
        True,
        {
            "candidate_blind": True,
            "dependency_edges": [{
                "dependent_span": "它能不能退款",
                "requires_result_of_span": "查一下键盘订单",
            }],
        },
    )
    feedback = _granularity_repair_feedback(verdict)["independent_verifier_feedback"]
    assert feedback["authority"] == "candidate_blind_goal_inventory"
    assert feedback["required_action"] == "redeclaration_preserving_candidate_blind_dependency_graph"
    assert feedback["dependency_edges"] == [{
        "dependent_span": "它能不能退款",
        "requires_result_of_span": "查一下键盘订单",
    }]
    joined = " ".join(feedback["constraints"])
    assert "capability_absence" in joined
    assert "requested_effect" in joined


def _visible_ref(result_ref: str, member: str, *, shape: str, rank: int = 1) -> dict:
    return {
        "result_ref": result_ref,
        "source_turn": 4,
        "shape": shape,
        "member_handles": [member],
        "canonical_order": [member],
        "resource_types": ["order"],
        "member_resource_types": ["order"],
        "is_latest_visible_turn": True,
        "discourse_recency_rank": rank,
        "lineage_result_refs": [],
        "member_labels": ["蓝牙耳机（订单10001）"],
        "label": "蓝牙耳机（订单10001）",
    }


def test_visible_scope_key_merges_singleton_and_single_member_collection_aliases() -> None:
    from agent_core.context.visible_result_refs import visible_result_scope_key

    artifact = _visible_ref("h_order:10001", "h_order:10001", shape="one", rank=1)
    collection = _visible_ref("h_result:in_transit", "h_order:10001", shape="collection", rank=2)
    assert visible_result_scope_key(artifact) == visible_result_scope_key(collection)

    reordered = {
        **collection,
        "result_ref": "h_result:other",
        "member_handles": ["h_order:10002", "h_order:10001"],
        "canonical_order": ["h_order:10002", "h_order:10001"],
    }
    original_order = {
        **collection,
        "result_ref": "h_result:ordered",
        "member_handles": ["h_order:10001", "h_order:10002"],
        "canonical_order": ["h_order:10001", "h_order:10002"],
    }
    assert visible_result_scope_key(reordered) != visible_result_scope_key(original_order)


def test_reference_resolution_collapses_equivalent_latest_aliases_but_not_distinct_targets() -> None:
    from agent_core.context.reference_resolution import resolve_reference_expression

    expression = {
        "reference_type": "temporal_visible_result",
        "evidence_span": "它",
        "object_type": "order",
        "expected_cardinality": "single",
        "temporal_relation": "latest",
    }
    artifact = _visible_ref("h_order:10001", "h_order:10001", shape="one", rank=1)
    collection = _visible_ref("h_result:in_transit", "h_order:10001", shape="collection", rank=2)
    proof = resolve_reference_expression(expression, visible_result_refs=[artifact, collection])
    assert proof["resolution_status"] == "UNIQUE"
    assert proof["resolved_member_handles"] == ["h_order:10001"]
    assert proof["equivalent_candidate_scope_count"] == 1
    assert proof["equivalent_aliases_collapsed"] == 1

    distinct = _visible_ref("h_order:10002", "h_order:10002", shape="one", rank=2)
    ambiguous = resolve_reference_expression(expression, visible_result_refs=[artifact, distinct])
    assert ambiguous["resolution_status"] == "AMBIGUOUS"
    assert ambiguous["equivalent_candidate_scope_count"] == 2


def test_capability_gate_counts_latest_semantic_scopes_not_raw_alias_handles(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import capability_gate

    artifact = _visible_ref("h_order:10001", "h_order:10001", shape="one", rank=1)
    collection = _visible_ref("h_result:in_transit", "h_order:10001", shape="collection", rank=2)
    refs = [artifact, collection]
    monkeypatch.setattr(capability_gate, "visible_result_refs_from_ledger", lambda *args, **kwargs: refs)
    monkeypatch.setattr(
        capability_gate,
        "validate_runtime_result_ref",
        lambda **kwargs: (artifact, None),
    )
    proof = capability_gate._visible_reference_proof(
        {"artifact_ledger": [], "current_user_input": "它现在是什么状态？"},
        {
            "target": {"mode": "artifact", "left_handle": "h_order:10001"},
            "reference_span": "它",
        },
    )
    discourse = proof["discourse_binding"]
    assert proof["complete"] is True
    assert discourse["latest_visible_result_count"] == 2
    assert discourse["latest_visible_scope_count"] == 1
    assert discourse["latest_visible_equivalent_alias_count"] == 1
    assert discourse["latest_visible_scope_ambiguous"] is False
    assert "latest_visible_scope_ambiguous_requires_explicit_return_or_group" not in proof["errors"]


def test_capability_gate_keeps_true_multi_target_latest_scope_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.runtime import capability_gate

    first = _visible_ref("h_order:10001", "h_order:10001", shape="one", rank=1)
    second = _visible_ref("h_order:10002", "h_order:10002", shape="one", rank=2)
    refs = [first, second]
    monkeypatch.setattr(capability_gate, "visible_result_refs_from_ledger", lambda *args, **kwargs: refs)
    monkeypatch.setattr(capability_gate, "validate_runtime_result_ref", lambda **kwargs: (first, None))
    proof = capability_gate._visible_reference_proof(
        {"artifact_ledger": [], "current_user_input": "它现在是什么状态？"},
        {
            "target": {"mode": "artifact", "left_handle": "h_order:10001"},
            "reference_span": "它",
        },
    )
    assert proof["complete"] is False
    assert proof["discourse_binding"]["latest_visible_scope_count"] == 2
    assert proof["discourse_binding"]["latest_visible_scope_ambiguous"] is True
    assert "latest_visible_scope_ambiguous_requires_explicit_return_or_group" in proof["errors"]
