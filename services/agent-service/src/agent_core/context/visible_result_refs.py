from __future__ import annotations

"""Stable, user-visible result references.

A ``VisibleResultRef`` is not a focus pointer and is never a runtime target
selection policy.  It records only a ledger item that has actually crossed the
customer-visible release boundary.  The model may refer to it in a later turn;
the Runtime verifies scope, TTL, shape and version before a registered
Capability can consume it.
"""

from copy import deepcopy
from typing import Any

from agent_core.ledger import append_entries, execution_scope_for_state, find_handle, normalize_ledger, scope_for_state

_VISIBLE_KINDS = {"artifact", "view", "result", "eligibility", "offer", "receipt"}


def _shape(entry: dict[str, Any]) -> str:
    if str(entry.get("kind") or "") in {"view", "result", "eligibility"}:
        return "collection"
    return "one"


def _members(entry: dict[str, Any]) -> list[str]:
    if str(entry.get("kind") or "") in {"view", "result"}:
        return [str(value) for value in entry.get("member_handles") or [] if str(value)]
    if str(entry.get("kind") or "") == "eligibility":
        # Eligibility is evidence *about* a business target.  Once that
        # evidence crosses the customer-visible release boundary, an implicit
        # continuation (for example, "prepare the refund") must be able to
        # keep the verified target without pretending that the eligibility
        # record itself is an order.  The result_ref remains the eligibility
        # evidence handle; its sole discourse member is the scoped target.
        target_handle = str(entry.get("target_handle") or "")
        return [target_handle] if target_handle else []
    handle = str(entry.get("handle") or "")
    return [handle] if handle else []


def _origin(entry: dict[str, Any]) -> dict[str, Any] | None:
    value = entry.get("presentation_origin")
    return dict(value) if isinstance(value, dict) else None


def _entry_to_ref(entry: dict[str, Any]) -> dict[str, Any] | None:
    origin = _origin(entry)
    handle = str(entry.get("handle") or "")
    if origin is None or not handle:
        return None
    scope = dict(entry.get("scope") or {})
    members = _members(entry)
    member_labels = [str(value) for value in entry.get("labels") or [] if str(value)]
    source_target = entry.get("source_target") if isinstance(entry.get("source_target"), dict) else {}
    source_query = entry.get("query") if isinstance(entry.get("query"), dict) else {}
    source_expression = source_target.get("target") if isinstance(source_target.get("target"), dict) else {}
    if not source_expression and isinstance(source_query.get("target"), dict):
        source_expression = source_query["target"]
    lineage_result_refs = list(dict.fromkeys(
        str(source_expression.get(key) or "").strip()
        for key in ("left_handle", "right_handle")
        if str(source_expression.get(key) or "").strip()
    ))
    # Preserve the typed operation that produced a visible collection.  This
    # is structural provenance only; it never chooses a later target.  The
    # CapabilityGate uses it to prevent a comparison reversal from sorting a
    # singleton that was itself produced by sort/take instead of returning to
    # its verified parent collection.
    operation_fields = {
        "mode", "operator", "sort_field", "sort_direction", "limit",
        "position", "status",
    }
    # Preserve user-authored operation evidence (``sort_span``,
    # ``status_span`` and future domain-neutral ``*_span`` fields).  These
    # strings are provenance only.  A later turn may repeat one literally to
    # prove a structural return; Runtime never interprets them as aliases.
    operation_fields.update(
        key for key, value in source_expression.items()
        if str(key).endswith("_span") and isinstance(value, (str, int, float, bool))
    )
    source_operation = {
        key: source_expression.get(key)
        for key in sorted(operation_fields)
        if source_expression.get(key) not in (None, "")
    }
    # Turn zero is a valid bootstrap/fixture provenance value.  Do not use
    # truthiness fallback here: a later fact refresh updates ``updated_turn``
    # but must not rewrite when the customer originally saw the identity.
    origin_source_turn = origin.get("source_turn")
    source_turn = int(
        origin_source_turn
        if origin_source_turn is not None
        else entry.get("updated_turn") or 0
    )
    return {
        "result_ref": handle,
        "evidence_handle": handle,
        "source_result_handle": str(origin.get("source_result_handle") or handle),
        "source_turn": source_turn,
        "source_effect_id": origin.get("source_effect_id"),
        "presentation_origin": str(origin.get("origin") or "customer_response"),
        "shape": _shape(entry),
        "member_handles": members,
        # Labels are verified projection metadata from the same result entry.
        # Exposing the parallel array lets the model map an explicit topic
        # return to the right opaque ResultRef without Core selecting it.
        "member_labels": member_labels,
        # A derived visible set may legitimately need its older source set for
        # a later typed relationship such as difference(source, latest).  This
        # is structural ledger lineage, not an inferred conversation target.
        "lineage_result_refs": lineage_result_refs,
        "source_operation": source_operation,
        "canonical_order": list(members),
        "owner_scope": {"user_id": str(scope.get("user_id") or "")},
        "tenant_scope": str(scope.get("tenant_id") or ""),
        "thread_scope": str(scope.get("thread_id") or ""),
        "created_at": entry.get("created_at"),
        "expires_at": entry.get("expires_at"),
        "status": str(entry.get("status") or "active"),
        "version_or_etag": entry.get("version"),
        "label": str(entry.get("label") or handle),
    }


