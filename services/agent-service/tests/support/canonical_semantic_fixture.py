"""Fail-closed migration for deterministic scripted model fixtures.

The production semantic writer now requires ``requested_outputs`` on every new
turn.  The large v20.4 scripted catalogs predate that writer contract, so their
model candidates must be upgraded before they are sent through the *real* live
writer boundary.

This helper is test-only.  It never changes production validation and never
uses capability availability, tool names, planner bindings, or oracle-required
tools to invent semantic identity.  Unique historical aliases come only from
the ecommerce semantic vocabulary's migration metadata.  Ambiguous or
capability-free meanings are resolved explicitly below; unknown meanings stay
``open`` rather than being coerced to a nearby installed capability.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from agent_modules.ecommerce.semantic_vocabulary import SEMANTIC_OUTPUTS


# The old logistics compatibility triple intentionally fans out to three
# canonical meanings.  These release fixtures ask where the shipment is, so
# they explicitly demand current status rather than letting compatibility data
# guess between status, ETA and tracking.
_AMBIGUOUS_OUTPUT_OVERRIDES: dict[tuple[str, str, int, str], str] = {
    (
        "conversation_runtime_contract_suite_v20_4",
        "multi_query_orders_and_logistics",
        1,
        "g2",
    ): "shipment.current_status",
    (
        "semantic_goal_coverage_suite_v20_4",
        "semantic_multi_orders_logistics",
        1,
        "g2",
    ): "shipment.current_status",
}

# Courier contact is a valid semantic output with deliberately zero installed
# business-capability coverage.  These exact fixtures must therefore retain
# that semantic identity instead of being downgraded to ``open`` merely because
# execution is unsupported.
_EXACT_OUTPUT_OVERRIDES: dict[tuple[str, str, int, str], str] = {
    (
        "semantic_goal_coverage_suite_v20_4",
        "semantic_supported_plus_unsupported",
        1,
        "g2",
    ): "courier.contact.phone",
    (
        "semantic_goal_coverage_suite_v20_4",
        "semantic_unsupported_courier_phone",
        1,
        "g1",
    ): "courier.contact.phone",
}


def _legacy_alias(effect: dict[str, Any]) -> str:
    return (
        f"{str(effect.get('domain') or '').strip()}."
        f"{str(effect.get('operation') or '').strip()}:"
        f"{str(effect.get('object_type') or '').strip()}"
    )


def _legacy_index() -> dict[str, tuple[str, ...]]:
    rows: dict[str, list[str]] = {}
    for definition in SEMANTIC_OUTPUTS:
        for alias in definition.legacy_effect_aliases:
            rows.setdefault(str(alias), []).append(str(definition.output_id))
    return {alias: tuple(sorted(set(output_ids))) for alias, output_ids in rows.items()}


_LEGACY_INDEX = _legacy_index()


def _output_for_fixture_goal(
    *,
    suite_id: str,
    case_id: str,
    turn_index: int,
    goal: dict[str, Any],
) -> dict[str, Any]:
    effect = goal.get("requested_effect") if isinstance(goal.get("requested_effect"), dict) else {}
    existing = effect.get("requested_outputs")
    if isinstance(existing, list) and existing:
        return effect

    goal_id = str(goal.get("goal_id") or "")
    evidence_span = str(goal.get("evidence_span") or "").strip()
    if not goal_id or not evidence_span:
        raise AssertionError(
            f"{suite_id}:{case_id}:t{turn_index}: canonical fixture migration requires goal_id and evidence_span"
        )

    key = (suite_id, case_id, turn_index, goal_id)
    output_id = _EXACT_OUTPUT_OVERRIDES.get(key) or _AMBIGUOUS_OUTPUT_OVERRIDES.get(key)
    alias = _legacy_alias(effect)
    candidates = _LEGACY_INDEX.get(alias, ())

    if output_id is None and len(candidates) == 1:
        output_id = candidates[0]
    elif output_id is None and len(candidates) > 1:
        raise AssertionError(
            f"{suite_id}:{case_id}:t{turn_index}:{goal_id}: ambiguous legacy semantic alias {alias!r}; "
            "author an exact fixture override instead of guessing"
        )

    upgraded = deepcopy(effect)
    if output_id is None:
        raw_description = str(effect.get("raw_description") or goal.get("description") or evidence_span).strip()
        upgraded["requested_outputs"] = [{
            "output_id": "open",
            "evidence_span": evidence_span,
            "open_description": raw_description,
        }]
    else:
        upgraded["requested_outputs"] = [{
            "output_id": output_id,
            "evidence_span": evidence_span,
        }]
    return upgraded


def canonicalize_scripted_live_goal_fixture(case: dict[str, Any], *, suite_id: str) -> dict[str, Any]:
    """Deep-copy one scripted case and make every live declaration canonical.

    The key includes suite/case/turn/goal identity so ambiguous semantic output
    choices cannot silently spread to unrelated fixtures.  Unknown historical
    effects remain ``open``; they are never mapped from an installed tool.
    """

    migrated = deepcopy(case)
    case_id = str(migrated.get("id") or "")
    contract = migrated.get("execution_contract") if isinstance(migrated.get("execution_contract"), dict) else {}
    turn_contracts = [row for row in list(contract.get("turn_contracts") or []) if isinstance(row, dict)]
    for turn_index, turn_contract in enumerate(turn_contracts, start=1):
        declaration_count = 0
        for model_step in list(turn_contract.get("model_steps") or []):
            if not isinstance(model_step, dict):
                continue
            for call in list(model_step.get("tool_calls") or []):
                if not isinstance(call, dict) or str(call.get("name") or "") != "declare_turn_goals":
                    continue
                declaration_count += 1
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                goals = [row for row in list(args.get("goals") or []) if isinstance(row, dict)]
                if not goals:
                    raise AssertionError(f"{suite_id}:{case_id}:t{turn_index}: declare_turn_goals has no goals")
                for goal in goals:
                    goal["requested_effect"] = _output_for_fixture_goal(
                        suite_id=suite_id,
                        case_id=case_id,
                        turn_index=turn_index,
                        goal=goal,
                    )
        if declaration_count != 1:
            raise AssertionError(
                f"{suite_id}:{case_id}:t{turn_index}: expected exactly one scripted declare_turn_goals, "
                f"got {declaration_count}"
            )
    return migrated


__all__ = ["canonicalize_scripted_live_goal_fixture"]
