from __future__ import annotations

"""Canonical historical-reference expressions and deterministic resolution proofs.

The language model proposes a typed relationship to already customer-visible
results.  Runtime resolves that relationship over ``VisibleResultRef`` rows and
returns a proof.  This module never falls back to a newer/similar result and
never dispatches a business capability.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Iterable

from agent_core.context.visible_result_refs import visible_result_scope_key

REFERENCE_EXPRESSION_VERSION = "reference-expression@1"
REFERENT_RESOLUTION_PROOF_VERSION = "referent-resolution-proof@1"

REFERENCE_TYPES = {
    "explicit_result_ref",
    "temporal_visible_result",
    "ordinal_visible_member",
    "explicit_visible_member",
}
TEMPORAL_RELATIONS = {
    "latest",
    "previous",
    "previous_previous",
    "visible_turn_offset",
    "first_visible",
    "last_same_type",
    "explicit_turn",
}
RESOLUTION_STATUSES = {
    "UNIQUE",
    "AMBIGUOUS",
    "NOT_FOUND",
    "TYPE_CONFLICT",
    "CARDINALITY_CONFLICT",
    "INVALID_EXPRESSION",
}


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def referent_resolution_proof_integrity(
    proof: Any,
    *,
    reference_expression: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a serialized resolution proof before it enters frozen semantics."""

    if not isinstance(proof, dict):
        return {"ok": False, "code": "REFERENT_RESOLUTION_PROOF_REQUIRED"}
    if str(proof.get("version") or "") != REFERENT_RESOLUTION_PROOF_VERSION:
        return {"ok": False, "code": "REFERENT_RESOLUTION_PROOF_VERSION_INVALID"}
    status = str(proof.get("resolution_status") or "")
    if status not in RESOLUTION_STATUSES:
        return {"ok": False, "code": "REFERENT_RESOLUTION_STATUS_INVALID"}
    if proof.get("auto_substitution_used") is not False:
        return {"ok": False, "code": "REFERENT_RESOLUTION_SUBSTITUTION_FORBIDDEN"}
    stored_digest = str(proof.get("proof_digest") or "").strip()
    if not stored_digest:
        return {"ok": False, "code": "REFERENT_RESOLUTION_PROOF_DIGEST_REQUIRED"}
    digest_payload = deepcopy(proof)
    digest_payload.pop("proof_digest", None)
    computed_digest = _digest(digest_payload)
    if stored_digest != computed_digest:
        return {
            "ok": False,
            "code": "REFERENT_RESOLUTION_PROOF_DIGEST_INVALID",
            "stored_digest": stored_digest,
            "computed_digest": computed_digest,
        }
    if (
        reference_expression is not None
        and proof.get("reference_expression") != reference_expression
    ):
        return {"ok": False, "code": "REFERENT_RESOLUTION_EXPRESSION_MISMATCH"}
    result_ref = str(proof.get("resolved_result_ref") or "").strip()
    members = [
        str(value)
        for value in list(proof.get("resolved_member_handles") or [])
        if str(value)
    ]
    if status == "UNIQUE":
        if not result_ref or not members:
            return {
                "ok": False,
                "code": "REFERENT_RESOLUTION_UNIQUE_RESULT_INCOMPLETE",
            }
    elif result_ref or members:
        return {
            "ok": False,
            "code": "REFERENT_RESOLUTION_NON_UNIQUE_RESULT_PRESENT",
        }
    return {
        "ok": True,
        "code": "REFERENT_RESOLUTION_PROOF_VALID",
        "proof_digest": stored_digest,
    }