def _transitive_lineage_result_refs(
    entries: list[dict[str, Any]] | None,
    *,
    state: dict[str, Any],
    initial: list[str] | tuple[str, ...],
) -> list[str]:
    """Return scoped structural ancestors of one visible derived result.

    Intermediate sort/take results need not themselves be customer-visible,
    but their source links are runtime-owned ledger provenance. Following
    those links cannot select an unrelated topic: every ancestor must remain
    an active view/result in the same user, tenant and thread scope.
    """
    scope = scope_for_state(state)
    ordered: list[str] = []
    pending = [str(value) for value in initial if str(value)]
    seen: set[str] = set()
    while pending:
        handle = pending.pop(0)
        if handle in seen:
            continue
        seen.add(handle)
        entry = find_handle(
            entries,
            handle,
            scope=scope,
            allowed_kinds={"view", "result"},
            active_only=True,
        )
        if entry is None:
            continue
        ordered.append(handle)
        source_target = entry.get("source_target") if isinstance(entry.get("source_target"), dict) else {}
        source_query = entry.get("query") if isinstance(entry.get("query"), dict) else {}
        expression = source_target.get("target") if isinstance(source_target.get("target"), dict) else {}
        if not expression and isinstance(source_query.get("target"), dict):
            expression = source_query["target"]
        pending.extend(
            str(expression.get(key) or "").strip()
            for key in ("left_handle", "right_handle")
            if str(expression.get(key) or "").strip() not in seen
        )
    return ordered


def _visible_collection_member_ref(
    entries: list[dict[str, Any]] | None,
    *,
    state: dict[str, Any],
    member_entry: dict[str, Any],
) -> dict[str, Any] | None:
    """Prove that an exact artifact was rendered inside a released collection.

    Releasing a collection or target-bearing eligibility result marks only
    that parent as customer-visible.  Its member artifacts deliberately remain
    ordinary ledger facts, otherwise an unrelated internal artifact could
    become usable merely by existing.  A later model may nevertheless propose
    one *exact* opaque member handle that the released parent lists.  Validate
    that membership and inherit only the parent's presentation provenance;
    never choose a member here.
    """
    member_handle = str(member_entry.get("handle") or "")
    if not member_handle:
        return None
    scope = scope_for_state(state)
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in normalize_ledger(entries):
        if str(row.get("kind") or "") not in {"view", "result", "eligibility"}:
            continue
        parent = find_handle(
            entries,
            str(row.get("handle") or ""),
            scope=scope,
            allowed_kinds={"view", "result", "eligibility"},
            active_only=True,
        )
        if parent is None or member_handle not in _members(parent):
            continue
        parent_ref = _entry_to_ref(parent)
        if parent_ref is not None:
            candidates.append((parent, parent_ref))
    if not candidates:
        return None
    parent, parent_ref = max(
        candidates,
        key=lambda pair: (
            int(pair[1].get("source_turn") or 0),
            float(pair[0].get("created_at") or 0),
        ),
    )
    members = _members(parent)
    index = members.index(member_handle)
    labels = list(parent_ref.get("member_labels") or [])
    label = str(labels[index]) if index < len(labels) and str(labels[index]) else str(member_entry.get("label") or member_handle)
    member_scope = dict(member_entry.get("scope") or {})
    return {
        "result_ref": member_handle,
        "source_result_handle": str(parent_ref.get("source_result_handle") or parent_ref.get("result_ref") or ""),
        "source_collection_ref": str(parent_ref.get("result_ref") or ""),
        "source_turn": int(parent_ref.get("source_turn") or 0),
        "source_effect_id": parent_ref.get("source_effect_id"),
        "presentation_origin": "customer_visible_result_member",
        "shape": "one",
        "member_handles": [member_handle],
        "member_labels": [label],
        "lineage_result_refs": list(parent_ref.get("lineage_result_refs") or []),
        "source_operation": dict(parent_ref.get("source_operation") or {}),
        "canonical_order": [member_handle],
        "owner_scope": {"user_id": str(member_scope.get("user_id") or "")},
        "tenant_scope": str(member_scope.get("tenant_id") or ""),
        "thread_scope": str(member_scope.get("thread_id") or ""),
        "created_at": member_entry.get("created_at"),
        "expires_at": member_entry.get("expires_at"),
        "status": str(member_entry.get("status") or "active"),
        "version_or_etag": member_entry.get("version"),
        "label": label,
    }


