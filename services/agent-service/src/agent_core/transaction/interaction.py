from __future__ import annotations

"""UI-independent transaction interaction contracts.

Business services expose operation previews and semantic input requirements;
this module projects verified ActionDraft state into a generic interaction
contract.  It deliberately contains no HTML, CSS, route names, or action
specific front-end logic.  Any web/mobile/native client may render the same
``transaction_interaction.v1`` payload differently while preserving the
same authority and lifecycle semantics.

The browser receives opaque control fields only to return them unchanged to
server-side validators.  They are not presentation data and must never be
rendered to users.
"""

from typing import Any

from agent_core.transaction.authority import UI_AUTHORITY_PROTOCOL, UI_CONFIRMED, UI_REJECTED
from agent_core.transaction.active_draft import active_draft_patch, get_active_draft_id
from agent_core.ledger import find_handle, scope_for_state

INTERACTION_SCHEMA_VERSION = "transaction_interaction.v1"
INTERACTION_REQUIRED = "interaction_required"
AUTHORITY_REQUIRED = "authority_required"  # explicit awaiting-authority state

_INTERNAL_INPUT_KEYS = {"action_span", "action_turn", "expected_version"}


def business_input_values(offer: dict[str, Any]) -> dict[str, Any]:
    """Return only business input values, excluding Agent/audit metadata.

    This is intentionally field-agnostic.  A future module can add a date,
    attachment id, address, approver or any other input without changing the
    transaction gateway.
    """
    return {
        str(key): value
        for key, value in dict(offer.get("input_values") or {}).items()
        if str(key) not in _INTERNAL_INPUT_KEYS
    }


def _input_schema(offer: dict[str, Any]) -> list[dict[str, Any]]:
    """Return semantic input metadata persisted with the draft.

    The interaction layer never owns business labels.  The operation preview
    supplies field metadata and the draft persists it so a later authority
    card can still label values after ``required_inputs`` becomes empty.
    """
    preview = offer.get("preview") if isinstance(offer.get("preview"), dict) else {}
    candidates = (
        preview.get("required_inputs") if isinstance(preview, dict) else None,
        offer.get("required_inputs"),
        offer.get("input_schema"),
    )
    for rows in candidates:
        normalized = [dict(row) for row in rows or [] if isinstance(row, dict) and str(row.get("name") or "").strip()]
        if normalized:
            return normalized
    return []


def _field_control(raw: dict[str, Any]) -> str:
    """Map semantic input metadata to a generic renderer control.

    ``input_kind`` is a semantic API field, not a layout command.  Unknown
    kinds degrade safely to text, letting clients evolve independently.
    """
    kind = str(raw.get("input_kind") or raw.get("value_kind") or raw.get("kind") or "").strip().lower()
    options = raw.get("options") if isinstance(raw.get("options"), list) else []
    if kind in {"textarea", "multiline"}:
        return "textarea"
    if kind in {"date", "datetime", "number", "checkbox"}:
        return kind
    if kind in {"file", "attachment", "attachment_ref", "file_ref"}:
        # Upload transport is a separate client capability.  The generic web
        # renderer must not pretend that a browser File object is already a
        # durable business attachment, so it asks for an uploaded reference.
        return "file_reference"
    if kind in {"select", "enum", "choice"}:
        # ``allow_custom`` is semantic field metadata.  A client may render it
        # as a select plus free-text escape hatch without knowing the action.
        return "choice_or_text" if options and bool(raw.get("allow_custom")) else "select"
    if options:
        return "choice_or_text" if bool(raw.get("allow_custom")) else "select"
    return "text"


