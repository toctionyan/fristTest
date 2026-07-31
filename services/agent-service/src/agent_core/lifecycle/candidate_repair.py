from __future__ import annotations

"""Classify model-candidate protocol failures that can be repaired in-loop.

These failures occur before a business effect is authorized or committed.
They are not user ambiguity and not a business-rule conclusion, so the Loop
may give the same model one bounded chance to submit a corrected candidate.
"""

from typing import Any


CANDIDATE_REPAIRABLE_CODES = frozenset({
    "CAPABILITY_UNAVAILABLE",
    "CAPABILITY_EXACT_MATCH_REQUIRED",
    "CAPABILITY_PARAMETERIZATION_INCOMPLETE",
    "EXPLICIT_MEMBER_REQUIRES_SINGLE_MEMBER_TARGET",
    "DERIVED_SINGLETON_REQUIRES_PARENT_SCOPE",
    "SOURCE_SPAN_NOT_IN_CURRENT_USER_MESSAGE",
    "REASON_CODE_SPAN_REQUIRED",
    "INVALID_REASON_CODE",
    "VISIBLE_RESULT_REF_INVALID",
    "VISIBLE_RESULT_REF_NOT_CUSTOMER_VISIBLE",
    "VISIBLE_RESULT_REF_SHAPE_MISMATCH",
    "WORKFLOW_GOAL_BINDING_REQUIRED",
    "WORKFLOW_GOAL_BINDING_INVALID",
})


def is_candidate_repairable_result(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict) or bool(result.get("ok")):
        return False
    return str(result.get("code") or "") in CANDIDATE_REPAIRABLE_CODES
