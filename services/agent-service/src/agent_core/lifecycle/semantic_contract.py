from __future__ import annotations

"""Validated, frozen semantic contract for one user turn.

This module deliberately contains no capability or tool selection. It stores
what the user asked for after candidate validation. Execution failures may
change plan progress, but must not rewrite this contract.
"""

from copy import deepcopy
from typing import Any, Iterable

from agent_core.context.reference_resolution import (
    normalize_reference_expression,
    referent_resolution_proof_integrity,
)
from agent_core.kernel.semantic_contract import (
    FROZEN_SEMANTIC_CONTRACT_VERSION,
    GOAL_TARGET_COMPATIBILITY_VERSION,
    assert_semantic_contract_integrity,
    compute_semantic_digest,
    derive_goal_target_identity,
    find_goal_dependency_cycle,
    prove_goal_target_compatibility,
    semantic_contract_integrity,
    semantic_goals,
)
from agent_core.lifecycle.condition_expression import (
    condition_goal_dependencies,
    normalize_condition_expression,
)


def _text(value: Any, *, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def normalize_requested_effect(raw: Any, *, description: str = "") -> dict[str, Any]:
    """Normalize capability-independent requested outputs or a legacy checkpoint.

    New provider declarations use ``effect_kind/subject_type/requested_outputs``.
    The legacy three-field identity remains readable only for historical/direct
    migration callers; provider schema no longer exposes it.
    """
    source = raw if isinstance(raw, dict) else {}
    if "requested_outputs" in source:
        effect_kind = _text(source.get("effect_kind"), limit=80).casefold()
        subject_type = _text(source.get("subject_type"), limit=160).casefold()
        raw_description = _text(source.get("raw_description") or description)
        values = source.get("requested_outputs")
        if not effect_kind:
            raise ValueError("requested_effect.effect_kind_required")
        if not subject_type:
            raise ValueError("requested_effect.subject_type_required")
        if not isinstance(values, list) or not values or len(values) > 8:
            raise ValueError("requested_effect.requested_outputs_required")
        outputs: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, raw_output in enumerate(values):
            if not isinstance(raw_output, dict):
                raise ValueError(f"requested_effect.output_invalid:{index}")
            output_id = _text(raw_output.get("output_id"), limit=240).casefold()
            evidence_span = _text(raw_output.get("evidence_span"), limit=240)
            open_description = _text(raw_output.get("open_description"), limit=500)
            if not output_id or not evidence_span:
                raise ValueError(f"requested_effect.output_incomplete:{index}")
            if output_id in seen:
                raise ValueError(f"requested_effect.output_duplicate:{output_id}")
            if output_id == "open" and not open_description:
                raise ValueError("requested_effect.open_description_required")
            if output_id != "open" and open_description:
                raise ValueError("requested_effect.open_description_only_for_open")
            seen.add(output_id)
            row = {"output_id": output_id, "evidence_span": evidence_span}
            if open_description:
                row["open_description"] = open_description
            outputs.append(row)
        return {
            "effect_kind": effect_kind,
            "subject_type": subject_type,
            "requested_outputs": outputs,
            "raw_description": raw_description,
        }

    # Compatibility-only historical representation. New provider schemas do
    # not expose these fields, so this branch cannot become a second writer
    # authority for newly declared turns.
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


def _normalized_goal_base(goal: dict[str, Any]) -> dict[str, Any]:
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
    for key in (
        "target_candidate",
        "input_candidates",
        "condition",
        "execution_commitment",
        "reference_expression",
        "referent_resolution_proof",
        "resolved_reference",
    ):
        value = goal.get(key)
        if value not in (None, "", [], {}):
            row[key] = deepcopy(value)
    legacy = _text(goal.get("goal_type"), limit=80)
    if legacy:
        row["compatibility"] = {"legacy_goal_type": legacy}
    return row


def _normalize_reference_fields(goal: dict[str, Any], *, user_text: str) -> None:
    expression = goal.get("reference_expression")
    proof = goal.get("referent_resolution_proof")
    resolved = goal.get("resolved_reference")
    if expression is None and proof is None and resolved is None:
        return
    if expression is not None:
        goal["reference_expression"] = normalize_reference_expression(
            expression,
            user_text=user_text,
            expected_object_type=str(
                (goal.get("requested_effect") or {}).get("subject_type")
                or (goal.get("requested_effect") or {}).get("object_type")
                or ""
            ),
            expected_cardinality=str(goal.get("expected_result_cardinality") or "unknown"),
        )
    integrity = referent_resolution_proof_integrity(
        proof,
        reference_expression=goal.get("reference_expression") if isinstance(goal.get("reference_expression"), dict) else None,
    )
    if not integrity.get("ok"):
        raise ValueError(
            f"referent_resolution_proof_invalid:{goal['goal_id']}:"
            f"{integrity.get('code') or 'UNKNOWN'}"
        )
    status = str(proof.get("resolution_status") or "")
    if status != "UNIQUE":
        raise ValueError(f"referent_resolution_not_unique:{goal['goal_id']}:{status}")
    if not isinstance(resolved, dict):
        raise ValueError(f"resolved_reference_required:{goal['goal_id']}")
    result_ref = _text(resolved.get("result_ref"), limit=500)
    members = [str(value) for value in list(resolved.get("member_handles") or []) if str(value)]
    proof_result_ref = _text(proof.get("resolved_result_ref"), limit=500)
    proof_members = [str(value) for value in list(proof.get("resolved_member_handles") or []) if str(value)]
    proof_digest = _text(resolved.get("proof_digest") or proof.get("proof_digest"), limit=128)
    if not result_ref or not members or not proof_digest:
        raise ValueError(f"resolved_reference_incomplete:{goal['goal_id']}")
    if result_ref != proof_result_ref or members != proof_members or proof_digest != proof.get("proof_digest"):
        raise ValueError(f"resolved_reference_proof_mismatch:{goal['goal_id']}")
    goal["referent_resolution_proof"] = deepcopy(proof)
    goal["resolved_reference"] = {
        "result_ref": result_ref,
        "member_handles": members,
        "proof_digest": proof_digest,
        **(
            {"position": int(resolved["position"])}
            if isinstance(resolved.get("position"), int) and not isinstance(resolved.get("position"), bool)
            else {}
        ),
    }


def _normalize_condition(goal: dict[str, Any], *, known_goal_ids: set[str]) -> None:
    if "condition" not in goal:
        return
    condition = normalize_condition_expression(goal["condition"], known_goal_ids=known_goal_ids)
    required_dependencies = condition_goal_dependencies(condition)
    declared = set(goal.get("depends_on") or [])
    missing = sorted(required_dependencies - declared)
    if missing:
        raise ValueError(f"condition_dependency_not_declared:{goal['goal_id']}:{','.join(missing)}")
    goal["condition"] = condition


def freeze_semantic_contract(
    *,
    turn: int,
    user_text: str,
    summary: str,
    goals: Iterable[dict[str, Any]],
    alignment_proof: dict[str, Any],
    granularity_proof: dict[str, Any] | None = None,
    goal_changes: Iterable[dict[str, Any]] | None = None,
    blocker_resolutions: Iterable[dict[str, Any]] | None = None,
    focus_change: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_goals = [_normalized_goal_base(dict(goal)) for goal in goals]
    goal_ids = [goal["goal_id"] for goal in normalized_goals]
    if len(goal_ids) != len(set(goal_ids)):
        raise ValueError("duplicate_goal_id")
    known = set(goal_ids)
    for goal in normalized_goals:
        unknown = [item for item in goal["depends_on"] if item not in known]
        if unknown:
            raise ValueError(f"unknown_goal_dependency:{goal['goal_id']}:{','.join(unknown)}")
        _normalize_condition(goal, known_goal_ids=known)
        _normalize_reference_fields(goal, user_text=user_text)
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
        "granularity_proof": deepcopy(granularity_proof or {"verdict": "exact", "source": "compatibility_default"}),
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
    """Create the same-turn declaration projection consumed by planning."""

    rows: list[dict[str, Any]] = []
    for goal in semantic_goals(contract):
        compatibility = goal.get("compatibility") if isinstance(goal.get("compatibility"), dict) else {}
        row = {
            "goal_id": goal["goal_id"],
            "description": goal["description"],
            "evidence_span": goal["evidence_span"],
            "goal_type": str(compatibility.get("legacy_goal_type") or "open"),
            "requested_effect": deepcopy(goal["requested_effect"]),
            "expected_result_cardinality": goal.get("expected_result_cardinality") or "none",
            "required": bool(goal.get("required", True)),
            "depends_on": list(goal.get("depends_on") or []),
        }
        for key in (
            "condition",
            "reference_expression",
            "referent_resolution_proof",
            "resolved_reference",
        ):
            if key in goal:
                row[key] = deepcopy(goal[key])
        rows.append(row)
    return {
        "version": "goal-declaration-projection@1",
        "authority": "derived_from_frozen_semantic_contract",
        "formal_semantic_contract_id": contract.get("semantic_contract_id"),
        "formal_semantic_digest": contract.get("semantic_digest"),
        "turn": int(contract.get("turn") or 0),
        "summary": _text(contract.get("summary")),
        "goals": rows,
        "alignment_proof": deepcopy(contract.get("alignment_proof") or {}),
        "granularity_proof": deepcopy(contract.get("granularity_proof") or {}),
    }


__all__ = [
    "FROZEN_SEMANTIC_CONTRACT_VERSION",
    "GOAL_TARGET_COMPATIBILITY_VERSION",
    "assert_semantic_contract_integrity",
    "compute_semantic_digest",
    "find_goal_dependency_cycle",
    "derive_goal_target_identity",
    "freeze_semantic_contract",
    "goal_declaration_projection_from_contract",
    "normalize_requested_effect",
    "prove_goal_target_compatibility",
    "semantic_contract_integrity",
    "semantic_contract_ready",
    "semantic_goals",
]