def _input_fields(offer: dict[str, Any]) -> list[dict[str, Any]]:
    values = business_input_values(offer)
    suggestions = offer.get("suggested_input") if isinstance(offer.get("suggested_input"), dict) else {}
    errors = offer.get("input_errors") if isinstance(offer.get("input_errors"), dict) else {}
    fields: list[dict[str, Any]] = []
    for raw in _input_schema(offer):
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        options = raw.get("options") if isinstance(raw.get("options"), list) else []
        value_kind = str(raw.get("input_kind") or raw.get("value_kind") or raw.get("kind") or "text").strip().lower()
        control = _field_control(raw)
        fields.append(
            {
                "name": name,
                "value_kind": value_kind,
                "label": str(raw.get("label") or name),
                "required": bool(raw.get("required", True)),
                "control": control,
                "placeholder": str(raw.get("placeholder") or ("请输入已上传文件的引用编号" if control == "file_reference" else f"请输入{str(raw.get('label') or name)}")),
                "description": str(raw.get("description") or ""),
                "options": [
                    {"value": str(row.get("value") or ""), "label": str(row.get("label") or row.get("value") or "")}
                    if isinstance(row, dict)
                    else {"value": str(row), "label": str(row)}
                    for row in options
                    if str((row.get("value") if isinstance(row, dict) else row) or "")
                ],
                "allow_custom": bool(raw.get("allow_custom")),
                "value": values.get(name, ""),
                "suggested_value": (suggestions.get(name) or {}).get("value", "") if isinstance(suggestions.get(name), dict) else "",
                "suggestion_evidence_span": (suggestions.get(name) or {}).get("evidence_span", "") if isinstance(suggestions.get(name), dict) else "",
                "error": str(errors.get(name) or ""),
                "step": max(1, int(raw.get("step") or raw.get("step_index") or 1)),
                "step_title": str(raw.get("step_title") or raw.get("group_label") or ""),
            }
        )
    return fields


