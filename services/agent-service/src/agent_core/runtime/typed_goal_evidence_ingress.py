from __future__ import annotations

"""Application-owned ingress for Typed Goal shadow evidence."""

from copy import deepcopy
from math import isfinite
from time import time
from typing import Any, Callable, TypedDict


TRUSTED_TYPED_GOAL_EVIDENCE_RESOLVER_KEY = "_trusted_typed_goal_evidence_resolver"
TypedGoalEvidenceResolver = Callable[[], dict[str, Any] | None]


class TypedGoalEvidenceInputs(TypedDict):
    available_input_evidence: tuple[dict[str, Any], ...]
    evaluation_time: float
    input_issuer_validator: Callable[[dict[str, Any]], bool]
    target_issuer_validator: Callable[[dict[str, Any]], bool]


def _metadata(status: str, **values: Any) -> dict[str, Any]:
    return {
        "status": status,
        "source": "application_runtime_deps",
        "evidence_count": 0,
        "has_evaluation_time": False,
        "has_input_issuer_validator": False,
        "has_target_issuer_validator": False,
        "raw_evidence_exposed": False,
        **values,
    }


def resolve_trusted_typed_goal_evidence(
    state: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Admit only application-composed evidence inputs for shadow use."""
    resolver = state.get(TRUSTED_TYPED_GOAL_EVIDENCE_RESOLVER_KEY)
    if resolver is None:
        return {}, _metadata("DISABLED")
    if not callable(resolver):
        return {}, _metadata("UNTRUSTED_STATE_VALUE_IGNORED")
    try:
        raw = resolver()
    except Exception as exc:
        return {}, _metadata("RESOLVER_ERROR_FAIL_CLOSED", error_type=exc.__class__.__name__)
    if not isinstance(raw, dict):
        return {}, _metadata("INVALID_RESOLUTION_FAIL_CLOSED")

    expected = {
        "available_input_evidence",
        "evaluation_time",
        "input_issuer_validator",
        "target_issuer_validator",
    }
    if set(raw) != expected:
        return {}, _metadata("INVALID_RESOLUTION_SHAPE_FAIL_CLOSED")
    evidence = raw.get("available_input_evidence")
    if not isinstance(evidence, (list, tuple)) or any(
        not isinstance(row, dict) for row in evidence
    ):
        return {}, _metadata("INVALID_EVIDENCE_SHAPE_FAIL_CLOSED")

    evaluation_time = raw.get("evaluation_time")
    if isinstance(evaluation_time, bool):
        valid_time = False
    else:
        try:
            evaluation_time = float(evaluation_time)
            valid_time = isfinite(evaluation_time)
        except (TypeError, ValueError):
            valid_time = False
    input_validator = raw.get("input_issuer_validator")
    target_validator = raw.get("target_issuer_validator")
    if not valid_time or not callable(input_validator) or not callable(target_validator):
        return {}, _metadata(
            "INCOMPLETE_TRUST_ROOT_FAIL_CLOSED",
            has_evaluation_time=bool(valid_time),
            has_input_issuer_validator=callable(input_validator),
            has_target_issuer_validator=callable(target_validator),
        )

    try:
        copied_evidence = tuple(deepcopy(row) for row in evidence)
    except Exception:
        return {}, _metadata("EVIDENCE_COPY_FAILED_FAIL_CLOSED")
    resolved: TypedGoalEvidenceInputs = {
        "available_input_evidence": copied_evidence,
        "evaluation_time": float(evaluation_time),
        "input_issuer_validator": input_validator,
        "target_issuer_validator": target_validator,
    }
    return dict(resolved), _metadata(
        "RESOLVED",
        evidence_count=len(evidence),
        has_evaluation_time=True,
        has_input_issuer_validator=True,
        has_target_issuer_validator=True,
    )


def disabled_typed_goal_evidence_resolver() -> dict[str, Any]:
    """Return an explicit empty trust root for compositions without a source.

    The clock is application-owned, while both issuer validators reject every
    envelope.  This keeps the production shadow call wired and fail-closed
    until a real evidence authority is composed.
    """
    return {
        "available_input_evidence": [],
        "evaluation_time": time(),
        "input_issuer_validator": lambda _row: False,
        "target_issuer_validator": lambda _row: False,
    }


__all__ = [
    "TRUSTED_TYPED_GOAL_EVIDENCE_RESOLVER_KEY",
    "TypedGoalEvidenceInputs",
    "TypedGoalEvidenceResolver",
    "disabled_typed_goal_evidence_resolver",
    "resolve_trusted_typed_goal_evidence",
]
