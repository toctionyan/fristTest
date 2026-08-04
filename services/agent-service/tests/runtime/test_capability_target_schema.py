from __future__ import annotations

import pytest

from agent_core.composition import get_runtime_registry
from agent_core.runtime.capability_gate import normalize_tool_arguments, validate_tool_arguments


def _errors(target: dict) -> list[str]:
    return validate_tool_arguments(
        "list_orders",
        {
            "target": target,
            "expected_shape": "collection",
            "reference_span": "当前目标",
        },
        capability_registry=get_runtime_registry().capabilities,
    )


@pytest.mark.parametrize(
    "target",
    [
        {"mode": "all_orders"},
        {"mode": "all_orders", "status": "已签收", "status_span": "已签收"},
        {"mode": "entity_match", "attribute_span": "键盘"},
        {"mode": "artifact", "left_handle": "artifact:one"},
        {"mode": "collection", "left_handle": "result:orders"},
        {"mode": "set_operation", "operator": "identity", "left_handle": "result:orders"},
        {"mode": "set_operation", "operator": "difference", "left_handle": "result:a", "right_handle": "result:b"},
        {"mode": "set_operation", "operator": "filter", "left_handle": "result:a", "status": "已签收", "status_span": "已签收"},
        {"mode": "set_operation", "operator": "sort", "left_handle": "result:a", "sort_field": "amount", "sort_direction": "desc", "sort_span": "最贵"},
        {"mode": "set_operation", "operator": "take", "left_handle": "result:a", "limit": 1},
        {"mode": "set_operation", "operator": "ordinal", "left_handle": "result:a", "position": 2},
    ],
)
def test_target_discriminated_union_accepts_only_complete_variants(target: dict) -> None:
    assert _errors(target) == []


@pytest.mark.parametrize(
    "target",
    [
        {"mode": "entity_match", "left_handle": "artifact:one"},
        {"mode": "artifact", "attribute_span": "键盘"},
        {"mode": "collection"},
        {"mode": "set_operation", "operator": "take", "left_handle": "result:a"},
        {"mode": "set_operation", "operator": "take", "left_handle": "result:a", "limit": 0},
        {"mode": "set_operation", "operator": "sort", "left_handle": "result:a", "sort_field": "amount"},
        {"mode": "set_operation", "operator": "sort", "left_handle": "result:a", "sort_field": "amount", "sort_direction": "desc"},
        {"mode": "set_operation", "operator": "difference", "left_handle": "result:a"},
        {"mode": "set_operation", "operator": "ordinal", "left_handle": "result:a", "position": 0},
    ],
)
def test_target_discriminated_union_rejects_mode_field_contradictions(target: dict) -> None:
    assert _errors(target) == ["$.target: one_of_mismatch"]


def test_generic_schema_runtime_enforces_array_cardinality_and_uniqueness() -> None:
    from agent_core.runtime.capability_gate import _validate_value

    schema = {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "minItems": 1,
        "maxItems": 2,
        "uniqueItems": True,
    }

    assert _validate_value([], schema) == ["$: min_items"]
    assert _validate_value(["a", "a"], schema) == ["$: unique_items"]
    assert _validate_value(["a", "b", "c"], schema) == ["$: max_items"]


def test_anchorless_status_filter_is_structurally_normalized_to_root_population() -> None:
    normalized, proof = normalize_tool_arguments({
        "target": {
            "mode": "set_operation",
            "operator": "filter",
            "status": "已签收",
            "status_span": "已签收",
        },
        "expected_shape": "collection",
        "reference_span": "已签收的订单",
    })

    assert normalized["target"] == {
        "mode": "all_orders",
        "status": "已签收",
        "status_span": "已签收",
    }
    assert proof["changed"] is True
    assert proof["transformations"][0]["kind"] == "anchorless_root_filter"
    assert proof["value_invention_allowed"] is False


