"""Controlled target-query DSL for the ecommerce overlay.

This module intentionally implements a small, closed expression language.  It
never evaluates SQL, Python expressions, arbitrary paths, or model-supplied
code.  The model may compose only registered fields, comparisons and pipeline
steps, while Runtime independently validates type compatibility and literal
current-turn evidence before evaluating the expression over authority-backed
order rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Callable

ORDER_STATUS_VALUES = ("待付款", "已付款", "待发货", "已发货", "运输中", "已签收", "已取消")

FIELD_REGISTRY: dict[str, dict[str, Any]] = {
    "status": {
        "type": "enum",
        "operators": {"eq", "neq", "in"},
        "values": set(ORDER_STATUS_VALUES),
        "sortable": False,
    },
    "amount": {
        "type": "decimal",
        "operators": {"eq", "neq", "gt", "gte", "lt", "lte", "between"},
        "sortable": True,
    },
    "created_at": {
        "type": "datetime",
        "operators": {"eq", "neq", "gt", "gte", "lt", "lte", "between", "within_last_days"},
        "sortable": True,
    },
    "order_id": {
        "type": "identifier",
        "operators": {"eq", "neq", "contains", "in"},
        "sortable": True,
    },
    "product_name": {
        "type": "string",
        "operators": {"eq", "neq", "contains", "in"},
        "sortable": True,
    },
}


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_SOURCE_SPAN = {"type": "string", "minLength": 1}
_VALUE_SPAN = {"type": "string", "minLength": 1}
_TEXT_VALUE = {"type": "string", "minLength": 1, "maxLength": 256}
_TEXT_VALUES = {
    "type": "array",
    "items": _TEXT_VALUE,
    "minItems": 1,
    "maxItems": 20,
    "uniqueItems": True,
}
_TEXT_VALUE_SPANS = {
    "type": "array",
    "items": _VALUE_SPAN,
    "minItems": 1,
    "maxItems": 20,
    "uniqueItems": True,
}
_STATUS_VALUE = {"type": "string", "enum": list(ORDER_STATUS_VALUES)}
_STATUS_VALUES = {
    "type": "array",
    "items": _STATUS_VALUE,
    "minItems": 1,
    "maxItems": len(ORDER_STATUS_VALUES),
    "uniqueItems": True,
}
_NUMBER = {"type": "number"}
_DATE_VALUE = {
    "type": "string",
    "minLength": 10,
    "maxLength": 40,
    "description": "ISO-8601 date or datetime selected from the user's explicit date span.",
}


def _predicate_variant(*, field_schema: dict[str, Any], comparison: str | list[str], value_schema: dict[str, Any]) -> dict[str, Any]:
    comparisons = [comparison] if isinstance(comparison, str) else list(comparison)
    return _object_schema(
        {
            "field": field_schema,
            "comparison": {"type": "string", "enum": comparisons},
            "value": value_schema,
            "source_span": _SOURCE_SPAN,
            "value_span": _VALUE_SPAN,
        },
        ["field", "comparison", "value", "source_span", "value_span"],
    )


PREDICATE_SCHEMA: dict[str, Any] = {
    "oneOf": [
        _predicate_variant(
            field_schema={"const": "status", "type": "string"},
            comparison=["eq", "neq"],
            value_schema=_STATUS_VALUE,
        ),
        _object_schema(
            {
                "field": {"const": "status", "type": "string"},
                "comparison": {"const": "in", "type": "string"},
                "values": _STATUS_VALUES,
                "source_span": _SOURCE_SPAN,
                "value_spans": _TEXT_VALUE_SPANS,
            },
            ["field", "comparison", "values", "source_span", "value_spans"],
        ),
        _predicate_variant(
            field_schema={"const": "amount", "type": "string"},
            comparison=["eq", "neq", "gt", "gte", "lt", "lte"],
            value_schema=_NUMBER,
        ),
        _object_schema(
            {
                "field": {"const": "amount", "type": "string"},
                "comparison": {"const": "between", "type": "string"},
                "lower": _NUMBER,
                "upper": _NUMBER,
                "source_span": _SOURCE_SPAN,
                "lower_span": _VALUE_SPAN,
                "upper_span": _VALUE_SPAN,
            },
            ["field", "comparison", "lower", "upper", "source_span", "lower_span", "upper_span"],
        ),
        _predicate_variant(
            field_schema={"const": "created_at", "type": "string"},
            comparison=["eq", "neq", "gt", "gte", "lt", "lte"],
            value_schema=_DATE_VALUE,
        ),
        _object_schema(
            {
                "field": {"const": "created_at", "type": "string"},
                "comparison": {"const": "between", "type": "string"},
                "lower": _DATE_VALUE,
                "upper": _DATE_VALUE,
                "source_span": _SOURCE_SPAN,
                "lower_span": _VALUE_SPAN,
                "upper_span": _VALUE_SPAN,
            },
            ["field", "comparison", "lower", "upper", "source_span", "lower_span", "upper_span"],
        ),
        _object_schema(
            {
                "field": {"const": "created_at", "type": "string"},
                "comparison": {"const": "within_last_days", "type": "string"},
                "days": {"type": "integer", "minimum": 1, "maximum": 3650},
                "source_span": _SOURCE_SPAN,
                "value_span": _VALUE_SPAN,
            },
            ["field", "comparison", "days", "source_span", "value_span"],
        ),
        _predicate_variant(
            field_schema={"type": "string", "enum": ["order_id", "product_name"]},
            comparison=["eq", "neq", "contains"],
            value_schema=_TEXT_VALUE,
        ),
        _object_schema(
            {
                "field": {"type": "string", "enum": ["order_id", "product_name"]},
                "comparison": {"const": "in", "type": "string"},
                "values": _TEXT_VALUES,
                "source_span": _SOURCE_SPAN,
                "value_spans": _TEXT_VALUE_SPANS,
            },
            ["field", "comparison", "values", "source_span", "value_spans"],
        ),
    ]
}

PIPELINE_STEP_SCHEMA: dict[str, Any] = {
    "oneOf": [
        _object_schema(
            {
                "op": {"const": "filter", "type": "string"},
                "predicate": PREDICATE_SCHEMA,
            },
            ["op", "predicate"],
        ),
        _object_schema(
            {
                "op": {"const": "sort", "type": "string"},
                "field": {"type": "string", "enum": [name for name, spec in FIELD_REGISTRY.items() if spec.get("sortable")]},
                "direction": {"type": "string", "enum": ["asc", "desc"]},
                "source_span": _SOURCE_SPAN,
            },
            ["op", "field", "direction", "source_span"],
        ),
        _object_schema(
            {
                "op": {"const": "take", "type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "source_span": _SOURCE_SPAN,
                "value_span": _VALUE_SPAN,
            },
            ["op", "limit", "source_span", "value_span"],
        ),
        _object_schema(
            {
                "op": {"const": "ordinal", "type": "string"},
                "position": {"type": "integer", "minimum": 1, "maximum": 100},
                "source_span": _SOURCE_SPAN,
                "value_span": _VALUE_SPAN,
            },
            ["op", "position", "source_span", "value_span"],
        ),
    ]
}

PIPELINE_STEPS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": PIPELINE_STEP_SCHEMA,
    "minItems": 1,
    "maxItems": 8,
}


@dataclass(frozen=True)
class TargetDslError(Exception):
    code: str
    message: str


def _normal(value: Any) -> str:
    return "".join(str(value or "").strip().casefold().split())


def _has_span(user_input: str, span: str) -> bool:
    return bool(_normal(span)) and _normal(span) in _normal(user_input)


def _require_span(user_input: str, span: str, *, label: str) -> None:
    if not _has_span(user_input, span):
        raise TargetDslError("SOURCE_SPAN_NOT_IN_CURRENT_USER_MESSAGE", f"{label}必须来自当前用户原话。")


def _decimal(value: Any, *, code: str = "INVALID_DECIMAL_VALUE") -> Decimal:
    if isinstance(value, bool):
        raise TargetDslError(code, "金额条件必须是数值。")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise TargetDslError(code, "金额条件必须是数值。") from None
    if not parsed.is_finite():
        raise TargetDslError(code, "金额条件必须是有限数值。")
    return parsed


def _numeric_span_matches(span: str, expected: Any) -> bool:
    expected_decimal = _decimal(expected)
    for raw in re.findall(r"[-+]?\d+(?:\.\d+)?", str(span or "")):
        try:
            if Decimal(raw) == expected_decimal:
                return True
        except InvalidOperation:
            continue
    return False


def _integer_span_matches(span: str, expected: int) -> bool:
    if any(int(raw) == int(expected) for raw in re.findall(r"\d+", str(span or ""))):
        return True
    chinese = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    normalized = _normal(span)
    return any(token in normalized and value == int(expected) for token, value in chinese.items())


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise TargetDslError("INVALID_DATETIME_VALUE", "时间条件不能为空。")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise TargetDslError("INVALID_DATETIME_VALUE", "时间条件必须是 ISO-8601 日期或时间。") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _datetime_span_matches(span: str, expected: Any) -> bool:
    text = str(expected or "").strip()
    if not text:
        return False
    # The first controlled version deliberately accepts only literal ISO date
    # evidence.  Natural-language date normalization belongs to a separate,
    # timezone-aware semantic contract rather than this deterministic evaluator.
    return _normal(text) in _normal(span)


def _status_span_matches(span: str, status: str) -> bool:
    normalized = _normal(span)
    expected = _normal(status)
    if expected in normalized:
        return True
    if status.startswith("已") and len(status) > 1:
        body = _normal(status[1:])
        return bool(body) and body in normalized
    return False


def _text_span_matches(span: str, value: Any) -> bool:
    return bool(_normal(value)) and _normal(value) in _normal(span)


def _validate_predicate_evidence(user_input: str, predicate: dict[str, Any]) -> None:
    field = str(predicate.get("field") or "")
    comparison = str(predicate.get("comparison") or "")
    spec = FIELD_REGISTRY.get(field)
    if spec is None or comparison not in set(spec.get("operators") or set()):
        raise TargetDslError("TARGET_DSL_OPERATOR_NOT_ALLOWED", "字段与比较操作不属于当前受控 Target DSL。")
    source_span = str(predicate.get("source_span") or "")
    _require_span(user_input, source_span, label="筛选条件")

    if comparison == "between":
        lower_span = str(predicate.get("lower_span") or "")
        upper_span = str(predicate.get("upper_span") or "")
        _require_span(user_input, lower_span, label="区间下界")
        _require_span(user_input, upper_span, label="区间上界")
        if field == "amount":
            if not _numeric_span_matches(lower_span, predicate.get("lower")) or not _numeric_span_matches(upper_span, predicate.get("upper")):
                raise TargetDslError("TARGET_DSL_VALUE_EVIDENCE_MISMATCH", "金额区间与用户原文数值不一致。")
            if _decimal(predicate.get("lower")) > _decimal(predicate.get("upper")):
                raise TargetDslError("TARGET_DSL_INVALID_RANGE", "金额区间下界不能大于上界。")
        else:
            if not _datetime_span_matches(lower_span, predicate.get("lower")) or not _datetime_span_matches(upper_span, predicate.get("upper")):
                raise TargetDslError("TARGET_DSL_VALUE_EVIDENCE_MISMATCH", "时间区间与用户原文日期不一致。")
            if _parse_datetime(predicate.get("lower")) > _parse_datetime(predicate.get("upper")):
                raise TargetDslError("TARGET_DSL_INVALID_RANGE", "时间区间下界不能晚于上界。")
        return

    if comparison == "in":
        values = list(predicate.get("values") or [])
        spans = [str(value) for value in list(predicate.get("value_spans") or [])]
        if len(values) != len(spans):
            raise TargetDslError("TARGET_DSL_VALUE_SPAN_CARDINALITY_MISMATCH", "集合筛选值与证据片段数量不一致。")
        for value, span in zip(values, spans):
            _require_span(user_input, span, label="集合筛选值")
            if field == "status":
                matched = _status_span_matches(span, str(value))
            else:
                matched = _text_span_matches(span, value)
            if not matched:
                raise TargetDslError("TARGET_DSL_VALUE_EVIDENCE_MISMATCH", "集合筛选值与用户原文不一致。")
        return

    if comparison == "within_last_days":
        span = str(predicate.get("value_span") or "")
        _require_span(user_input, span, label="相对时间数量")
        days = int(predicate.get("days") or 0)
        if days < 1 or not _integer_span_matches(span, days):
            raise TargetDslError("TARGET_DSL_VALUE_EVIDENCE_MISMATCH", "相对天数与用户原文不一致。")
        return

    value_span = str(predicate.get("value_span") or "")
    _require_span(user_input, value_span, label="筛选值")
    value = predicate.get("value")
    if field == "amount":
        matched = _numeric_span_matches(value_span, value)
    elif field == "created_at":
        matched = _datetime_span_matches(value_span, value)
    elif field == "status":
        matched = _status_span_matches(value_span, str(value))
    else:
        matched = _text_span_matches(value_span, value)
    if not matched:
        raise TargetDslError("TARGET_DSL_VALUE_EVIDENCE_MISMATCH", "筛选值与用户原文不一致。")


def _compare(left: Any, comparison: str, right: Any) -> bool:
    if comparison == "eq":
        return left == right
    if comparison == "neq":
        return left != right
    if comparison == "gt":
        return left > right
    if comparison == "gte":
        return left >= right
    if comparison == "lt":
        return left < right
    if comparison == "lte":
        return left <= right
    raise TargetDslError("TARGET_DSL_OPERATOR_NOT_ALLOWED", "当前比较操作不受支持。")


def _row_value(row: dict[str, Any], field: str) -> Any:
    raw = row.get(field)
    if raw is None or str(raw).strip() == "":
        return None
    field_type = str(FIELD_REGISTRY[field]["type"])
    if field_type == "decimal":
        return _decimal(raw, code="TARGET_DSL_ROW_VALUE_INVALID")
    if field_type == "datetime":
        return _parse_datetime(raw)
    if field_type == "enum":
        return str(raw or "")
    return _normal(raw)


def evaluate_predicate(
    row: dict[str, Any],
    predicate: dict[str, Any],
    *,
    user_input: str,
    now: datetime | None = None,
) -> bool:
    _validate_predicate_evidence(user_input, predicate)
    field = str(predicate["field"])
    comparison = str(predicate["comparison"])
    left = _row_value(row, field)
    if left is None:
        return False

    if comparison == "in":
        raw_values = list(predicate.get("values") or [])
        if FIELD_REGISTRY[field]["type"] in {"identifier", "string"}:
            values = {_normal(value) for value in raw_values}
        else:
            values = {str(value) for value in raw_values}
        return left in values
    if comparison == "contains":
        return _normal(predicate.get("value")) in str(left)
    if comparison == "between":
        if field == "amount":
            lower = _decimal(predicate.get("lower"))
            upper = _decimal(predicate.get("upper"))
        else:
            lower = _parse_datetime(predicate.get("lower"))
            upper = _parse_datetime(predicate.get("upper"))
        return lower <= left <= upper
    if comparison == "within_last_days":
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        cutoff = reference.astimezone(timezone.utc) - timedelta(days=int(predicate.get("days") or 0))
        return cutoff <= left <= reference.astimezone(timezone.utc)

    if field == "amount":
        right = _decimal(predicate.get("value"))
    elif field == "created_at":
        right = _parse_datetime(predicate.get("value"))
    elif field == "status":
        right = str(predicate.get("value") or "")
    else:
        right = _normal(predicate.get("value"))
    return _compare(left, comparison, right)


_SORT_FIELD_TERMS: dict[str, tuple[str, ...]] = {
    "amount": ("金额", "价格", "最贵", "最便宜", "由高到低", "由低到高", "从高到低", "从低到高"),
    "created_at": ("创建时间", "下单时间", "日期", "最近", "最新", "最早", "最旧"),
    "order_id": ("订单号", "订单编号", "编号"),
    "product_name": ("商品名", "商品名称", "名称", "名字"),
}
_SORT_DIRECTION_TERMS: dict[str, tuple[str, ...]] = {
    "asc": ("升序", "由低到高", "从低到高", "最便宜", "最低", "最少", "最早", "最旧", "从小到大"),
    "desc": ("降序", "由高到低", "从高到低", "最贵", "最高", "最多", "最近", "最新", "从大到小"),
}


def _validate_sort_evidence(user_input: str, *, field: str, direction: str, source_span: str) -> None:
    _require_span(user_input, source_span, label="排序条件")
    normalized = _normal(source_span)
    if not any(_normal(term) in normalized for term in _SORT_FIELD_TERMS.get(field, ())):
        raise TargetDslError("TARGET_DSL_SORT_EVIDENCE_MISMATCH", "排序字段与用户原文不一致。")
    if not any(_normal(term) in normalized for term in _SORT_DIRECTION_TERMS.get(direction, ())):
        raise TargetDslError("TARGET_DSL_SORT_EVIDENCE_MISMATCH", "排序方向与用户原文不一致。")


def apply_pipeline(
    records: list[tuple[str, dict[str, Any]]],
    steps: list[dict[str, Any]],
    *,
    user_input: str,
    now: datetime | None = None,
) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, Any]]]:
    if not isinstance(steps, list) or not 1 <= len(steps) <= 8 or any(not isinstance(step, dict) for step in steps):
        raise TargetDslError("TARGET_DSL_PIPELINE_INVALID", "Pipeline 必须包含 1 到 8 个结构化步骤。")
    current = [(str(handle), dict(row)) for handle, row in records]
    proof: list[dict[str, Any]] = []
    for index, raw_step in enumerate(steps):
        step = dict(raw_step or {})
        operation = str(step.get("op") or "")
        before = len(current)
        if operation == "filter":
            predicate = dict(step.get("predicate") or {})
            _validate_predicate_evidence(user_input, predicate)
            current = [
                (handle, row)
                for handle, row in current
                if evaluate_predicate(row, predicate, user_input=user_input, now=now)
            ]
            proof.append({
                "index": index,
                "op": "filter",
                "field": str(predicate.get("field") or ""),
                "comparison": str(predicate.get("comparison") or ""),
                "before_count": before,
                "after_count": len(current),
                "source_span": str(predicate.get("source_span") or ""),
            })
        elif operation == "sort":
            field = str(step.get("field") or "")
            direction = str(step.get("direction") or "")
            spec = FIELD_REGISTRY.get(field)
            if spec is None or not bool(spec.get("sortable")) or direction not in {"asc", "desc"}:
                raise TargetDslError("TARGET_DSL_SORT_NOT_ALLOWED", "排序字段或方向不属于当前受控 Target DSL。")
            source_span = str(step.get("source_span") or "")
            _validate_sort_evidence(user_input, field=field, direction=direction, source_span=source_span)
            decorated = [(handle, row, _row_value(row, field)) for handle, row in current]
            present = [item for item in decorated if item[2] is not None]
            missing = [item for item in decorated if item[2] is None]
            present = sorted(present, key=lambda item: item[2], reverse=direction == "desc")
            current = [(handle, row) for handle, row, _ in [*present, *missing]]
            proof.append({
                "index": index,
                "op": "sort",
                "field": field,
                "direction": direction,
                "before_count": before,
                "after_count": len(current),
                "source_span": str(step.get("source_span") or ""),
            })
        elif operation in {"take", "ordinal"}:
            source_span = str(step.get("source_span") or "")
            value_span = str(step.get("value_span") or "")
            _require_span(user_input, source_span, label="数量或序号条件")
            _require_span(user_input, value_span, label="数量或序号值")
            value_key = "limit" if operation == "take" else "position"
            value = int(step.get(value_key) or 0)
            if value < 1 or not _integer_span_matches(value_span, value):
                raise TargetDslError("TARGET_DSL_VALUE_EVIDENCE_MISMATCH", "数量或序号与用户原文不一致。")
            if operation == "take":
                current = current[:value]
            else:
                if value > len(current):
                    raise TargetDslError("ORDINAL_OUT_OF_RANGE", "集合中不存在该序号。")
                current = [current[value - 1]]
            proof.append({
                "index": index,
                "op": operation,
                value_key: value,
                "before_count": before,
                "after_count": len(current),
                "source_span": source_span,
            })
        else:
            raise TargetDslError("TARGET_DSL_STEP_NOT_ALLOWED", "Pipeline 包含未注册的操作。")
    return current, proof
