from __future__ import annotations

from copy import deepcopy
from typing import Any

try:
    from langgraph.types import interrupt
except Exception:  # pragma: no cover
    def interrupt(payload):  # type: ignore
        return payload

from agent_core.transaction.authority import UI_CONFIRMED, UI_REJECTED, build_ui_authority
from agent_core.transaction.interaction import interaction_response_contract
from agent_core.ledger import append_entries, authority_entry, find_handle, ledger_cards, scope_for_state
from agent_core.kernel.decision_trace import append_decision as _append_decision
from agent_core.transaction.deps import TransactionExecutionDeps
from agent_core.transaction import DRAFT_REQUIRES_REVIEW, transition_draft
from agent_core.transaction.active_draft import active_draft_patch, get_active_draft_id
from agent_core.transaction.coordinator import issue_grant_for_authority, persist_draft_from_offer
from agent_core.transaction.capability_snapshot import snapshot_matches_registry
from agent_core.transaction.target_contract import allowed_target_resource_types, target_unavailable_message
from agent_core.transaction.gateway_runtime import (
    _gateway_observation_update,
    _mark_offer_needs_input,
    _refresh_offer_preflight,
    _required_offer_input_rows,
)

def _queue_phase_or_final(queue: list[dict[str, Any]], *, fallback_answer: str | None = None) -> dict[str, Any]:
    if queue:
        return {"phase": "action_gateway", "status": "ContinueActionQueue"}
    return {"phase": "final", "status": "ActionAuthorityTerminal", "current_final_answer": fallback_answer}


def _required_offer_input_rows(offer: dict[str, Any]) -> list[dict[str, Any]]:
    preview = offer.get("preview") if isinstance(offer.get("preview"), dict) else {}
    rows = preview.get("required_inputs") if isinstance(preview, dict) else None
    if not rows:
        rows = offer.get("required_inputs")
    return [dict(row) for row in rows or [] if isinstance(row, dict) and str(row.get("name") or "").strip()]


def _validate_structured_input_submission(
    *,
    state: dict[str, Any],
    offer: dict[str, Any],
    resumed: dict[str, Any],
) -> tuple[bool, dict[str, str], dict[str, Any]]:
    """Validate a client-neutral transaction form envelope.

    The server validates identifiers, revisions and semantic field requirements.
    It never reads a free-form chat sentence to decide whether a value is an
    input, nor does it know how the client displayed the form.
    """
    current_revision = int(state.get("turn_index") or 0)
    structural = (
        str(resumed.get("interaction_mode") or "submit_input") == "submit_input"
        and str(resumed.get("offer_handle") or "") == str(offer.get("handle") or "")
        and str(resumed.get("action_id") or "") == str(offer.get("action_id") or "")
        and str(resumed.get("target_handle") or "") == str(offer.get("target_handle") or "")
        and str(resumed.get("form_id") or "") == str(offer.get("input_form_id") or "")
        and int(resumed.get("form_version") or 0) == int(offer.get("input_form_version") or 0)
        and int(resumed.get("conversation_revision") or 0) == int(offer.get("interaction_revision") or current_revision)
        and bool(str(resumed.get("client_request_id") or ""))
        and bool(str(resumed.get("submitted_by") or ""))
    )
    if not structural:
        return False, {"_form": "该表单已失效或与当前操作不匹配。"}, {}
    raw_values = resumed.get("input_values") if isinstance(resumed.get("input_values"), dict) else {}
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    current_step = max(1, int(offer.get("input_step") or 1))
    rows = [
        row for row in _required_offer_input_rows(offer)
        if max(1, int(row.get("step") or row.get("step_index") or 1)) == current_step
    ]
    for row in rows:
        name = str(row.get("name") or "").strip()
        raw_value = raw_values.get(name, "")
        value = str(raw_value).strip() if raw_value is not None else ""
        if bool(row.get("required", True)) and not value:
            errors[name] = f"请填写{str(row.get('label') or name)}。"
            continue
        options = row.get("options") if isinstance(row.get("options"), list) else []
        allowed = {
            str(item.get("value") if isinstance(item, dict) else item)
            for item in options
            if str(item.get("value") if isinstance(item, dict) else item)
        }
        if value and allowed and value not in allowed and not bool(row.get("allow_custom")):
            errors[name] = f"{str(row.get('label') or name)}不在可选范围内。"
            continue
        values[name] = value
    return not errors, errors, values