def normalize_reference_expression(
    raw: Any,
    *,
    user_text: str,
    expected_object_type: str | None = None,
    expected_cardinality: str | None = None,
) -> dict[str, Any]:
    """Normalize one model-proposed reference expression.

    Shape validation is intentionally strict.  Runtime does not infer a missing
    temporal relation or silently reinterpret a normal pronoun as an explicit
    historical return.
    """

    if not isinstance(raw, dict):
        raise ValueError("reference_expression_object_required")
    reference_type = _text(raw.get("reference_type"), limit=80)
    if reference_type not in REFERENCE_TYPES:
        raise ValueError("reference_expression_type_invalid")
    evidence_span = _text(raw.get("evidence_span"), limit=240)
    if not evidence_span or evidence_span not in str(user_text or ""):
        raise ValueError("reference_expression_evidence_not_in_current_turn")

    expression: dict[str, Any] = {
        "version": REFERENCE_EXPRESSION_VERSION,
        "reference_type": reference_type,
        "evidence_span": evidence_span,
        "object_type": (
            _text(raw.get("object_type") or expected_object_type, limit=160)
            or None
        ),
        "expected_cardinality": _text(
            raw.get("expected_cardinality") or expected_cardinality or "unknown", limit=40
        ).lower(),
    }
    if expression["expected_cardinality"] not in {"single", "collection", "unknown"}:
        raise ValueError("reference_expression_cardinality_invalid")

    if reference_type == "explicit_result_ref":
        result_ref = _text(raw.get("result_ref"), limit=500)
        if not result_ref:
            raise ValueError("reference_expression_result_ref_required")
        expression["result_ref"] = result_ref
    elif reference_type == "explicit_visible_member":
        member_handle = _text(raw.get("member_handle"), limit=500)
        if not member_handle:
            raise ValueError("reference_expression_member_handle_required")
        expression["member_handle"] = member_handle
        source_result_ref = _text(raw.get("source_result_ref"), limit=500)
        if source_result_ref:
            expression["source_result_ref"] = source_result_ref
    else:
        relation = _text(raw.get("temporal_relation"), limit=80)
        if relation not in TEMPORAL_RELATIONS:
            raise ValueError("reference_expression_temporal_relation_invalid")
        expression["temporal_relation"] = relation
        if relation == "visible_turn_offset":
            offset = raw.get("visible_turn_offset")
            if (
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or offset < 0
                or offset > 50
            ):
                raise ValueError("reference_expression_visible_turn_offset_invalid")
            expression["visible_turn_offset"] = int(offset)
        if relation == "explicit_turn":
            turn = raw.get("source_turn")
            if isinstance(turn, bool) or not isinstance(turn, int) or turn < 0:
                raise ValueError("reference_expression_source_turn_invalid")
            expression["source_turn"] = int(turn)
        if reference_type == "ordinal_visible_member":
            position = raw.get("position")
            if (
                isinstance(position, bool)
                or not isinstance(position, int)
                or position < 1
                or position > 100
            ):
                raise ValueError("reference_expression_position_invalid")
            expression["position"] = int(position)
    return expression


def _resource_types(ref: dict[str, Any]) -> set[str]:
    values = (
        ref.get("resource_types")
        if isinstance(ref.get("resource_types"), list)
        else []
    )
    values = [*values, *list(ref.get("member_resource_types") or [])]
    return {str(value).strip() for value in values if str(value).strip()}


def _matches_object_type(ref: dict[str, Any], object_type: str | None) -> bool:
    expected = str(object_type or "").strip()
    if not expected or expected in {"unspecified", "unknown"}:
        return True
    known = _resource_types(ref)
    # A requested object type needs positive authority-backed member metadata.
    # Unknown legacy metadata cannot prove a typed reference and therefore must
    # fail closed instead of being treated as a compatible candidate.
    return bool(known and expected in known)


def _distinct_turns(refs: list[dict[str, Any]]) -> list[int]:
    return sorted({int(row.get("source_turn") or 0) for row in refs}, reverse=True)


