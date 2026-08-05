from __future__ import annotations

from typing import Any

from agent_core.context.referent_sets import build_visible_referent_sets
from agent_core.runtime.capability_effects import discover_exact_effect_surface


def _tool_set(values: Any) -> set[str]:
    return {str(value) for value in list(values or []) if str(value)}


def _verify_effect_decision(
    *,
    decision: dict[str, Any],
    expected: dict[str, Any],
    unsupported_goal_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    goal_id = str(decision.get("goal_id") or "")
    if str(decision.get("status") or "") != str(expected.get("status") or ""):
        errors.append(f"{goal_id}:status")
    for field in ("completion_tools", "support_tools", "candidate_tools"):
        if _tool_set(decision.get(field)) != _tool_set(expected.get(field)):
            errors.append(f"{goal_id}:{field}")
    if bool(decision.get("similarity_used")) != bool(expected.get("similarity_used")):
        errors.append(f"{goal_id}:similarity_used")
    expected_unsupported = bool(expected.get("unsupported"))
    if (goal_id in unsupported_goal_ids) != expected_unsupported:
        errors.append(f"{goal_id}:unsupported_goal_membership")
    if str(decision.get("match_basis") or "") != "structured_identity_exact_only":
        errors.append(f"{goal_id}:match_basis")
    return errors


def verify_campaign_case(case: dict[str, Any], *, registry: Any) -> list[str]:
    kind = str(case.get("kind") or "")
    if kind in {"exact_effect", "absent_effect"}:
        surface = discover_exact_effect_surface(registry, [dict(case.get("goal") or {})])
        decisions = list(surface.get("goals") or [])
        if len(decisions) != 1:
            return ["surface_decision_count"]
        errors = _verify_effect_decision(
            decision=dict(decisions[0]),
            expected=dict(case.get("expected") or {}),
            unsupported_goal_ids=_tool_set(surface.get("unsupported_goal_ids")),
        )
        if bool(surface.get("similarity_used")):
            errors.append("surface_similarity_used")
        return errors

    if kind == "mixed_effects":
        goals = [dict(row) for row in list(case.get("goals") or []) if isinstance(row, dict)]
        expected_rows = [dict(row) for row in list(case.get("expected") or []) if isinstance(row, dict)]
        surface = discover_exact_effect_surface(registry, goals)
        decisions = [dict(row) for row in list(surface.get("goals") or []) if isinstance(row, dict)]
        if len(decisions) != len(expected_rows):
            return ["mixed_surface_decision_count"]
        errors: list[str] = []
        unsupported_ids = _tool_set(surface.get("unsupported_goal_ids"))
        for decision, expected in zip(decisions, expected_rows, strict=True):
            errors.extend(
                _verify_effect_decision(
                    decision=decision,
                    expected=expected,
                    unsupported_goal_ids=unsupported_ids,
                )
            )
        if bool(surface.get("similarity_used")):
            errors.append("mixed_surface_similarity_used")
        # A supported goal must retain its own completion tools while the
        # absent goal receives only the unsupported reporter; no substitution.
        if decisions and "report_unsupported_request" in _tool_set(decisions[0].get("candidate_tools")):
            errors.append("unsupported_reporter_leaked_into_supported_goal")
        return errors

    if kind == "context_projection":
        projection = build_visible_referent_sets(list(case.get("refs") or []), max_recent_group_size=4)
        latest = projection.get("latest_visible_turn_set") if isinstance(projection.get("latest_visible_turn_set"), dict) else {}
        expected = dict(case.get("expected") or {})
        errors: list[str] = []
        checks = {
            "latest_result_count": int(latest.get("result_count") or 0),
            "latest_member_count": int(latest.get("member_count") or 0),
            "singular_ambiguous": bool(latest.get("singular_reference_is_ambiguous")),
        }
        for field, actual in checks.items():
            if actual != expected.get(field):
                errors.append(f"context:{field}")
        if bool(projection.get("runtime_auto_select_target")):
            errors.append("context:auto_select_enabled")
        if latest and bool(latest.get("dispatchable")):
            errors.append("context:latest_set_dispatchable")
        expected_ranks = expected.get("group_ranks")
        if expected_ranks is not None:
            actual_ranks = [
                list(row.get("discourse_recency_ranks") or [])
                for row in list(projection.get("recent_contiguous_groups") or [])
            ]
            if actual_ranks != expected_ranks:
                errors.append("context:group_ranks")
        if any(
            not bool(row.get("contiguous_from_latest")) or bool(row.get("dispatchable"))
            for row in list(projection.get("recent_contiguous_groups") or [])
        ):
            errors.append("context:unsafe_group_projection")
        return errors

    return [f"unknown_case_kind:{kind}"]
