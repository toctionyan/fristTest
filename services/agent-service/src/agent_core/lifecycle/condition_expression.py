from __future__ import annotations

"""Closed, side-effect-free condition expression algebra for Goal dependencies."""

from copy import deepcopy
import re
from typing import Any, Iterable

CONDITION_EXPRESSION_VERSION = "condition-expression@1"
_COMPARISONS = {
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "between",
    "exists",
}
_LOGICAL = {"and", "or", "not"}
_OPERAND_SOURCES = {"goal_output", "target_fact", "input", "literal"}
_MAX_CONDITION_DEPTH = 4
_MAX_LOGICAL_ARGUMENTS = 8
_SAFE_PATH = re.compile(
    r"^[0-9A-Za-z_\-\u3400-\u9fff]+"
    r"(?:\.[0-9A-Za-z_\-\u3400-\u9fff]+)*$"
)


def _text(value: Any, *, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


def _safe_path(value: Any) -> str:
    path = _text(value, limit=300)
    if not path or not _SAFE_PATH.fullmatch(path) or "__" in path:
        raise ValueError("condition_operand_path_invalid")
    return path


def _operand(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"source": "literal", "value": deepcopy(raw)}
    source = _text(raw.get("source"), limit=80)
    if source not in _OPERAND_SOURCES:
        raise ValueError("condition_operand_source_invalid")
    row: dict[str, Any] = {"source": source}
    if source == "goal_output":
        goal_id = _text(raw.get("goal_id"), limit=200)
        if not goal_id:
            raise ValueError("condition_goal_output_binding_required")
        row.update({"goal_id": goal_id, "path": _safe_path(raw.get("path"))})
    elif source in {"target_fact", "input"}:
        row["path"] = _safe_path(raw.get("path"))
    else:
        row["value"] = deepcopy(raw.get("value"))
    return row


def _legacy_to_canonical(raw: dict[str, Any]) -> dict[str, Any] | None:
    goal_id = _text(raw.get("source_goal_id"), limit=200)
    path = _text(raw.get("output_path") or raw.get("path"), limit=300)
    operator = _text(raw.get("operator"), limit=40).lower()
    if goal_id and path and operator in _COMPARISONS:
        row: dict[str, Any] = {
            "op": operator,
            "left": {"source": "goal_output", "goal_id": goal_id, "path": path},
        }
        if operator == "between":
            row["lower"] = {"source": "literal", "value": deepcopy(raw.get("lower"))}
            row["upper"] = {"source": "literal", "value": deepcopy(raw.get("upper"))}
        elif operator != "exists":
            row["right"] = {"source": "literal", "value": deepcopy(raw.get("value"))}
        return row
    return None


def normalize_condition_expression(
    raw: Any,
    *,
    known_goal_ids: Iterable[str],
    _depth: int = 0,
) -> dict[str, Any]:
    if _depth > _MAX_CONDITION_DEPTH:
        raise ValueError("condition_expression_depth_exceeded")
    if not isinstance(raw, dict):
        raise ValueError("condition_expression_object_required")
    source = _legacy_to_canonical(raw) or deepcopy(raw)
    op = _text(source.get("op"), limit=40).lower()
    if op in _LOGICAL:
        args = source.get("args") if isinstance(source.get("args"), list) else []
        if op == "not" and len(args) != 1:
            raise ValueError("condition_not_requires_one_argument")
        if op in {"and", "or"} and len(args) < 2:
            raise ValueError("condition_logical_requires_two_arguments")
        if len(args) > _MAX_LOGICAL_ARGUMENTS:
            raise ValueError("condition_logical_argument_limit_exceeded")
        return {
            "version": CONDITION_EXPRESSION_VERSION,
            "op": op,
            "args": [
                normalize_condition_expression(
                    value, known_goal_ids=known_goal_ids, _depth=_depth + 1
                )
                for value in args
            ],
        }
    if op not in _COMPARISONS:
        raise ValueError("condition_operator_invalid")
    row: dict[str, Any] = {
        "version": CONDITION_EXPRESSION_VERSION,
        "op": op,
        "left": _operand(source.get("left")),
    }
    if op == "between":
        row["lower"] = _operand(source.get("lower"))
        row["upper"] = _operand(source.get("upper"))
    elif op != "exists":
        row["right"] = _operand(source.get("right"))

    known = {str(value) for value in known_goal_ids if str(value)}
    for operand in condition_operands(row):
        if (
            operand.get("source") == "goal_output"
            and str(operand.get("goal_id") or "") not in known
        ):
            raise ValueError(f"condition_unknown_goal:{operand.get('goal_id')}")
    return row


def condition_operands(expression: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(expression, dict):
        return []
    op = str(expression.get("op") or "")
    if op in _LOGICAL:
        return [
            operand
            for child in list(expression.get("args") or [])
            if isinstance(child, dict)
            for operand in condition_operands(child)
        ]
    rows = []
    for key in ("left", "right", "lower", "upper"):
        if isinstance(expression.get(key), dict):
            rows.append(dict(expression[key]))
    return rows


def condition_goal_dependencies(expression: dict[str, Any]) -> set[str]:
    return {
        str(row.get("goal_id") or "")
        for row in condition_operands(expression)
        if row.get("source") == "goal_output" and str(row.get("goal_id") or "")
    }


def condition_schema(*, depth: int = 3) -> dict[str, Any]:
    """Return a bounded provider schema; Runtime remains the strict verifier."""

    operand = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "source": {"const": "goal_output"},
                    "goal_id": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["source", "goal_id", "path"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "source": {"enum": ["target_fact", "input"]},
                    "path": {"type": "string"},
                },
                "required": ["source", "path"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"source": {"const": "literal"}, "value": {}},
                "required": ["source", "value"],
                "additionalProperties": False,
            },
        ]
    }
    comparisons = [
        {
            "type": "object",
            "properties": {
                "op": {
                    "enum": ["eq", "neq", "gt", "gte", "lt", "lte", "in"]
                },
                "left": operand,
                "right": operand,
            },
            "required": ["op", "left", "right"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "op": {"const": "between"},
                "left": operand,
                "lower": operand,
                "upper": operand,
            },
            "required": ["op", "left", "lower", "upper"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"op": {"const": "exists"}, "left": operand},
            "required": ["op", "left"],
            "additionalProperties": False,
        },
    ]
    if depth <= 0:
        return {"oneOf": comparisons}
    child = condition_schema(depth=depth - 1)
    logical = [
        {
            "type": "object",
            "properties": {
                "op": {"enum": ["and", "or"]},
                "args": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": _MAX_LOGICAL_ARGUMENTS,
                    "items": child,
                },
            },
            "required": ["op", "args"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "op": {"const": "not"},
                "args": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": child,
                },
            },
            "required": ["op", "args"],
            "additionalProperties": False,
        },
    ]
    return {"oneOf": [*comparisons, *logical]}


__all__ = [
    "CONDITION_EXPRESSION_VERSION",
    "condition_goal_dependencies",
    "condition_operands",
    "condition_schema",
    "normalize_condition_expression",
]
