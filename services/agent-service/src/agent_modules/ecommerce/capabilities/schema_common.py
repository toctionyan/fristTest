"""Shared ecommerce schema fragments. They are helpers, not capability selectors."""
from __future__ import annotations
from typing import Any

_TARGET_PROPERTIES: dict[str, Any] = {
    "mode": {"type": "string", "enum": ["all_orders", "entity_match", "artifact", "collection", "set_operation"]},
    "attribute_span": {"type": "string", "minLength": 1},
    "status": {"type": "string", "enum": ["待付款", "已付款", "待发货", "已发货", "运输中", "已签收", "已取消"]},
    "status_span": {"type": "string", "minLength": 1},
    "operator": {"type": "string", "enum": ["identity", "difference", "union", "intersection", "filter", "sort", "take", "ordinal"]},
    "left_handle": {"type": "string", "minLength": 1},
    "right_handle": {"type": "string", "minLength": 1},
    "position": {"type": "integer", "minimum": 1},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    "sort_field": {"type": "string", "enum": ["created_at", "amount", "order_id"]},
    "sort_direction": {"type": "string", "enum": ["asc", "desc"]},
    "sort_span": {"type": "string", "minLength": 1},
}


def _target_variant(*, properties: list[str], required: list[str], constants: dict[str, str]) -> dict[str, Any]:
    variant_properties = {name: _TARGET_PROPERTIES[name] for name in properties}
    for name, value in constants.items():
        variant_properties[name] = {**variant_properties[name], "const": value}
    return {
        "type": "object",
        "properties": variant_properties,
        "required": required,
        "additionalProperties": False,
    }


# A target is a real discriminated union.  Mode/operator combinations that do
# not make sense cannot pass the model schema or the Runtime's independent
# schema verifier and therefore never reach a domain resolver.
TARGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "oneOf": [
        _target_variant(
            properties=["mode", "attribute_span", "status", "status_span"],
            required=["mode"],
            constants={"mode": "all_orders"},
        ),
        _target_variant(
            properties=["mode", "attribute_span"],
            required=["mode", "attribute_span"],
            constants={"mode": "entity_match"},
        ),
        _target_variant(
            properties=["mode", "left_handle"],
            required=["mode", "left_handle"],
            constants={"mode": "artifact"},
        ),
        _target_variant(
            properties=["mode", "left_handle"],
            required=["mode", "left_handle"],
            constants={"mode": "collection"},
        ),
        *[
            _target_variant(
                properties=["mode", "operator", "left_handle"],
                required=["mode", "operator", "left_handle"],
                constants={"mode": "set_operation", "operator": operator},
            )
            for operator in ("identity",)
        ],
        *[
            _target_variant(
                properties=["mode", "operator", "left_handle", "right_handle"],
                required=["mode", "operator", "left_handle", "right_handle"],
                constants={"mode": "set_operation", "operator": operator},
            )
            for operator in ("difference", "union", "intersection")
        ],
        _target_variant(
            properties=["mode", "operator", "left_handle", "status", "status_span"],
            required=["mode", "operator", "left_handle", "status", "status_span"],
            constants={"mode": "set_operation", "operator": "filter"},
        ),
        _target_variant(
            properties=["mode", "operator", "left_handle", "sort_field", "sort_direction", "sort_span"],
            required=["mode", "operator", "left_handle", "sort_field", "sort_direction", "sort_span"],
            constants={"mode": "set_operation", "operator": "sort"},
        ),
        _target_variant(
            properties=["mode", "operator", "left_handle", "limit"],
            required=["mode", "operator", "left_handle", "limit"],
            constants={"mode": "set_operation", "operator": "take"},
        ),
        _target_variant(
            properties=["mode", "operator", "left_handle", "position"],
            required=["mode", "operator", "left_handle", "position"],
            constants={"mode": "set_operation", "operator": "ordinal"},
        ),
    ],
}
LOGISTICS_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "delivery_status": {"type": "string", "enum": ["待发货", "运输中", "派送中", "已签收", "已取消"]},
        "dispatched": {
            "type": "boolean",
            "description": "已发出/已经发货用 true；尚未发出/还没发货用 false。不要与 delivery_status 同时填写。",
        },
    },
    "additionalProperties": False,
}
CONSTRAINT_BINDINGS_SCHEMA: dict[str, Any] = {"type": "array", "items": {"type": "object", "properties": {"source_span": {"type": "string"}, "kind": {"type": "string", "enum": ["scope", "condition", "result_scope", "time", "quantity", "exclusion"]}, "parameter_path": {"type": "string"}, "normalized_value": {}}, "required": ["source_span", "kind", "parameter_path"], "additionalProperties": False}}
CONTEXT_BINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "仅在显式引用非最新可见结果时填写。explicit_return 用于按商品名/订单号回到一个旧结果，"
        "source_span 必须逐字复制标签片段；explicit_group_reference 用于‘刚才两个/前面三个’这类"
        "明确把最近连续多个可见结果作为一组的引用，source_span 必须逐字复制该组引用。普通的"
        "其中/它/这些不能伪装成任一种显式绑定。"
    ),
    "properties": {
        "reference_kind": {"type": "string", "enum": ["explicit_return", "explicit_group_reference"]},
        "source_span": {"type": "string", "minLength": 1},
    },
    "required": ["reference_kind", "source_span"],
    "additionalProperties": False,
}


def function_schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    declared = dict(properties)
    if "target" in declared:
        declared["context_binding"] = CONTEXT_BINDING_SCHEMA
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": declared, "required": required, "additionalProperties": False}}}


def target_query_schema(
    name: str,
    description: str,
    *,
    shape: str | tuple[str, ...],
) -> dict[str, Any]:
    """Build a target query without silently narrowing collection-capable reads.

    Detail reads pass one literal shape.  Record-list capabilities pass both
    ``one`` and ``collection`` because their domain operation is a list even
    when the selected population happens to contain a single order.  Keeping
    this distinction at the schema boundary prevents a valid "all records"
    request from being converted into a target-selection clarification.
    """
    shapes = [shape] if isinstance(shape, str) else list(shape)
    return function_schema(
        name,
        description,
        {
            "target": TARGET_SCHEMA,
            "expected_shape": {"type": "string", "enum": shapes},
            "reference_span": {"type": "string"},
        },
        ["target", "expected_shape", "reference_span"],
    )


def draft_schema(name: str, description: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    properties = {
        "target": TARGET_SCHEMA,
        "reference_span": {"type": "string"},
        "action_span": {"type": "string"},
        "reason_span": {"type": "string"},
        "reason_code": {"type": "string"},
        "reason_code_span": {"type": "string"},
    }
    properties.update(extra or {})
    return function_schema(name, description, properties, ["target", "reference_span", "action_span"])