@pytest.mark.parametrize("tool_name", ["list_refunds", "list_after_sales_requests", "list_invoices"])
def test_business_record_list_contract_accepts_one_or_collection(tool_name: str) -> None:
    registry = get_runtime_registry().capabilities
    base = {
        "target": {"mode": "all_orders"},
        "reference_span": "所有业务记录",
    }

    assert validate_tool_arguments(
        tool_name,
        {**base, "expected_shape": "one"},
        capability_registry=registry,
    ) == []
    assert validate_tool_arguments(
        tool_name,
        {**base, "expected_shape": "collection"},
        capability_registry=registry,
    ) == []


def test_controlled_pipeline_schema_accepts_typed_composition() -> None:
    target = {
        "mode": "pipeline",
        "source_kind": "all_orders",
        "steps": [
            {
                "op": "filter",
                "predicate": {
                    "field": "amount",
                    "comparison": "gte",
                    "value": 200,
                    "source_span": "金额不低于200元",
                    "value_span": "200",
                },
            },
            {
                "op": "filter",
                "predicate": {
                    "field": "status",
                    "comparison": "eq",
                    "value": "已签收",
                    "source_span": "已签收",
                    "value_span": "已签收",
                },
            },
            {"op": "sort", "field": "amount", "direction": "desc", "source_span": "最贵"},
            {"op": "take", "limit": 2, "source_span": "两个", "value_span": "2"},
        ],
    }
    assert _errors(target) == []


def test_controlled_pipeline_schema_accepts_verified_collection_source() -> None:
    target = {
        "mode": "pipeline",
        "source_kind": "collection",
        "source_handle": "result:visible-orders",
        "steps": [
            {
                "op": "filter",
                "predicate": {
                    "field": "product_name",
                    "comparison": "contains",
                    "value": "键盘",
                    "source_span": "包含键盘",
                    "value_span": "键盘",
                },
            }
        ],
    }
    assert _errors(target) == []


@pytest.mark.parametrize(
    "target",
    [
        {
            "mode": "pipeline",
            "source_kind": "all_orders",
            "steps": [{"op": "filter", "predicate": {"field": "paid_amount", "comparison": "gte", "value": 200, "source_span": "200以上", "value_span": "200"}}],
        },
        {
            "mode": "pipeline",
            "source_kind": "all_orders",
            "steps": [{"op": "filter", "predicate": {"field": "amount", "comparison": "gte", "value": "200", "source_span": "200以上", "value_span": "200"}}],
        },
        {
            "mode": "pipeline",
            "source_kind": "all_orders",
            "steps": [{"op": "filter", "predicate": {"field": "amount", "comparison": "gte", "value": 200, "source_span": "200以上", "value_span": "200", "sql": "DROP TABLE orders"}}],
        },
        {
            "mode": "pipeline",
            "source_kind": "collection",
            "steps": [{"op": "take", "limit": 1, "source_span": "1个", "value_span": "1"}],
        },
        {
            "mode": "pipeline",
            "source_kind": "all_orders",
            "steps": [{"op": "take", "limit": 1, "source_span": "1个", "value_span": "1"}] * 9,
        },
    ],
)
def test_controlled_pipeline_schema_rejects_unregistered_or_untyped_expressions(target: dict) -> None:
    assert _errors(target) == ["$.target: one_of_mismatch"]


def test_generic_schema_runtime_enforces_number_type() -> None:
    from agent_core.runtime.capability_gate import _validate_value

    schema = {"type": "number", "minimum": 0, "maximum": 500}
    assert _validate_value("200", schema) == ["$: expected_number"]
    assert _validate_value(True, schema) == ["$: expected_number"]
    assert _validate_value(-1, schema) == ["$: minimum"]
    assert _validate_value(501.0, schema) == ["$: maximum"]
    assert _validate_value(float("nan"), schema) == ["$: non_finite_number"]
    assert _validate_value(float("inf"), schema) == ["$: non_finite_number"]
    assert _validate_value(200.5, schema) == []
