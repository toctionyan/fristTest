from __future__ import annotations

from agent_core.context.reference_resolution import (
    normalize_reference_expression,
    resolve_reference_expression,
)


def _refs():
    return [
        {
            "result_ref": "view:turn-3:orders",
            "source_turn": 3,
            "shape": "collection",
            "member_handles": ["order:d", "order:e"],
            "canonical_order": ["order:d", "order:e"],
            "resource_types": ["order"],
            "discourse_recency_rank": 1,
        },
        {
            "result_ref": "view:turn-2:refunds",
            "source_turn": 2,
            "shape": "collection",
            "member_handles": ["refund:r1"],
            "canonical_order": ["refund:r1"],
            "resource_types": ["refund"],
            "discourse_recency_rank": 2,
        },
        {
            "result_ref": "view:turn-1:orders",
            "source_turn": 1,
            "shape": "collection",
            "member_handles": ["order:a", "order:b", "order:c"],
            "canonical_order": ["order:a", "order:b", "order:c"],
            "resource_types": ["order"],
            "discourse_recency_rank": 3,
        },
    ]


def test_previous_previous_visible_turn_resolves_without_substitution() -> None:
    text = "上上次那些订单查下物流"
    expression = normalize_reference_expression(
        {
            "reference_type": "temporal_visible_result",
            "temporal_relation": "visible_turn_offset",
            "visible_turn_offset": 2,
            "object_type": "order",
            "expected_cardinality": "collection",
            "evidence_span": "上上次那些订单",
        },
        user_text=text,
    )
    proof = resolve_reference_expression(expression, visible_result_refs=_refs())

    assert proof["resolution_status"] == "UNIQUE"
    assert proof["resolved_result_ref"] == "view:turn-1:orders"
    assert proof["resolved_member_handles"] == ["order:a", "order:b", "order:c"]
    assert proof["auto_substitution_used"] is False


def test_previous_visible_turn_resolves_latest_historical_result() -> None:
    text = "上一次那些订单"
    expression = normalize_reference_expression(
        {
            "reference_type": "temporal_visible_result",
            "temporal_relation": "previous",
            "object_type": "order",
            "expected_cardinality": "collection",
            "evidence_span": text,
        },
        user_text=text,
    )
    proof = resolve_reference_expression(expression, visible_result_refs=_refs())

    assert proof["resolution_status"] == "UNIQUE"
    assert proof["resolved_result_ref"] == "view:turn-3:orders"
    assert proof["auto_substitution_used"] is False


def test_ordinal_member_uses_original_canonical_order() -> None:
    text = "刚才第二个"
    expression = normalize_reference_expression(
        {
            "reference_type": "ordinal_visible_member",
            "temporal_relation": "latest",
            "position": 2,
            "object_type": "order",
            "expected_cardinality": "single",
            "evidence_span": text,
        },
        user_text=text,
    )
    proof = resolve_reference_expression(expression, visible_result_refs=_refs())

    assert proof["resolution_status"] == "UNIQUE"
    assert proof["resolved_result_ref"] == "view:turn-3:orders"
    assert proof["resolved_member_handles"] == ["order:e"]
    assert proof["resolved_position"] == 2


def test_previous_means_latest_historical_turn_and_previous_previous_means_second_latest() -> None:
    previous = normalize_reference_expression(
        {
            "reference_type": "temporal_visible_result",
            "temporal_relation": "previous",
            "object_type": "refund",
            "expected_cardinality": "collection",
            "evidence_span": "上一次那些退款",
        },
        user_text="上一次那些退款",
        expected_object_type="refund",
        expected_cardinality="collection",
    )
    previous_proof = resolve_reference_expression(previous, visible_result_refs=_refs())
    assert previous_proof["resolution_status"] == "TYPE_CONFLICT"
    assert previous_proof["resolved_result_ref"] is None
    assert previous_proof["auto_substitution_used"] is False

    previous_previous = normalize_reference_expression(
        {
            "reference_type": "temporal_visible_result",
            "temporal_relation": "previous_previous",
            "object_type": "refund",
            "expected_cardinality": "collection",
            "evidence_span": "上上次那些退款",
        },
        user_text="上上次那些退款",
        expected_object_type="refund",
        expected_cardinality="collection",
    )
    previous_previous_proof = resolve_reference_expression(previous_previous, visible_result_refs=_refs())
    assert previous_previous_proof["resolution_status"] == "UNIQUE"
    assert previous_previous_proof["resolved_result_ref"] == "view:turn-2:refunds"
    assert previous_previous_proof["auto_substitution_used"] is False


def test_same_turn_multiple_visible_results_are_ambiguous() -> None:
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
        *_refs(),
        {
            "result_ref": "view:turn-3:other-orders",
            "source_turn": 3,
            "shape": "collection",
            "member_handles": ["order:f"],
            "canonical_order": ["order:f"],
            "resource_types": ["order"],
            "discourse_recency_rank": 2,
        },
    ]
    proof = resolve_reference_expression(expression, visible_result_refs=refs)

    assert proof["resolution_status"] == "AMBIGUOUS"
    assert proof["resolved_result_ref"] is None
    assert proof["auto_substitution_used"] is False


def test_not_found_never_falls_back_to_latest_same_type() -> None:
    text = "第九次显示的那些订单"
    expression = normalize_reference_expression(
        {
            "reference_type": "temporal_visible_result",
            "temporal_relation": "visible_turn_offset",
            "visible_turn_offset": 8,
            "object_type": "order",
            "expected_cardinality": "collection",
            "evidence_span": text,
        },
        user_text=text,
    )
    proof = resolve_reference_expression(expression, visible_result_refs=_refs())

    assert proof["resolution_status"] == "NOT_FOUND"
    assert proof["resolved_result_ref"] is None
    assert proof["auto_substitution_used"] is False


def test_unknown_member_type_cannot_prove_typed_reference() -> None:
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
    refs = [{
        "result_ref": "view:legacy",
        "source_turn": 3,
        "shape": "collection",
        "member_handles": ["opaque:1"],
        "canonical_order": ["opaque:1"],
    }]
    proof = resolve_reference_expression(expression, visible_result_refs=refs)

    assert proof["resolution_status"] == "TYPE_CONFLICT"
    assert proof["candidate_refs"][0]["checks"]["object_type_proven"] is False


def test_collection_reference_rejects_single_shape() -> None:
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
    refs = [{
        "result_ref": "artifact:order-1",
        "source_turn": 3,
        "shape": "one",
        "member_handles": ["order:1"],
        "canonical_order": ["order:1"],
        "resource_types": ["order"],
    }]
    proof = resolve_reference_expression(expression, visible_result_refs=refs)

    assert proof["resolution_status"] == "CARDINALITY_CONFLICT"
    assert proof["resolved_result_ref"] is None
