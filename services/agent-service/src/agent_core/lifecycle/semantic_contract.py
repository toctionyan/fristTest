from __future__ import annotations

"""Validated, frozen semantic contract for one user turn.

This module deliberately contains no capability or tool selection.  It stores
what the user asked for after candidate validation.  Execution failures may
change plan progress, but must not rewrite this contract.
"""

from copy import deepcopy
from typing import Any, Iterable

from agent_core.kernel.semantic_contract import (
    FROZEN_SEMANTIC_CONTRACT_VERSION,
    assert_semantic_contract_integrity,
    compute_semantic_digest,
    find_goal_dependency_cycle,
    semantic_contract_integrity,
    semantic_goals,
)


def _text(value: Any, *, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def normalize_requested_effect(raw: Any, *, description: str = "") -> dict[str, str]:
    """Normalize an open business-effect identity without language classification.

    The values are open strings.  The program validates shape only; it does not
    infer an operation from keywords or coerce an unknown effect into a nearby
    registered category.
    """
    source = raw if isinstance(raw, dict) else {}
    effect = {
        "domain": _text(source.get("domain"), limit=120),
        "operation": _text(source.get("operation"), limit=160),
        "object_type": _text(source.get("object_type"), limit=160),
        "raw_description": _text(source.get("raw_description") or description),
    }
    if not effect["operation"]:
        raise ValueError("requested_effect.operation_required")
    if not effect["domain"]:
        effect["domain"] = "open"
    if not effect["object_type"]:
        effect["object_type"] = "unspecified"
    return effect


def _normalized_goal(goal: dict[str, Any]) -> dict[str, Any]:
    goal_id = _text(goal.get("goal_id"), limit=200)
    description = _text(goal.get("description"))
    evidence_span = _text(goal.get("evidence_span"))
    if not goal_id:
        raise ValueError("goal_id_required")
    if not description:
        raise ValueError(f"goal_description_required:{goal_id}")
    if not evidence_span:
        raise ValueError(f"goal_evidence_required:{goal_id}")
    requested_effect = normalize_requested_effect(goal.get("requested_effect"), description=description)
    row: dict[str, Any] = {
        "goal_id": goal_id,
        "description": description,
        "evidence_span": evidence_span,
        "requested_effect": requested_effect,
        "expected_result_cardinality": _text(goal.get("expected_result_cardinality") or "none", limit=40),
        "required": bool(goal.get("required", True)),
        "depends_on": [
            _text(item, limit=200)
            for item in list(goal.get("depends_on") or [])
            if _text(item, limit=200)
        ],
    }
    continuation_of = _text(goal.get("continuation_of"), limit=200)
    if continuation_of:
        row["continuation_of"] = continuation_of
    for key in ("target_candidate", "input_candidates", "condition", "execution_commitment"):
        value = goal.get(key)
        if value not in (None, "", [], {}):
            row[key] = deepcopy(value)
    # A legacy execution category may be retained as metadata for adapters, but
    # it is not part of the formal semantic identity and is never inferred here.
    legacy = _text(goal.get("goal_type"), limit=80)
    if legacy:
        row["compatibility"] = {"legacy_goal_type": legacy}
    return row



def freeze_semantic_contract(
    *,
    turn: int,
    user_text: str,
    summary: str,
    goals: Iterable[dict[str, Any]],
    alignment_proof: dict[str, Any],
    goal_changes: Iterable[dict[str, Any]] | None = None,
    blocker_resolutions: Iterable[dict[str, Any]] | None = None,
    focus_change: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_goals = [_normalized_goal(dict(goal)) for goal in goals]
    goal_ids = [goal["goal_id"] for goal in normalized_goals]
    if len(goal_ids) != len(set(goal_ids)):
        raise ValueError("duplicate_goal_id")
    known = set(goal_ids)
    for goal in normalized_goals:
        unknown = [item for item in goal["depends_on"] if item not in known]
        if unknown:
            raise ValueError(f"unknown_goal_dependency:{goal['goal_id']}:{','.join(unknown)}")
    cycle = find_goal_dependency_cycle(normalized_goals)
    if cycle:
        raise ValueError(f"goal_dependency_cycle:{'->'.join(cycle)}")

    contract: dict[str, Any] = {
        "version": FROZEN_SEMANTIC_CONTRACT_VERSION,
        "authority": "sole_formal_turn_semantics",
        "immutable": True,
        "turn": int(turn),
        "user_text": _text(user_text, limit=20_000),
        "summary": _text(summary),
        "goals": normalized_goals,
        "goal_changes": [deepcopy(row) for row in list(goal_changes or []) if isinstance(row, dict)],
        "blocker_resolutions": [
            deepcopy(row) for row in list(blocker_resolutions or []) if isinstance(row, dict)
        ],
        "focus_change": deepcopy(focus_change) if isinstance(focus_change, dict) else None,
        "alignment_proof": deepcopy(alignment_proof),
        "semantic_rewrite_allowed_after_freeze": False,
    }
    contract["semantic_digest"] = compute_semantic_digest(contract)
    contract["semantic_contract_id"] = f"semantic:{int(turn)}:{contract['semantic_digest'][:20]}"
    return contract



def semantic_contract_ready(state: dict[str, Any]) -> bool:
    contract = state.get("frozen_semantic_contract")
    return bool(
        isinstance(contract, dict)
        and contract.get("version") == FROZEN_SEMANTIC_CONTRACT_VERSION
        and contract.get("authority") == "sole_formal_turn_semantics"
        and semantic_contract_integrity(contract).get("ok")
    )


def goal_declaration_projection_from_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Create the same-turn declaration projection consumed by planning.

    This is not persisted as an authority and never reads retired state. It is
    derived only from the frozen semantic contract so execution planning cannot
    reinterpret user intent.
    """
    rows: list[dict[str, Any]] = []
    for goal in semantic_goals(contract):
        compatibility = goal.get("compatibility") if isinstance(goal.get("compatibility"), dict) else {}
        rows.append(
            {
                "goal_id": goal["goal_id"],
                "description": goal["description"],
                "evidence_span": goal["evidence_span"],
                "goal_type": str(compatibility.get("legacy_goal_type") or "open"),
                "requested_effect": deepcopy(goal["requested_effect"]),
                "expected_result_cardinality": goal.get("expected_result_cardinality") or "none",
                "required": bool(goal.get("required", True)),
                "depends_on": list(goal.get("depends_on") or []),
            }
        )
    return {
        "version": "goal-declaration-projection@1",
        "authority": "derived_from_frozen_semantic_contract",
        "formal_semantic_contract_id": contract.get("semantic_contract_id"),
        "formal_semantic_digest": contract.get("semantic_digest"),
        "turn": int(contract.get("turn") or 0),
        "summary": _text(contract.get("summary")),
        "goals": rows,
        "alignment_proof": deepcopy(contract.get("alignment_proof") or {}),
    }


__all__ = [
    "FROZEN_SEMANTIC_CONTRACT_VERSION",
    "assert_semantic_contract_integrity",
    "compute_semantic_digest",
    "find_goal_dependency_cycle",
    "freeze_semantic_contract",
    "goal_declaration_projection_from_contract",
    "normalize_requested_effect",
    "semantic_contract_integrity",
    "semantic_contract_ready",
    "semantic_goals",
]