def _apply_input_submission(
    state: dict[str, Any],
    offer: dict[str, Any],
    ledger: list[dict[str, Any]],
    resumed: dict[str, Any],
    *,
    deps: TransactionExecutionDeps,
) -> dict[str, Any]:
    """Apply a structured form submission and route it back through preflight.

    This is deliberately a state transition, not a model tool call.  A future
    native/mobile client can submit the exact same envelope and gets the same
    lifecycle result.
    """
    valid, errors, submitted = _validate_structured_input_submission(state=state, offer=offer, resumed=resumed)
    current_turn = int(state.get("turn_index") or 0)
    queue = list(state.get("action_queue") or [])
    if not valid:
        invalid = deepcopy(offer)
        invalid["input_errors"] = errors
        invalid["input_form_version"] = int(offer.get("input_form_version") or 0) + 1
        invalid["updated_turn"] = current_turn
        ledger = append_entries(ledger, [invalid])
        contract = interaction_response_contract({**state, "artifact_ledger": ledger, **active_draft_patch(invalid.get("handle"))})
        return {
            "artifact_ledger": ledger,
            "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
            **active_draft_patch(invalid.get("handle")),
            "pending_confirmation_id": None,
            "pending_confirmation_version": None,
            "response_contract": contract,
            "action_gateway_result": {"decision": "input_invalid", "offer_handle": invalid.get("handle"), "message": "请检查表单字段。", "field_errors": errors},
            "phase": "offer_confirmation",
            "status": "ActionInputInvalid",
            "decision_chain": _append_decision(state, stage="transaction_input", decision="input_rejected", details={"offer_handle": invalid.get("handle"), "field_errors": errors}),
        }

    updated = deepcopy(offer)
    values = dict(updated.get("input_values") or {})
    values.update(submitted)
    updated["input_values"] = values
    updated["input_errors"] = {}
    updated["updated_turn"] = current_turn
    all_steps = sorted({max(1, int(row.get("step") or row.get("step_index") or 1)) for row in _required_offer_input_rows(updated)}) or [1]
    current_step = max(1, int(offer.get("input_step") or 1))
    remaining_steps = [step for step in all_steps if step > current_step]
    if remaining_steps:
        # Form progression is a deterministic transaction state transition.  It
        # does not re-enter the LLM and it does not run final business preflight
        # until all semantic inputs have been captured.
        updated["input_step"] = remaining_steps[0]
        updated = transition_draft(updated, "NEEDS_INPUT")
        ledger, pending = _mark_offer_needs_input(state, updated, ledger)
        contract = interaction_response_contract({**state, "artifact_ledger": ledger, **active_draft_patch(pending.get("handle"))})
        return {
            "artifact_ledger": ledger,
            "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
            **active_draft_patch(pending.get("handle")),
            "pending_confirmation_id": None,
            "pending_confirmation_version": None,
            "response_contract": contract,
            "action_gateway_result": {"decision": "input_step_completed", "offer_handle": pending.get("handle"), "message": "已保存当前信息，请继续下一步。"},
            "phase": "offer_confirmation",
            "status": "ActionInputStepCompleted",
            "decision_chain": _append_decision(state, stage="transaction_input", decision="input_step_completed", details={"offer_handle": pending.get("handle"), "next_step": remaining_steps[0]}),
        }
    target = find_handle(ledger, str(updated.get("target_handle") or ""), scope=scope_for_state(state), allowed_kinds={"artifact"}, allowed_resource_types=allowed_target_resource_types(str(updated.get("action_id") or "")), active_only=False)
    if target is None:
        updated = transition_draft(updated, "FAILED_FINAL", reason="input_target_missing")
        ledger = append_entries(ledger, [updated])
        return {
            "artifact_ledger": ledger,
            "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
            **active_draft_patch(None),
            "pending_confirmation_id": None,
            "pending_confirmation_version": None,
            "response_contract": None,
            "action_gateway_result": {"decision": "rejected", "offer_handle": updated.get("handle"), "message": "业务对象已失效，请重新发起。"},
            "current_final_answer": "该业务对象已失效，请重新发起。",
            "phase": "final",
            "status": "ActionInputTargetMissing",
        }
    preflight, preview, additions = _refresh_offer_preflight(state, updated, target, deps=deps)
    ledger = append_entries(ledger, additions)
    if not preflight.get("success") or preview is None:
        updated = transition_draft(updated, "FAILED_FINAL", reason="input_preflight_failed")
        updated["superseded_reason"] = "input_preflight_failed"
        ledger = append_entries(ledger, [updated])
        return {
            "artifact_ledger": ledger,
            "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
            **active_draft_patch(None),
            "pending_confirmation_id": None,
            "pending_confirmation_version": None,
            "response_contract": None,
            "action_gateway_result": {"decision": "rejected", "offer_handle": updated.get("handle"), "message": str(preflight.get("error") or "业务预检失败")},
            "current_final_answer": str(preflight.get("error") or "业务预检失败，未执行任何业务操作。"),
            "phase": "final",
            "status": "ActionInputPreflightFailed",
        }
    updated["preview"] = preview
    snapshot = preview.get("snapshot") if isinstance(preview.get("snapshot"), dict) else {}
    if snapshot.get("version") is not None:
        updated["input_values"]["expected_version"] = int(snapshot.get("version") or 1)
    decision = str(preview.get("decision") or "")
    if decision == "NEEDS_INPUT":
        updated["required_inputs"] = list(preview.get("required_inputs") or [])
        ledger, pending = _mark_offer_needs_input(state, updated, ledger)
        contract = interaction_response_contract({**state, "artifact_ledger": ledger, **active_draft_patch(pending.get("handle"))})
        return {
            "artifact_ledger": ledger,
            "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
            **active_draft_patch(pending.get("handle")),
            "pending_confirmation_id": None,
            "pending_confirmation_version": None,
            "response_contract": contract,
            "action_gateway_result": {"decision": "needs_input", "offer_handle": pending.get("handle"), "message": str(preview.get("message") or "请继续补充信息。"), "preview": preview},
            "phase": "offer_confirmation",
            "status": "ActionInputStillRequired",
        }
    if decision not in {"ALLOWED", "NEEDS_REVIEW"}:
        updated = transition_draft(updated, "FAILED_FINAL", reason="input_preflight_rejected")
        ledger = append_entries(ledger, [updated])
        return {
            "artifact_ledger": ledger,
            "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
            **active_draft_patch(None),
            "pending_confirmation_id": None,
            "pending_confirmation_version": None,
            "response_contract": None,
            "action_gateway_result": {"decision": "rejected", "offer_handle": updated.get("handle"), "message": str(preview.get("message") or "当前业务状态不允许该操作。"), "preview": preview},
            "current_final_answer": str(preview.get("message") or "当前业务状态不允许该操作。"),
            "phase": "final",
            "status": "ActionInputPreflightRejected",
        }
    updated = transition_draft(updated, "READY")
    updated["required_inputs"] = []
    updated["ready_turn"] = current_turn
    updated["ready_source_tool"] = "structured_interaction_input"
    ledger = append_entries(ledger, [updated])
    current_item = {"offer_handle": updated.get("handle"), "origin_tool": "structured_interaction_input", "transition": "draft_ready"}
    queue = [current_item, *[row for row in queue if str(row.get("offer_handle") or "") != str(updated.get("handle") or "")]]
    return {
        "artifact_ledger": ledger,
        "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
        **active_draft_patch(None),
        "pending_confirmation_id": None,
        "pending_confirmation_version": None,
        "response_contract": None,
        "action_queue": queue,
        "action_gateway_result": {"decision": "input_completed", "offer_handle": updated.get("handle"), "message": str(preview.get("message") or "信息已补充，正在进行业务核验。"), "preview": preview},
        "phase": "action_gateway",
        "status": "ActionInputCompleted",
        "decision_chain": _append_decision(state, stage="transaction_input", decision="input_completed", details={"offer_handle": updated.get("handle")}),
    }