def visible_result_refs_from_ledger(
    entries: list[dict[str, Any]] | None,
    *,
    state: dict[str, Any],
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Return only active, scoped results that a customer has already seen."""
    scope = scope_for_state(state)
    rows: list[dict[str, Any]] = []
    for entry in normalize_ledger(entries):
        if str(entry.get("kind") or "") not in _VISIBLE_KINDS:
            continue
        candidate = find_handle(
            entries,
            str(entry.get("handle") or ""),
            scope=scope,
            allowed_kinds=_VISIBLE_KINDS,
            active_only=True,
        )
        if candidate is None:
            continue
        ref = _entry_to_ref(candidate)
        if ref is not None:
            ref["lineage_result_refs"] = _transitive_lineage_result_refs(
                entries,
                state=state,
                initial=list(ref.get("lineage_result_refs") or []),
            )
            rows.append(ref)
    rows.sort(key=lambda item: (int(item.get("source_turn") or 0), float(item.get("created_at") or 0)), reverse=True)
    limited = rows[:max(0, int(limit))]
    latest_turn = max((int(item.get("source_turn") or 0) for item in limited), default=0)
    # Recency is discourse metadata for the semantic owner, not a target
    # selection.  Runtime still validates only the exact ResultRef proposed by
    # the model.  Older results remain available for explicit topic returns.
    return [
        {
            **item,
            "discourse_recency_rank": index + 1,
            "is_latest_visible_turn": int(item.get("source_turn") or 0) == latest_turn,
        }
        for index, item in enumerate(limited)
    ]


def mark_visible_result_refs(
    entries: list[dict[str, Any]] | None,
    *,
    state: dict[str, Any],
    evidence_handles: list[str] | tuple[str, ...],
    source_effect_by_handle: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Mark release-backed evidence as visible without changing its facts.

    The final release gate has already validated these evidence handles.  This
    function only adds presentation provenance; it never creates a new result
    or upgrades an invalid entry into usable context.
    """
    scope = scope_for_state(state)
    additions: list[dict[str, Any]] = []
    effect_index = dict(source_effect_by_handle or {})
    for raw_handle in evidence_handles or ():
        handle = str(raw_handle or "").strip()
        if not handle:
            continue
        entry = find_handle(
            entries,
            handle,
            scope=scope,
            allowed_kinds=_VISIBLE_KINDS,
            active_only=False,
        )
        if entry is None:
            continue
        next_entry = deepcopy(entry)
        next_entry["presentation_origin"] = {
            "origin": "customer_final_response",
            "source_turn": int(state.get("turn_index") or 0),
            "source_result_handle": handle,
            "source_effect_id": effect_index.get(handle),
        }
        next_entry["updated_turn"] = int(state.get("turn_index") or 0)
        additions.append(next_entry)
    return append_entries(entries, additions) if additions else list(entries or [])


def validate_visible_result_ref(
    *,
    state: dict[str, Any],
    result_ref: str,
    expected_shape: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate a model-proposed visible reference without choosing another."""
    handle = str(result_ref or "").strip()
    if not handle:
        return None, "visible_result_ref_missing"
    entry = find_handle(
        state.get("artifact_ledger") or [],
        handle,
        scope=scope_for_state(state),
        allowed_kinds=_VISIBLE_KINDS,
        active_only=True,
    )
    if entry is None:
        return None, "visible_result_ref_unknown_or_expired_or_out_of_scope"
    ref = _entry_to_ref(entry)
    if ref is None and str(entry.get("kind") or "") == "artifact":
        ref = _visible_collection_member_ref(
            state.get("artifact_ledger") or [],
            state=state,
            member_entry=entry,
        )
    if ref is None:
        return None, "visible_result_ref_not_customer_visible"
    if expected_shape and str(ref.get("shape") or "") != str(expected_shape):
        return None, "visible_result_ref_shape_mismatch"
    return ref, None


def _successful_observation_for_handle(
    *,
    state: dict[str, Any],
    handle: str,
) -> dict[str, Any] | None:
    """Return provenance only for a prior, permit-backed observation in this turn.

    ``artifact_ledger`` is durable internal state, but mere presence there is
    not proof that a model may consume an entry.  Same-turn chaining therefore
    requires the opaque handle to be the declared output of an already
    executed read whose exact MatchProof and ExecutionPermit are both present
    in the runtime-owned trace.
    """
    current_turn = int(state.get("turn_index") or 0)
    expected_scope = scope_for_state(state)
    expected_permit_scope = execution_scope_for_state(state)
    for row in reversed(list(state.get("tool_trace") or [])):
        if not isinstance(row, dict) or str(row.get("classification") or "") != "observation":
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        produced_handles = {
            str(data.get(key) or "").strip()
            for key in ("result_handle", "view_handle")
            if str(data.get(key) or "").strip()
        }
        if not bool(result.get("ok")):
            continue
        matched_output_handle = handle if handle in produced_handles else None
        if matched_output_handle is None:
            # A successful read normally returns an opaque Result/View plus
            # its member artifacts.  A later call may target one exact member,
            # but only when the runtime-owned ledger proves that membership in
            # the Result/View returned by this very observation.  This does not
            # trust arbitrary nested model/tool payload fields and does not
            # authorize an artifact merely because it exists in the ledger.
            for output_handle in produced_handles:
                output = find_handle(
                    state.get("artifact_ledger") or [],
                    output_handle,
                    scope=expected_scope,
                    allowed_kinds={"view", "result"},
                    active_only=True,
                )
                if (
                    output is not None
                    and int(output.get("updated_turn") or -1) == current_turn
                    and handle in _members(output)
                ):
                    matched_output_handle = output_handle
                    break
        if matched_output_handle is None:
            continue
        effect_id = str(row.get("effect_id") or "").strip()
        permit = row.get("execution_permit") if isinstance(row.get("execution_permit"), dict) else result.get("execution_permit")
        proof = row.get("match_proof") if isinstance(row.get("match_proof"), dict) else result.get("match_proof")
        if not effect_id or not isinstance(permit, dict) or not isinstance(proof, dict):
            continue
        if not bool(proof.get("exact_match")):
            continue
        if str(permit.get("effect_id") or "") != effect_id:
            continue
        if str(permit.get("tool_name") or "") != str(row.get("name") or ""):
            continue
        if int(permit.get("turn") or -1) != current_turn:
            continue
        if dict(permit.get("scope") or {}) != expected_permit_scope:
            continue
        if not str(permit.get("permit_id") or "") or not str(permit.get("capability_id") or ""):
            continue
        return {
            "effect_id": effect_id,
            "tool_name": str(row.get("name") or ""),
            "source_output_handle": matched_output_handle,
        }
    return None


def validate_runtime_result_ref(
    *,
    state: dict[str, Any],
    result_ref: str,
    expected_shape: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate a consumable result without weakening the cross-turn boundary.

    A prior-turn reference must have crossed the customer-visible final-release
    boundary.  An intermediate reference from the current turn may instead be
    consumed only when a preceding, successful, permit-backed observation
    produced that exact handle.  This supports generic pipelines such as
    ``sort -> take -> downstream read`` while rejecting ledger injection and
    invisible historical state.
    """
    visible, visible_error = validate_visible_result_ref(
        state=state,
        result_ref=result_ref,
        expected_shape=expected_shape,
    )
    if visible is not None:
        return {**visible, "reference_kind": "customer_visible"}, None

    handle = str(result_ref or "").strip()
    if not handle:
        return None, "runtime_result_ref_missing"
    entry = find_handle(
        state.get("artifact_ledger") or [],
        handle,
        scope=scope_for_state(state),
        allowed_kinds={"artifact", "view", "result"},
        active_only=True,
    )
    if entry is None:
        return None, visible_error or "runtime_result_ref_unknown_or_expired_or_out_of_scope"
    if int(entry.get("updated_turn") or -1) != int(state.get("turn_index") or 0):
        return None, visible_error or "runtime_result_ref_not_customer_visible"
    shape = _shape(entry)
    if expected_shape and shape != str(expected_shape):
        return None, "runtime_result_ref_shape_mismatch"
    provenance = _successful_observation_for_handle(state=state, handle=handle)
    if provenance is None:
        return None, "runtime_result_ref_not_verified_current_turn_observation"
    members = _members(entry)
    return {
        "result_ref": handle,
        "source_result_handle": handle,
        "source_turn": int(entry.get("updated_turn") or 0),
        "source_effect_id": provenance["effect_id"],
        "source_output_handle": provenance["source_output_handle"],
        "presentation_origin": "current_turn_verified_observation",
        "reference_kind": "current_turn_verified_observation",
        "shape": shape,
        "member_handles": members,
        "canonical_order": list(members),
        "owner_scope": {"user_id": str((entry.get("scope") or {}).get("user_id") or "")},
        "tenant_scope": str((entry.get("scope") or {}).get("tenant_id") or ""),
        "thread_scope": str((entry.get("scope") or {}).get("thread_id") or ""),
        "created_at": entry.get("created_at"),
        "expires_at": entry.get("expires_at"),
        "status": str(entry.get("status") or "active"),
        "version_or_etag": entry.get("version"),
        "label": str(entry.get("label") or handle),
    }, None
