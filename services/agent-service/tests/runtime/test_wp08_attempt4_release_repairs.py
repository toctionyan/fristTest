from __future__ import annotations

import inspect

from agent_core.context.reference_resolution import (
    normalize_reference_expression,
    reference_resolution_prompt_contract,
    resolve_reference_expression,
)
from agent_core.lifecycle import protocol
from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier
from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier


def _visible_aliases() -> list[dict]:
    return [
        {
  "result_ref": "view:all-orders",
  "source_turn": 2,
  "shape": "collection",
  "member_handles": ["order:1", "order:2"],
  "canonical_order": ["order:1", "order:2"],
  "resource_types": ["order"],
  "discourse_recency_rank": 3,
        },
        {
  "result_ref": "view:earphones-only",
  "source_turn": 5,
  "shape": "one",
  "member_handles": ["order:1"],
  "canonical_order": ["order:1"],
  "resource_types": ["order"],
  "discourse_recency_rank": 2,
        },
        {
  "result_ref": "eligibility:earphones",
  "source_turn": 6,
  "shape": "collection",
  "member_handles": ["order:1"],
  "canonical_order": ["order:1"],
  "resource_types": ["order"],
  "discourse_recency_rank": 1,
        },
    ]


def test_explicit_visible_member_collapses_parent_aliases_by_exact_member_identity() -> None:
    text = "回到刚才的蓝牙耳机，它是哪一个订单？"
    expression = normalize_reference_expression(
        {
  "reference_type": "explicit_visible_member",
  "member_handle": "order:1",
  "object_type": "order",
  "expected_cardinality": "single",
  "evidence_span": "蓝牙耳机",
        },
        user_text=text,
    )
    proof = resolve_reference_expression(expression, visible_result_refs=_visible_aliases())
    assert proof["resolution_status"] == "UNIQUE"
    assert proof["resolved_member_handles"] == ["order:1"]
    assert proof["resolved_result_ref"] == "view:earphones-only"
    assert proof["equivalent_candidate_scope_count"] == 1
    assert proof["equivalent_aliases_collapsed"] == 2
    assert proof["selection_policy"] == "explicit_member_identity_then_visible_parent_provenance_no_fallback"
    assert proof["auto_substitution_used"] is False


def test_explicit_visible_member_source_parent_is_exact_and_never_falls_back() -> None:
    text = "回到刚才的蓝牙耳机"
    expression = normalize_reference_expression(
        {
  "reference_type": "explicit_visible_member",
  "member_handle": "order:1",
  "source_result_ref": "view:unrelated",
  "object_type": "order",
  "expected_cardinality": "single",
  "evidence_span": "蓝牙耳机",
        },
        user_text=text,
    )
    proof = resolve_reference_expression(expression, visible_result_refs=_visible_aliases())
    assert proof["resolution_status"] == "NOT_FOUND"
    assert proof["resolved_result_ref"] is None
    assert proof["resolved_member_handles"] == []
    assert proof["auto_substitution_used"] is False


def test_temporal_reference_keeps_distinct_parent_scope_ambiguity() -> None:
    text = "刚才那些订单"
    expression = normalize_reference_expression(
        {
  "reference_type": "temporal_visible_result",
  "temporal_relation": "latest",
  "object_type": "order",
  "expected_cardinality": "collection",
  "evidence_span": text,
        },
        user_text=text,
    )
    refs = [
        {
  "result_ref": "view:a",
  "source_turn": 9,
  "shape": "collection",
  "member_handles": ["order:1"],
  "canonical_order": ["order:1"],
  "resource_types": ["order"],
        },
        {
  "result_ref": "view:b",
  "source_turn": 9,
  "shape": "collection",
  "member_handles": ["order:2"],
  "canonical_order": ["order:2"],
  "resource_types": ["order"],
        },
    ]
    proof = resolve_reference_expression(expression, visible_result_refs=refs)
    assert proof["resolution_status"] == "AMBIGUOUS"
    assert proof["resolved_result_ref"] is None


def test_semantic_dependency_contract_explicit_result_reference_precedes_zero_anaphora_ellipsis() -> None:
    schema_text = str(protocol.DECLARE_TURN_GOALS_SCHEMA)
    alignment_source = inspect.getsource(ModelGoalAlignmentVerifier.verify)
    granularity_source = inspect.getsource(ModelGoalGranularityVerifier.verify)
    assert "显式结果指代不是普通零指代省略" in schema_text
    assert "takes precedence over ordinary same-turn zero-anaphora ellipsis" in alignment_source
    assert "dependency_edges" in alignment_source
    assert "Do not judge or emit a Goal dependency graph" in granularity_source


def test_reference_prompt_contract_documents_exact_member_alias_semantics() -> None:
    rules = " ".join(reference_resolution_prompt_contract()["rules"])
    assert "explicit_visible_member" in rules
    assert "provenance aliases rather than member ambiguity" in rules
    assert "Distinct member handles remain ambiguous" in rules