def action_confirmation_node(state: dict[str, Any], *, deps: TransactionExecutionDeps) -> dict[str, Any]:
    """Pause for a generic transaction interaction, not free-form chat text.

    The same node handles two lifecycle stages: structured input collection and
    structured final authority.  The browser/API contract, rather than a model
    sentence, determines which stage is active.
    """
    ledger = list(state.get("artifact_ledger") or [])
    queue = list(state.get("action_queue") or [])
    handle = str(get_active_draft_id(state) or "")
    offer = find_handle(ledger, handle, scope=scope_for_state(state), allowed_kinds={"offer"}, active_only=False)
    contract = interaction_response_contract(state)
    if offer and not snapshot_matches_registry(offer):
        review = transition_draft(offer, DRAFT_REQUIRES_REVIEW, reason="operation_capability_snapshot_unavailable")
        review["read_only"] = True
        review["updated_turn"] = int(state.get("turn_index") or 0)
        persist_draft_from_offer(state=state, offer=review, draft_state=DRAFT_REQUIRES_REVIEW)
        ledger = append_entries(ledger, [review])
        return {
            "artifact_ledger": ledger,
            "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
            **active_draft_patch(None),
            "response_contract": None,
            "current_final_answer": "该办理记录使用的能力合同已不可验证，只能查询或重新发起，未执行任何业务写操作。",
            "phase": "final",
            "status": "TransactionCapabilityReviewRequired",
        }
    if not offer or contract is None:
        return {
            "current_final_answer": target_unavailable_message(str(offer.get("action_id") or "")) if offer else "当前业务操作已失效或状态不完整，请重新发起。",
            **active_draft_patch(None),
            "pending_confirmation_id": None,
            "pending_confirmation_version": None,
            "response_contract": None,
            "commit_authority": None,
            "phase": "final",
            "status": "TransactionInteractionUnavailable",
        }
    interaction = dict(contract.get("interaction") or {})
    lifecycle = str(interaction.get("lifecycle") or "")
    payload = {"type": "ui_transaction_interaction_required", "message": str(contract.get("message") or "请继续处理该操作。"), "interaction": interaction}
    resume = interrupt(payload)
    resumed = dict(resume or {}) if isinstance(resume, dict) else {}
    current_revision = int(state.get("turn_index") or 0)

    if lifecycle == "collecting_input":
        if str(resumed.get("interaction_mode") or "") == "cancel_interaction":
            declined = transition_draft(offer, "REVOKED", reason="user_cancelled_input")
            declined["updated_turn"] = current_revision
            persist_draft_from_offer(state=state, offer=declined, draft_state="REVOKED")
            ledger = append_entries(ledger, [declined])
            next_phase = "action_gateway" if queue else "final"
            return {
                "artifact_ledger": ledger,
                "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
                **active_draft_patch(None),
                "pending_confirmation_id": None,
                "pending_confirmation_version": None,
                "response_contract": None,
                "action_gateway_result": {"decision": "cancelled", "offer_handle": handle, "message": "已取消本次办理，未执行任何业务写操作。"},
                "current_final_answer": None if queue else "已取消本次办理，未执行任何业务写操作。",
                "phase": next_phase,
                "status": "ActionInputCancelled",
            }
        return _apply_input_submission(state, offer, ledger, resumed, deps=deps)

    if lifecycle != "awaiting_authority":
        return {
            "current_final_answer": "当前业务操作状态不支持继续提交，请重新发起。",
            **active_draft_patch(None),
            "pending_confirmation_id": None,
            "pending_confirmation_version": None,
            "response_contract": None,
            "phase": "final",
            "status": "TransactionInteractionLifecycleInvalid",
        }

    decision = str(resumed.get("decision") or "").lower()
    expected_authority_type = UI_CONFIRMED if decision in {"approved", "confirm", "confirmed", "yes"} else UI_REJECTED
    structural_matches = (
        str(resumed.get("offer_handle") or "") == handle
        and str(resumed.get("confirmation_id") or "") == str(offer.get("confirmation_id") or "")
        and int(resumed.get("confirmation_version") or 0) == int(offer.get("confirmation_version") or 0)
        and str(resumed.get("action_id") or "") == str(offer.get("action_id") or "")
        and str(resumed.get("target_handle") or "") == str(offer.get("target_handle") or "")
        and int(resumed.get("conversation_revision") or 0) == int(offer.get("authority_revision") or current_revision)
        and str(resumed.get("authority_type") or "") == expected_authority_type
        and expected_authority_type in {UI_CONFIRMED, UI_REJECTED}
    )
    if not structural_matches:
        return {
            "current_final_answer": "该结构化授权已失效或不匹配，未执行任何业务写操作。",
            **active_draft_patch(None),
            "pending_confirmation_id": None,
            "pending_confirmation_version": None,
            "response_contract": None,
            "commit_authority": None,
            "phase": "final",
            "status": "ActionAuthorityExpired",
        }

    authority = build_ui_authority(
        payload=resumed,
        actor_id=str(resumed.get("approved_by") or ""),
        actor_role=str(resumed.get("approved_role") or ""),
        current_revision=current_revision,
    )
    # The browser cannot submit a digest or grant id. Freeze the exact adapter
    # command before issuing the Grant so later reconciliation never rebuilds it
    # from current Ledger state.
    if authority.get("authority_type") == UI_CONFIRMED:
        target = find_handle(ledger, str(offer.get("target_handle") or ""), scope=scope_for_state(state), allowed_kinds={"artifact"}, allowed_resource_types=allowed_target_resource_types(str(offer.get("action_id") or "")), active_only=False)
        if not target:
            return {"current_final_answer":"目标资源已失效，未执行任何业务写操作。",**active_draft_patch(None),"pending_confirmation_id":None,"pending_confirmation_version":None,"response_contract":None,"commit_authority":None,"phase":"final","status":"ActionTargetUnavailable"}
        prepared = deepcopy(offer)
        envelope = prepared.get("business_command_envelope") if isinstance(prepared.get("business_command_envelope"), dict) else {}
        if not envelope or not str(envelope.get("command_id") or ""):
            return {"current_final_answer":"待授权业务命令快照缺失，未执行任何业务写操作。",**active_draft_patch(None),"pending_confirmation_id":None,"pending_confirmation_version":None,"response_contract":None,"commit_authority":None,"phase":"final","status":"ActionCommandEnvelopeInvalid"}
        prepared = transition_draft(prepared, "AWAITING_AUTHORIZATION", previous=offer)
        prepared["updated_turn"] = current_revision
        ledger = append_entries(ledger, [prepared])
        offer = find_handle(ledger, handle, scope=scope_for_state(state), allowed_kinds={"offer"}, active_only=False) or prepared
        authority = issue_grant_for_authority(state=state, offer=offer, authority=authority)
    authority_label = f"{offer.get('label') or '业务动作'} / {authority.get('authority_type')}"
    ledger = append_entries(ledger, [authority_entry(authority=authority, scope=scope_for_state(state), turn=current_revision, label=authority_label)])

    if authority.get("authority_type") == UI_CONFIRMED:
        authorized = transition_draft(offer, "AUTHORIZED")
        authorized["active_grant_id"] = authority.get("grant_id")
        authorized["updated_turn"] = current_revision
        ledger = append_entries(ledger, [authorized])
        return {
            "artifact_ledger": ledger,
            "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
            **active_draft_patch(handle),
            "pending_confirmation_id": offer.get("confirmation_id"),
            "pending_confirmation_version": offer.get("confirmation_version"),
            "response_contract": None,
            "commit_authority": authority,
            "approval_result": {"decision": "authorized", "authority_type": UI_CONFIRMED},
            "phase": "commit_action",
            "status": "ActionAuthorityAccepted",
            "decision_chain": _append_decision(state, stage="action_authority", decision="ui_authority_accepted", details={"offer_handle": handle, "action_id": offer.get("action_id")}),
        }

    declined = transition_draft(offer, "REVOKED", reason="authority_rejected")
    declined["updated_turn"] = current_revision
    persist_draft_from_offer(state=state, offer=declined, draft_state="REVOKED")
    ledger = append_entries(ledger, [declined])
    next_phase = "action_gateway" if queue else "final"
    return {
        "artifact_ledger": ledger,
        "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
        **active_draft_patch(None),
        "pending_confirmation_id": None,
        "pending_confirmation_version": None,
        "response_contract": None,
        "commit_authority": None,
        "approval_result": {"decision": "rejected", "authority_type": UI_REJECTED},
        "action_gateway_result": {"decision": "cancelled", "offer_handle": handle, "message": "已取消本次办理，未执行任何业务写操作。"},
        "current_final_answer": None if queue else "已取消本次" + str(offer.get("label") or "操作") + "，未执行任何业务写操作。",
        "phase": next_phase,
        "status": "ActionAuthorityRejectedContinueQueue" if queue else "ActionAuthorityRejected",
        "decision_chain": _append_decision(state, stage="action_authority", decision="ui_authority_rejected", details={"offer_handle": handle, "action_id": offer.get("action_id")}),
    }