def _temporal_candidates(
    expression: dict[str, Any],
    refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    relation = str(expression.get("temporal_relation") or "")
    object_type = expression.get("object_type")
    typed = [row for row in refs if _matches_object_type(row, object_type)]
    turns = _distinct_turns(refs)
    typed_turns = _distinct_turns(typed)
    source_rows = refs
    if relation in {"latest", "previous"}:
        # Visible refs are historical by definition.  The latest visible turn
        # is therefore the user's previous public result.  Keep ``latest`` as
        # the discourse-oriented spelling and ``previous`` as the natural-
        # language spelling; both resolve to offset zero.
        selected_turn = turns[0] if turns else None
    elif relation == "previous_previous":
        selected_turn = turns[1] if len(turns) > 1 else None
    elif relation == "visible_turn_offset":
        offset = int(expression.get("visible_turn_offset") or 0)
        selected_turn = turns[offset] if offset < len(turns) else None
    elif relation == "first_visible":
        selected_turn = turns[-1] if turns else None
    elif relation == "last_same_type":
        selected_turn = typed_turns[0] if typed_turns else None
        source_rows = typed
    elif relation == "explicit_turn":
        selected_turn = int(expression.get("source_turn") or 0)
    else:
        selected_turn = None
    if selected_turn is None:
        return []
    return [row for row in source_rows if int(row.get("source_turn") or 0) == selected_turn]


def _candidate_row(ref: dict[str, Any], expression: dict[str, Any]) -> dict[str, Any]:
    known_types = sorted(_resource_types(ref))
    return {
        "result_ref": str(ref.get("result_ref") or ""),
        "source_turn": int(ref.get("source_turn") or 0),
        "shape": str(ref.get("shape") or ""),
        "member_count": len(list(ref.get("member_handles") or [])),
        "resource_types": known_types,
        "checks": {
            "visible_to_user": True,
            "scope_valid": True,
            "not_expired": True,
            "object_type_match": _matches_object_type(
                ref, expression.get("object_type")
            ),
            "object_type_proven": bool(known_types),
        },
    }


def _cardinality_matches(ref: dict[str, Any], expected: str) -> bool:
    if expected == "unknown":
        return True
    count = len(list(ref.get("member_handles") or []))
    if expected == "single":
        return count == 1
    if expected == "collection":
        return str(ref.get("shape") or "") == "collection"
    return False


def resolve_reference_expression(
    expression: dict[str, Any],
    *,
    visible_result_refs: Iterable[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Resolve one normalized expression without auto-substitution."""

    refs = [
        deepcopy(row)
        for row in list(visible_result_refs or [])
        if isinstance(row, dict) and str(row.get("result_ref") or "")
    ]
    refs.sort(
        key=lambda row: (
            int(row.get("source_turn") or 0),
            -int(row.get("discourse_recency_rank") or 0),
        ),
        reverse=True,
    )
    try:
        reference_type = str(expression.get("reference_type") or "")
        if reference_type not in REFERENCE_TYPES:
            raise ValueError("invalid_reference_type")
    except Exception as exc:
        payload = {
            "version": REFERENT_RESOLUTION_PROOF_VERSION,
            "reference_expression": deepcopy(expression),
            "candidate_refs": [],
            "resolution_status": "INVALID_EXPRESSION",
            "resolved_result_ref": None,
            "resolved_member_handles": [],
            "auto_substitution_used": False,
            "reason": str(exc),
        }
        payload["proof_digest"] = _digest(payload)
        return payload

    candidates: list[dict[str, Any]] = []
    if reference_type == "explicit_result_ref":
        wanted = str(expression.get("result_ref") or "")
        candidates = [row for row in refs if str(row.get("result_ref") or "") == wanted]
    elif reference_type == "explicit_visible_member":
        member = str(expression.get("member_handle") or "")
        source = str(expression.get("source_result_ref") or "")
        parents = [
            row
            for row in refs
            if member in {str(value) for value in list(row.get("member_handles") or [])}
            and (not source or str(row.get("result_ref") or "") == source)
        ]
        candidates = parents
    else:
        candidates = _temporal_candidates(expression, refs)

    type_filtered = [
        row
        for row in candidates
        if _matches_object_type(row, expression.get("object_type"))
    ]
    expected = str(expression.get("expected_cardinality") or "unknown")
    if reference_type in {"ordinal_visible_member", "explicit_visible_member"}:
        # Cardinality describes the resolved member, not its parent visible
        # collection.  The parent must simply contain the proposed member.
        cardinality_filtered = [
            row
            for row in type_filtered
            if list(row.get("member_handles") or [])
        ]
    else:
        cardinality_filtered = [
            row for row in type_filtered if _cardinality_matches(row, expected)
        ]

    equivalent_groups: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}
    for row in cardinality_filtered:
        equivalent_groups.setdefault(visible_result_scope_key(row), []).append(row)
    distinct_candidate_groups = list(equivalent_groups.values())

    status = "NOT_FOUND"
    resolved_result_ref: str | None = None
    resolved_members: list[str] = []
    resolved_position: int | None = None
    if candidates and not type_filtered:
        status = "TYPE_CONFLICT"
    elif type_filtered and not cardinality_filtered:
        status = "CARDINALITY_CONFLICT"
    elif len(distinct_candidate_groups) == 1:
        equivalent = distinct_candidate_groups[0]
        preferred_shape = (
            "one" if expected == "single"
            else "collection" if expected == "collection"
            else ""
        )
        selected = next(
            (row for row in equivalent if str(row.get("shape") or "") == preferred_shape),
            equivalent[0],
        )
        members = [
            str(value)
            for value in list(
                selected.get("canonical_order")
                or selected.get("member_handles")
                or []
            )
            if str(value)
        ]
        if reference_type == "ordinal_visible_member":
            position = int(expression.get("position") or 0)
            if position < 1 or position > len(members):
                status = "CARDINALITY_CONFLICT"
            else:
                status = "UNIQUE"
                resolved_result_ref = str(selected.get("result_ref") or "")
                resolved_members = [members[position - 1]]
                resolved_position = position
        elif reference_type == "explicit_visible_member":
            member = str(expression.get("member_handle") or "")
            status = "UNIQUE"
            resolved_result_ref = str(selected.get("result_ref") or "")
            resolved_members = [member]
        else:
            status = "UNIQUE"
            resolved_result_ref = str(selected.get("result_ref") or "")
            resolved_members = members
    elif len(distinct_candidate_groups) > 1:
        status = "AMBIGUOUS"

    payload: dict[str, Any] = {
        "version": REFERENT_RESOLUTION_PROOF_VERSION,
        "reference_expression": deepcopy(expression),
        "candidate_refs": [_candidate_row(row, expression) for row in candidates],
        "resolution_status": status,
        "resolved_result_ref": resolved_result_ref,
        "resolved_member_handles": resolved_members,
        "resolved_position": resolved_position,
        "auto_substitution_used": False,
        "equivalent_candidate_scope_count": len(distinct_candidate_groups),
        "equivalent_aliases_collapsed": sum(max(0, len(group) - 1) for group in distinct_candidate_groups),
        "selection_policy": "typed_relation_then_semantic_scope_equivalence_then_runtime_validation_no_fallback",
    }
    payload["proof_digest"] = _digest(payload)
    return payload


def reference_resolution_prompt_contract() -> dict[str, Any]:
    return {
        "version": REFERENCE_EXPRESSION_VERSION,
        "authority": "model_proposes_relation_runtime_resolves",
        "reference_types": sorted(REFERENCE_TYPES),
        "temporal_relations": sorted(TEMPORAL_RELATIONS),
        "rules": [
            (
                "Use a reference_expression when the current user explicitly "
                "refers to a prior visible result/member/turn."
            ),
            (
                "Do not copy a newer result_ref as a substitute for an expired "
                "or unresolved historical reference."
            ),
            (
                "Only a UNIQUE runtime proof may become a frozen resolved "
                "reference; ambiguity requires clarification."
            ),
        ],
    }


__all__ = [
    "REFERENCE_EXPRESSION_VERSION",
    "REFERENT_RESOLUTION_PROOF_VERSION",
    "REFERENCE_TYPES",
    "TEMPORAL_RELATIONS",
    "RESOLUTION_STATUSES",
    "normalize_reference_expression",
    "referent_resolution_proof_integrity",
    "reference_resolution_prompt_contract",
    "resolve_reference_expression",
]