def _target(offer: dict[str, Any], ledger: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    return find_handle(
        ledger,
        str(offer.get("target_handle") or ""),
        scope=scope_for_state(state),
        allowed_kinds={"artifact"},
        active_only=False,
    ) or {}


def _detail_rows(offer: dict[str, Any]) -> list[dict[str, Any]]:
    """Project persisted semantic field labels without knowing any business action."""
    labels = {
        str(row.get("name") or ""): str(row.get("label") or row.get("name") or "")
        for row in _input_schema(offer)
        if str(row.get("name") or "")
    }
    return [
        {"label": labels.get(str(key)) or str(key), "value": str(value)}
        for key, value in business_input_values(offer).items()
        if value not in (None, "")
    ]


def _base_view(offer: dict[str, Any], ledger: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    target = _target(offer, ledger, state)
    preview = dict(offer.get("preview") or {})
    return {
        "schema_version": INTERACTION_SCHEMA_VERSION,
        "interaction_id": str(offer.get("handle") or ""),
        "kind": "transaction",
        "title": str(offer.get("label") or "待办理操作"),
        "target": str(target.get("label") or ""),
        "summary": str(preview.get("message") or ""),
        "details": _detail_rows(offer),
    }


def _input_control(offer: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "submit_input",
        "offer_handle": str(offer.get("handle") or ""),
        "action_id": str(offer.get("action_id") or ""),
        "target_handle": str(offer.get("target_handle") or ""),
        "conversation_revision": int(offer.get("interaction_revision") or state.get("turn_index") or 0),
        "form_id": str(offer.get("input_form_id") or ""),
        "form_version": int(offer.get("input_form_version") or 0),
        "form_step": max(1, int(offer.get("input_step") or 1)),
    }


def _authority_control(offer: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "authorize",
        "authority_protocol": UI_AUTHORITY_PROTOCOL,
        "authority_type_required": UI_CONFIRMED,
        "offer_handle": str(offer.get("handle") or ""),
        "action_id": str(offer.get("action_id") or ""),
        "target_handle": str(offer.get("target_handle") or ""),
        "conversation_revision": int(offer.get("authority_revision") or state.get("turn_index") or 0),
        "confirmation_id": str(offer.get("confirmation_id") or ""),
        "confirmation_version": int(offer.get("confirmation_version") or 0),
    }




def lifecycle_from_draft_state(draft_state: str) -> str:
    """Translate canonical transaction lifecycle into the public card state.

    This is intentionally the only place where transaction state becomes a
    UI lifecycle.  Runtime code must never consult retired offer status/action
    projections.
    """
    state = str(draft_state or "").upper()
    mapping = {
        "NEEDS_INPUT": "collecting_input",
        "READY": "queued",
        "AWAITING_AUTHORIZATION": "awaiting_authority",
        "AUTHORIZED": "submitting",
        "COMMITTING": "submitting",
        "SUBMISSION_UNKNOWN": "submission_unknown",
        "RECONCILIATION_REQUIRED": "submission_unknown",
        "COMMITTED": "committed",
        "FAILED_RETRYABLE": "retryable_failure",
        "FAILED_FINAL": "failed",
        "EXPIRED": "closed",
        "REVOKED": "closed",
        "REQUIRES_REVIEW": "review_required",
    }
    return mapping.get(state, "draft")

def _draft_state_for_projection(offer: dict[str, Any]) -> str:
    """Project only canonical draft state.

    Status/action display fields are intentionally not consulted here.  The
    serving runtime accepts the current checkpoint contract only.
    """
    return str(offer.get("draft_state") or "REQUIRES_REVIEW").strip().upper()

def _is_pending_input_offer(state: dict[str, Any], offer: dict[str, Any] | None) -> bool:
    if not isinstance(offer, dict):
        return False
    if _draft_state_for_projection(offer) != "NEEDS_INPUT":
        return False
    if not str(offer.get("handle") or "") or not str(offer.get("input_form_id") or ""):
        return False
    if int(offer.get("input_form_version") or 0) < 1:
        return False
    return str(get_active_draft_id(state) or "") == str(offer.get("handle") or "")


def _is_pending_authority_offer(state: dict[str, Any], offer: dict[str, Any] | None) -> bool:
    if not isinstance(offer, dict):
        return False
    if _draft_state_for_projection(offer) != "AWAITING_AUTHORIZATION":
        return False
    if str(offer.get("authority_protocol") or "") != UI_AUTHORITY_PROTOCOL:
        return False
    if not str(offer.get("handle") or "") or not str(offer.get("confirmation_id") or ""):
        return False
    if int(offer.get("confirmation_version") or 0) < 1 or int(offer.get("authority_revision") or 0) < 1:
        return False
    return str(get_active_draft_id(state) or "") == str(offer.get("handle") or "")


def build_transaction_interaction(*, offer: dict[str, Any], ledger: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    """Build a client-neutral transaction interaction from verified state."""
    view = _base_view(offer, ledger, state)
    if _draft_state_for_projection(offer) == "NEEDS_INPUT":
        fields = _input_fields(offer)
        step_values = sorted({max(1, int(field.get("step") or 1)) for field in fields}) or [1]
        current_step = max(1, int(offer.get("input_step") or step_values[0]))
        if current_step not in step_values:
            current_step = step_values[0]
        current_fields = [field for field in fields if int(field.get("step") or 1) == current_step]
        final_step = current_step == step_values[-1]
        view.update(
            {
                "lifecycle": "collecting_input",
                "fields": fields,
                "current_step": current_step,
                "total_steps": len(step_values),
                "step_title": str((current_fields[0] if current_fields else {}).get("step_title") or ""),
                "actions": [
                    {"id": "submit_input", "label": "下一步" if not final_step else "继续", "style": "primary"},
                    {"id": "cancel_interaction", "label": "暂不办理", "style": "secondary"},
                ],
                "control": _input_control(offer, state),
            }
        )
    elif _draft_state_for_projection(offer) == "AWAITING_AUTHORIZATION":
        view.update(
            {
                "lifecycle": "awaiting_authority",
                "fields": [],
                "actions": [
                    {"id": "approve", "label": f"确认{str(offer.get('label') or '提交')}", "style": "primary"},
                    {"id": "reject", "label": "暂不提交", "style": "secondary"},
                ],
                "control": _authority_control(offer, state),
            }
        )
    else:
        lifecycle = lifecycle_from_draft_state(_draft_state_for_projection(offer))
        view.update({"lifecycle": lifecycle, "fields": [], "actions": [], "control": {}})
    return view


def explicit_interaction_response_contract(state: dict[str, Any]) -> dict[str, Any] | None:
    """Return an explicit interaction only while its durable Draft is pending.

    ``response_contract`` is an ephemeral projection.  A checkpoint can retain
    it after a later commit/reject transition, so it must never override the
    canonical Draft lifecycle or hide a terminal transaction update.
    """
    value = state.get("response_contract")
    if not isinstance(value, dict):
        return None
    interaction = value.get("interaction")
    if not isinstance(interaction, dict):
        return None
    active_handle = str(get_active_draft_id(state) or "")
    interaction_id = str(interaction.get("interaction_id") or "")
    if not active_handle or interaction_id != active_handle:
        return None
    durable = interaction_response_contract(state)
    if durable is None or not isinstance(durable.get("interaction"), dict):
        return None
    # The runtime may add presentation metadata, but the lifecycle/card target
    # itself must still be the currently verified durable interaction.
    return value


def interaction_response_contract(state: dict[str, Any]) -> dict[str, Any] | None:
    """Derive the one public interaction response from persisted state."""
    handle = str(get_active_draft_id(state) or "")
    if not handle:
        return None
    ledger = list(state.get("artifact_ledger") or [])
    offer = find_handle(ledger, handle, scope=scope_for_state(state), allowed_kinds={"offer"}, active_only=False)
    if _is_pending_input_offer(state, offer):
        assert offer is not None
        interaction = build_transaction_interaction(offer=offer, ledger=ledger, state=state)
        return {
            "kind": INTERACTION_REQUIRED,
            "message": "请补充以下信息后继续办理。",
            "interaction": interaction,
            "source": "verified_offer_state",
        }
    if _is_pending_authority_offer(state, offer):
        assert offer is not None
        interaction = build_transaction_interaction(offer=offer, ledger=ledger, state=state)
        return {
            "kind": INTERACTION_REQUIRED,
            "message": f"请确认是否提交：{interaction.get('title') or '该操作'}。",
            "interaction": interaction,
            "source": "verified_offer_state",
        }
    return None


def _public_transaction_snapshot(interaction: dict[str, Any]) -> dict[str, Any]:
    """Strip live authority/form controls from a history-safe interaction view."""
    source = dict(interaction or {})
    return {
        "schema_version": source.get("schema_version") or INTERACTION_SCHEMA_VERSION,
        "interaction_id": str(source.get("interaction_id") or ""),
        "kind": "transaction",
        "lifecycle": str(source.get("lifecycle") or "draft"),
        "title": str(source.get("title") or "待办理操作"),
        "target": str(source.get("target") or ""),
        "summary": str(source.get("summary") or ""),
        "details": [dict(row) for row in source.get("details") or [] if isinstance(row, dict)],
        "current_step": int(source.get("current_step") or 1),
        "total_steps": int(source.get("total_steps") or 1),
        "step_title": str(source.get("step_title") or ""),
        # Fields/actions/controls are intentionally absent: a reopened history
        # is informative only.  The server exposes a fresh live pending form
        # through ``pending-interaction`` when one still exists.
        "fields": [],
        "actions": [],
        "control": {},
        "read_only": True,
    }


def transaction_display_snapshot_from_state(state: dict[str, Any]) -> dict[str, Any] | None:
    """Return a history-safe transaction card for the response just produced.

    The graph state is the only authority for the transaction lifecycle.  This
    helper projects it into a presentation snapshot after both pending form
    responses and terminal commit/rejection responses, so history replays use
    the same product component rather than regressing to hidden audit prose.
    """
    contract = interaction_response_contract(state)
    if contract is not None and isinstance(contract.get("interaction"), dict):
        return _public_transaction_snapshot(dict(contract["interaction"]))

    update = transaction_update_from_state(state)
    if update is None:
        return None
    handle = str(update.get("interaction_id") or "")
    ledger = list(state.get("artifact_ledger") or [])
    offer = find_handle(
        ledger,
        handle,
        scope=scope_for_state(state),
        allowed_kinds={"offer"},
        active_only=False,
    ) if handle else None
    if not isinstance(offer, dict):
        return {
            "schema_version": INTERACTION_SCHEMA_VERSION,
            "interaction_id": handle,
            "kind": "transaction",
            "lifecycle": str(update.get("lifecycle") or "draft"),
            "title": "业务办理",
            "target": "",
            "summary": str(update.get("message") or ""),
            "details": [],
            "fields": [],
            "actions": [],
            "control": {},
            "read_only": True,
        }
    view = _base_view(offer, ledger, state)
    view.update(
        {
            "lifecycle": str(update.get("lifecycle") or lifecycle_from_draft_state(_draft_state_for_projection(offer))),
            "summary": str(update.get("message") or view.get("summary") or ""),
            "fields": [],
            "actions": [],
            "control": {},
            "read_only": True,
        }
    )
    return _public_transaction_snapshot(view)


def transaction_update_from_state(state: dict[str, Any]) -> dict[str, Any] | None:
    """Return a UI-neutral lifecycle update after an interaction event.

    This lets a live card update in-place without forcing a client to infer
    success/failure from chat prose.  It is intentionally optional: clients
    that do not support it can still render the ordinary answer.
    """
    result = state.get("offer_execution_result") if isinstance(state.get("offer_execution_result"), dict) else None
    gateway = state.get("action_gateway_result") if isinstance(state.get("action_gateway_result"), dict) else {}
    handle = str((gateway or {}).get("offer_handle") or "")
    if not handle and result is None:
        return None
    gateway_draft_state = str((gateway or {}).get("draft_state") or "")
    lifecycle = (
        "committed" if result and bool(result.get("success")) else
        lifecycle_from_draft_state(gateway_draft_state) if gateway_draft_state else
        "failed" if result else str((gateway or {}).get("decision") or "")
    )
    return {
        "schema_version": INTERACTION_SCHEMA_VERSION,
        "interaction_id": handle or None,
        "kind": "transaction",
        "lifecycle": lifecycle,
        "message": str((gateway or {}).get("message") or (result or {}).get("error") or ""),
    }


def pending_transaction_summaries_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return client-neutral summaries for the pending-work drawer.

    The active transaction remains the only expandable card.  Other queued
    drafts are intentionally summaries: exposing several live forms in one
    chat stream creates ambiguous input ownership and accidental submissions.
    The drawer lets users see that the work exists without bypassing the graph
    state machine that decides which transaction may currently collect input.
    """
    ledger = list(state.get("artifact_ledger") or [])
    scope = scope_for_state(state)
    active_handle = str(get_active_draft_id(state) or "")
    queued_handles = [
        str(row.get("offer_handle") or "")
        for row in state.get("action_queue") or []
        if isinstance(row, dict) and str(row.get("offer_handle") or "")
    ]
    candidate_handles: list[str] = []
    if active_handle:
        candidate_handles.append(active_handle)
    candidate_handles.extend(handle for handle in queued_handles if handle not in candidate_handles)

    # Recover drafts that survived an older checkpoint without an action_queue
    # entry.  Terminal entries are deliberately excluded.
    for item in ledger:
        if not isinstance(item, dict) or str(item.get("kind") or "") != "offer":
            continue
        state_name = _draft_state_for_projection(item)
        if state_name in {"NEEDS_INPUT", "AWAITING_AUTHORIZATION", "READY", "SUBMISSION_UNKNOWN", "RECONCILIATION_REQUIRED"}:
            handle = str(item.get("handle") or "")
            if handle and handle not in candidate_handles:
                candidate_handles.append(handle)

    rows: list[dict[str, Any]] = []
    for handle in candidate_handles:
        offer = find_handle(ledger, handle, scope=scope, allowed_kinds={"offer"}, active_only=False)
        if not isinstance(offer, dict):
            continue
        draft_state = _draft_state_for_projection(offer)
        if draft_state not in {"NEEDS_INPUT", "AWAITING_AUTHORIZATION", "READY", "SUBMISSION_UNKNOWN", "RECONCILIATION_REQUIRED"}:
            continue
        target = _target(offer, ledger, state)
        is_active = handle == active_handle
        lifecycle = (
            "submission_unknown" if draft_state in {"SUBMISSION_UNKNOWN", "RECONCILIATION_REQUIRED"} else
            "collecting_input" if draft_state == "NEEDS_INPUT" else
            "awaiting_authority" if draft_state == "AWAITING_AUTHORIZATION" else
            "queued"
        )
        rows.append(
            {
                "interaction_id": handle,
                "title": str(offer.get("label") or "待办理操作"),
                "target": str(target.get("label") or ""),
                "lifecycle": lifecycle,
                "active": is_active and lifecycle != "submission_unknown",
                "read_only": lifecycle == "submission_unknown",
                "summary": "提交结果正在确认中，请勿重复操作。" if lifecycle == "submission_unknown" else str((offer.get("preview") or {}).get("message") or ""),
            }
        )
    return rows
