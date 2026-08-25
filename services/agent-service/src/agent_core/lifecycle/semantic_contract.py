from __future__ import annotations

"""Validated, frozen semantic contract for one user turn.

This module deliberately contains no capability or tool selection. It stores
what the user asked for after candidate validation. Execution failures may
change plan progress, but must not rewrite this contract.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Iterable

from agent_core.context.reference_resolution import (
    normalize_reference_expression,
    referent_resolution_proof_integrity,
)
from agent_core.kernel.semantic_contract import (
    FROZEN_SEMANTIC_CONTRACT_VERSION,
    GOAL_INPUT_BINDING_AUTHORITY,
    GOAL_INPUT_BINDING_VERSION,
    GOAL_TARGET_COMPATIBILITY_VERSION,
    LEGACY_DEPENDENCY_COMPATIBILITY_AUTHORITY,
    assert_semantic_contract_integrity,
    compute_semantic_digest,
    derive_goal_target_identity,
    find_goal_dependency_cycle,
    goal_dependency_ids,
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
    """Normalize one capability-blind effect without consulting installed Tools.

    Historical/direct callers may still provide only the open
    ``domain/operation/object_type`` triple. New provider declarations also
    carry ``requested_outputs``; those canonical semantic output IDs become the
    exact post-freeze capability-coverage identity. The compatibility triple is
    preserved for old readers but never grants execution authority.
    """
    source = raw if isinstance(raw, dict) else {}
    raw_description = _text(source.get("raw_description") or description)
    values = source.get("requested_outputs")
    if isinstance(values, list):
        if not values or len(values) > 8:
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

        first_semantic = next((row["output_id"] for row in outputs if row["output_id"] != "open"), "")
        semantic_domain, semantic_operation = (
            first_semantic.split(".", 1) if "." in first_semantic else ("", "")
        )
        domain = _text(source.get("domain"), limit=120) or semantic_domain or "open"
        operation = _text(source.get("operation"), limit=160) or (
            semantic_operation if len(outputs) == 1 and semantic_operation else "semantic_output_set"
        )
        object_type = _text(
            source.get("object_type") or source.get("subject_type"), limit=160
        ) or "unspecified"
        result: dict[str, Any] = {
            "domain": domain,
            "operation": operation,
            "object_type": object_type,
            "requested_outputs": outputs,
            "raw_description": raw_description,
        }
        effect_kind = _text(source.get("effect_kind"), limit=80).casefold()
        subject_type = _text(source.get("subject_type"), limit=160).casefold()
        if effect_kind:
            result["effect_kind"] = effect_kind
        if subject_type:
            result["subject_type"] = subject_type
        return result

    effect = {
        "domain": _text(source.get("domain"), limit=120),
        "operation": _text(source.get("operation"), limit=160),
        "object_type": _text(source.get("object_type"), limit=160),
        "raw_description": raw_description,
    }
    if not effect["operation"]:
        raise ValueError("requested_effect.operation_required")
    if not effect["domain"]:
        effect["domain"] = "open"
    if not effect["object_type"]:
        effect["object_type"] = "unspecified"
    return effect


def _normalize_input_binding(
    raw: Any,
    *,
    goal_id: str,
    index: int,
    user_text: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"goal_input_binding_invalid:{goal_id}:{index}")
    port = _text(raw.get("port"), limit=240)
    relation_kind = _text(raw.get("relation_kind"), limit=80)
    expected_cardinality = _text(raw.get("expected_cardinality"), limit=40).casefold()
    evidence_span = _text(raw.get("evidence_span"), limit=500)
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    source_kind = _text(source.get("kind"), limit=80)
    if not port or not evidence_span or evidence_span not in user_text:
        raise ValueError(f"goal_input_binding_evidence_invalid:{goal_id}:{index}")
    if expected_cardinality not in {"single", "collection", "unknown"}:
        raise ValueError(f"goal_input_binding_cardinality_invalid:{goal_id}:{index}")
    normalized_source: dict[str, Any]
    if source_kind == "current_goal_output":
        producer_goal_id = _text(source.get("producer_goal_id"), limit=200)
        output_id = _text(source.get("output_id"), limit=240).casefold()
        if not producer_goal_id or not output_id:
            raise ValueError(f"goal_input_binding_producer_incomplete:{goal_id}:{index}")
        if relation_kind not in {"result_reference", "result_value_input"}:
            raise ValueError(f"goal_input_binding_relation_invalid:{goal_id}:{index}")
        normalized_source = {
            "kind": source_kind,
            "producer_goal_id": producer_goal_id,
            "output_id": output_id,
        }
    elif source_kind == "current_text":
        subject_ref = _text(source.get("subject_ref"), limit=500)
        if not subject_ref or relation_kind != "shared_subject":
            raise ValueError(f"goal_input_binding_current_text_invalid:{goal_id}:{index}")
        normalized_source = {"kind": source_kind, "subject_ref": subject_ref}
    elif source_kind == "visible_result_ref":
        result_ref = _text(source.get("result_ref"), limit=500)
        if not result_ref or relation_kind != "historical_result":
            raise ValueError(f"goal_input_binding_visible_result_invalid:{goal_id}:{index}")
        normalized_source = {"kind": source_kind, "result_ref": result_ref}
    else:
        raise ValueError(f"goal_input_binding_source_invalid:{goal_id}:{index}")
    binding: dict[str, Any] = {
        "version": GOAL_INPUT_BINDING_VERSION,
        "port": port,
        "source": normalized_source,
        "relation_kind": relation_kind,
        "expected_cardinality": expected_cardinality,
        "evidence_span": evidence_span,
    }
    binding["binding_digest"] = sha256(
        json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return binding


def _normalized_goal_base(goal: dict[str, Any], *, user_text: str) -> dict[str, Any]:
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
    }
    if "input_bindings" in goal:
        if "depends_on" in goal:
            raise ValueError(f"raw_goal_dependency_forbidden:{goal_id}")
        raw_bindings = goal.get("input_bindings")
        if not isinstance(raw_bindings, list) or len(raw_bindings) > 8:
            raise ValueError(f"goal_input_bindings_invalid:{goal_id}")
        row["input_bindings"] = [
            _normalize_input_binding(
                value,
                goal_id=goal_id,
                index=index,
                user_text=user_text,
            )
            for index, value in enumerate(raw_bindings)
        ]
    else:
        row["depends_on"] = [
            _text(item, limit=200)
            for item in list(goal.get("depends_on") or [])
            if _text(item, limit=200)
        ]
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
    if "input_bindings" not in goal:
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
    raw_goals = [dict(goal) for goal in goals]
    binding_modes = {"input_bindings" in goal for goal in raw_goals}
    if len(binding_modes) > 1:
        raise ValueError("mixed_goal_dependency_authorities_forbidden")
    typed_bindings = binding_modes == {True}
    normalized_goals = [
        _normalized_goal_base(goal, user_text=user_text)
        for goal in raw_goals
    ]
    goal_ids = [goal["goal_id"] for goal in normalized_goals]
    if len(goal_ids) != len(set(goal_ids)):
        raise ValueError("duplicate_goal_id")
    known = set(goal_ids)
    goal_order = {goal_id: index for index, goal_id in enumerate(goal_ids)}
    outputs_by_goal = {
        goal["goal_id"]: {
            _text(row.get("output_id"), limit=240).casefold()
            for row in list((goal.get("requested_effect") or {}).get("requested_outputs") or [])
            if isinstance(row, dict) and _text(row.get("output_id"), limit=240)
        }
        for goal in normalized_goals
    }
    for goal in normalized_goals:
        unknown = [item for item in goal_dependency_ids(goal) if item not in known]
        if unknown:
            raise ValueError(f"unknown_goal_dependency:{goal['goal_id']}:{','.join(unknown)}")
        if typed_bindings:
            ports: set[str] = set()
            for binding in list(goal.get("input_bindings") or []):
                port = str(binding.get("port") or "")
                if port in ports:
                    raise ValueError(f"goal_input_binding_port_duplicate:{goal['goal_id']}:{port}")
                ports.add(port)
                source = binding.get("source") if isinstance(binding.get("source"), dict) else {}
                if source.get("kind") != "current_goal_output":
                    continue
                producer = str(source.get("producer_goal_id") or "")
                if producer == goal["goal_id"]:
                    raise ValueError(f"goal_input_binding_self_reference:{goal['goal_id']}")
                if goal_order.get(producer, len(goal_ids)) >= goal_order[goal["goal_id"]]:
                    raise ValueError(f"goal_input_binding_producer_must_precede_consumer:{goal['goal_id']}:{producer}")
                output_id = str(source.get("output_id") or "")
                if output_id not in outputs_by_goal.get(producer, set()):
                    raise ValueError(f"goal_input_binding_output_unknown:{goal['goal_id']}:{producer}:{output_id}")
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
        "dependency_authority": (
            GOAL_INPUT_BINDING_AUTHORITY
            if typed_bindings
            else LEGACY_DEPENDENCY_COMPATIBILITY_AUTHORITY
        ),
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
        }
        if isinstance(goal.get("input_bindings"), list):
            row["input_bindings"] = deepcopy(goal["input_bindings"])
            row["derived_dependency_goal_ids"] = goal_dependency_ids(goal)
        else:
            row["depends_on"] = list(goal.get("depends_on") or [])
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
