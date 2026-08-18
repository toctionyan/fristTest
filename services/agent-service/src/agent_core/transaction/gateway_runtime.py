from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from agent_core.transaction.authority import UI_AUTHORITY_PROTOCOL, deterministic_offer_readiness, policy_for_action
from agent_core.business import ActorContext
from agent_core.business import BusinessServiceError
from agent_core.transaction.interaction import business_input_values, interaction_response_contract
from agent_core.ledger import append_entries, artifact_entry, find_handle, ledger_cards, scope_for_state
from agent_core.kernel.decision_trace import append_decision as _append_decision
from agent_core.transaction.deps import TransactionExecutionDeps
from agent_core.transaction import DRAFT_REQUIRES_REVIEW, transition_draft
from agent_core.transaction.active_draft import active_draft_patch
from agent_core.transaction.coordinator import persist_draft_from_offer
from agent_core.transaction.capability_snapshot import snapshot_matches_registry
from agent_core.transaction.target_contract import allowed_target_resource_types, target_unavailable_message

def _actor_context_from_state(state: dict[str, Any]) -> ActorContext:
    permissions = tuple(str(item) for item in (state.get("actor_permissions") or []) if str(item))
    return ActorContext(
        user_id=str(state.get("current_user_id") or ""),
        role=str(state.get("current_role") or "customer"),
        tenant_id=str(state.get("current_tenant_id") or "") or None,
        subject_user_id=str(state.get("current_subject") or state.get("current_user_id") or "") or None,
        subject=str(state.get("current_subject") or "") or None,
        permissions=permissions,
    )


def _refresh_offer_preflight(
    state: dict[str, Any],
    offer: dict[str, Any],
    target: dict[str, Any],
    *,
    deps: TransactionExecutionDeps,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    """Re-read Business Service when the queued Draft is not fresh this turn."""
    adapter = deps.business_port
    actor = _actor_context_from_state(state)
    additions: list[dict[str, Any]] = []
    target_id = str(target.get("resource_id") or "")
    try:
        current = adapter.read_resource(
            actor,
            resource_type=str(target.get("resource_type") or ""),
            resource_id=target_id,
            query={"user_id": actor.user_id},
        )
        if current.get("success") and isinstance(current.get("data"), dict):
            row = dict(current["data"])
            additions.append(artifact_entry(
                resource_type=str(target.get("resource_type") or ""),
                resource_id=target_id,
                label=str(target.get("label") or f"{target.get('resource_type') or 'resource'}:{target_id}"),
                facts=row,
                scope=scope_for_state(state),
                turn=int(state.get("turn_index") or 0),
                source="action_gateway_fresh_read",
                freshness_version=int(row.get("version") or 1),
                handle=str(target.get("handle") or "") or None,
            ))
    except Exception:
        pass
    try:
        payload = adapter.preview_operation(
            actor,
            resource_type=str(target.get("resource_type") or ""),
            resource_id=target_id,
            operation=str(offer.get("operation") or ""),
            input_values=business_input_values(offer),
        )
    except BusinessServiceError as exc:
        return {"success": False, "error": exc.message, "code": "PREFLIGHT_FAILED"}, None, additions
    except Exception as exc:
        return {"success": False, "error": f"预检异常：{exc.__class__.__name__}: {exc}", "code": "PREFLIGHT_FAILED"}, None, additions
    if not payload.get("success") or not isinstance(payload.get("data"), dict):
        return {"success": False, "error": str(payload.get("error") or "业务预检失败"), "code": "PREFLIGHT_FAILED"}, None, additions
    return {"success": True}, dict(payload.get("data") or {}), additions


def _same_turn_prepared_preview(state: dict[str, Any], offer: dict[str, Any]) -> dict[str, Any] | None:
    """Reuse only the preview that produced this exact gateway-ready Draft.

    ToolExecutionRuntime stamps ``ready_turn`` only after the action-draft tool
    has produced the Draft. Reusing that preview in the same turn prevents a
    second mutable-business read from silently changing the command snapshot
    before structured authority. Older Drafts still cross the refresh path,
    and commit-time preflight remains mandatory before any business effect.
    """
    try:
        ready_turn = int(offer.get("ready_turn") or -1)
    except (TypeError, ValueError):
        return None
    preview = offer.get("preview") if isinstance(offer.get("preview"), dict) else None
    if ready_turn != int(state.get("turn_index") or 0) or not isinstance(preview, dict):
        return None
    if str(preview.get("decision") or "") not in {"ALLOWED", "NEEDS_REVIEW", "NEEDS_INPUT"}:
        return None
    return deepcopy(preview)


def _mark_offer_needs_input(state: dict[str, Any], offer: dict[str, Any], ledger: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    next_offer = transition_draft(offer, "NEEDS_INPUT")
    next_offer["interaction_revision"] = int(state.get("turn_index") or 0)
    next_offer["input_form_id"] = str(uuid4())
    next_offer["input_form_version"] = int(offer.get("input_form_version") or 0) + 1
    next_offer["input_errors"] = {}
    schema = _required_offer_input_rows(next_offer)
    next_offer["input_schema"] = schema
    known_steps = sorted({max(1, int(row.get("step") or row.get("step_index") or 1)) for row in schema}) or [1]
    current_step = max(1, int(offer.get("input_step") or 1))
    next_offer["input_step"] = current_step if current_step in known_steps else known_steps[0]
    next_offer["updated_turn"] = int(state.get("turn_index") or 0)
    persist_draft_from_offer(state=state, offer=next_offer, draft_state="NEEDS_INPUT")
    return append_entries(ledger, [next_offer]), next_offer


def _mark_offer_awaiting_authority(state: dict[str, Any], offer: dict[str, Any], ledger: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    next_offer = transition_draft(offer, "AWAITING_AUTHORIZATION")
    next_offer["authority_protocol"] = UI_AUTHORITY_PROTOCOL
    next_offer["authority_requirement"] = "ui_action_authority"
    next_offer["authority_revision"] = int(state.get("turn_index") or 0)
    next_offer["confirmation_id"] = str(uuid4())
    next_offer["confirmation_version"] = int(offer.get("confirmation_version") or 0) + 1
    next_offer["updated_turn"] = int(state.get("turn_index") or 0)
    persist_draft_from_offer(state=state, offer=next_offer, draft_state="AWAITING_AUTHORIZATION")
    return append_entries(ledger, [next_offer]), next_offer


def advance_transaction_gateway(state: dict[str, Any], *, deps: TransactionExecutionDeps) -> dict[str, Any]:
    existing_interaction = interaction_response_contract(state)
    if existing_interaction is not None:
        return {"response_contract": existing_interaction, "current_final_answer": None, "phase": "offer_confirmation", "status": "TransactionInteractionRequired"}
    queue = list(state.get("action_queue") or [])
    if not queue:
        return {"response_contract": None, "phase": "agent_loop", "status": "NoActionProposal"}
    item = queue.pop(0)
    handle = str(item.get("offer_handle") or "")
    ledger = list(state.get("artifact_ledger") or [])
    offer = find_handle(ledger, handle, scope=scope_for_state(state), allowed_kinds={"offer"}, active_only=False)
    if not offer:
        result = {"decision": "rejected", "reason": "动作草稿不存在或不属于当前会话。", "offer_handle": handle}
        return _gateway_observation_update(state, queue, ledger, result, phase="action_gateway" if queue else "agent_loop", status="ActionDraftUnavailable")
    if not snapshot_matches_registry(offer):
        review = transition_draft(offer, DRAFT_REQUIRES_REVIEW, reason="operation_capability_snapshot_unavailable")
        review["read_only"] = True; review["updated_turn"] = int(state.get("turn_index") or 0)
        persist_draft_from_offer(state=state, offer=review, draft_state=DRAFT_REQUIRES_REVIEW)
        ledger = append_entries(ledger, [review])
        result = {"decision": "review_required", "reason": "该办理记录使用的能力合同已不可验证，只能查看或取消，未执行任何业务写操作。", "offer_handle": handle}
        return _gateway_observation_update(state, queue, ledger, result, phase="agent_loop", status="ActionCapabilityReviewRequired")
    target = find_handle(ledger, str(offer.get("target_handle") or ""), scope=scope_for_state(state), allowed_kinds={"artifact"}, allowed_resource_types=allowed_target_resource_types(str(offer.get("action_id") or "")), active_only=False)
    policy = policy_for_action(str(offer.get("action_id") or ""))
    if str(offer.get("draft_state") or "") == "NEEDS_INPUT" and target is not None:
        ledger, pending = _mark_offer_needs_input(state, offer, ledger)
        result = {"decision": "needs_input", "reason": str((offer.get("preview") or {}).get("message") or "请补充必要信息。"), "offer_handle": handle, "action_id": offer.get("action_id"), "risk_level": policy.risk_level, "preview": dict(offer.get("preview") or {})}
        contract = interaction_response_contract({**state, "artifact_ledger": ledger, **active_draft_patch(pending.get("handle"))})
        if contract is None:
            raise RuntimeError("needs-input offer could not produce an interaction contract")
        return _gateway_observation_update(state, queue, ledger, result, phase="offer_confirmation", status="ActionInputRequired", pending_offer=pending, response_contract=contract)
    ok, readiness_reason = deterministic_offer_readiness(offer=offer, current_turn=int(state.get("turn_index") or 0), target_exists=target is not None)
    if not ok:
        result = {"decision": "clarify", "reason": readiness_reason, "offer_handle": handle, "action_id": offer.get("action_id"), "risk_level": policy.risk_level}
        return _gateway_observation_update(state, queue, ledger, result, phase="action_gateway" if queue else "agent_loop", status="ActionDraftReadinessInsufficient")
    assert target is not None
    preview = _same_turn_prepared_preview(state, offer)
    if preview is not None:
        preflight, additions = {"success": True, "source": "same_turn_prepared_preview"}, []
    else:
        preflight, preview, additions = _refresh_offer_preflight(state, offer, target, deps=deps)
    ledger = append_entries(ledger, additions)
    if not preflight.get("success") or preview is None:
        stale = transition_draft(offer, "FAILED_FINAL", reason="latest_preflight_failed")
        stale["updated_turn"] = int(state.get("turn_index") or 0); stale["superseded_reason"] = "latest_preflight_failed"
        persist_draft_from_offer(state=state, offer=stale, draft_state="FAILED_FINAL")
        ledger = append_entries(ledger, [stale])
        result = {"decision": "rejected", "reason": str(preflight.get("error") or "业务预检失败"), "offer_handle": handle, "action_id": offer.get("action_id"), "risk_level": policy.risk_level}
        return _gateway_observation_update(state, queue, ledger, result, phase="action_gateway" if queue else "agent_loop", status="ActionPreflightFailed")
    decision = str(preview.get("decision") or "")
    refreshed = deepcopy(offer); refreshed["preview"] = preview
    values = dict(refreshed.get("input_values") or {})
    snapshot = preview.get("snapshot") if isinstance(preview.get("snapshot"), dict) else {}
    if snapshot.get("version") is not None:
        values["expected_version"] = int(snapshot.get("version") or values.get("expected_version") or 1)
    refreshed["input_values"] = values; refreshed["updated_turn"] = int(state.get("turn_index") or 0)
    if decision == "NEEDS_INPUT":
        refreshed = transition_draft(refreshed, "NEEDS_INPUT"); refreshed["required_inputs"] = list(preview.get("required_inputs") or [])
        ledger, pending = _mark_offer_needs_input(state, refreshed, ledger)
        result = {"decision": "needs_input", "reason": str(preview.get("message") or "业务动作需要补充信息"), "offer_handle": handle, "action_id": offer.get("action_id"), "risk_level": policy.risk_level, "preview": preview}
        contract = interaction_response_contract({**state, "artifact_ledger": ledger, **active_draft_patch(pending.get("handle"))})
        if contract is None:
            raise RuntimeError("pending input offer could not produce an interaction contract")
        return _gateway_observation_update(state, queue, ledger, result, phase="offer_confirmation", status="ActionInputRequired", pending_offer=pending, response_contract=contract)
    if decision not in {"ALLOWED", "NEEDS_REVIEW"}:
        refreshed = transition_draft(refreshed, "FAILED_FINAL", reason="latest_preflight_rejected"); refreshed["superseded_reason"] = "latest_preflight_rejected"
        persist_draft_from_offer(state=state, offer=refreshed, draft_state="FAILED_FINAL")
        ledger = append_entries(ledger, [refreshed])
        result = {"decision": "rejected", "reason": str(preview.get("message") or "当前业务状态不允许该动作。"), "offer_handle": handle, "action_id": offer.get("action_id"), "risk_level": policy.risk_level, "preview": preview}
        return _gateway_observation_update(state, queue, ledger, result, phase="action_gateway" if queue else "agent_loop", status="ActionPreflightRejected")
    ledger = append_entries(ledger, [refreshed])
    offer = find_handle(ledger, handle, scope=scope_for_state(state), allowed_kinds={"offer"}, active_only=False) or refreshed
    if policy.authority_requirement == "human_review":
        review = transition_draft(offer, "FAILED_FINAL", reason="human_review_required"); review["updated_turn"] = int(state.get("turn_index") or 0)
        persist_draft_from_offer(state=state, offer=review, draft_state="FAILED_FINAL"); ledger = append_entries(ledger, [review])
        result = {"decision": "human_review", "reason": "该动作按风险策略需人工审核，未执行任何业务写操作。", "offer_handle": handle, "action_id": offer.get("action_id"), "risk_level": policy.risk_level, "preview": preview}
        return _gateway_observation_update(state, queue, ledger, result, phase="action_gateway" if queue else "agent_loop", status="ActionHumanReview")
    ledger, pending = _mark_offer_awaiting_authority(state, offer, ledger)
    result = {"decision": "awaiting_structured_authority", "reason": "动作草稿已完成预检，等待用户对明确对象的结构化 UI 授权。", "authority_protocol": UI_AUTHORITY_PROTOCOL, "offer_handle": handle, "action_id": offer.get("action_id"), "target_handle": offer.get("target_handle"), "risk_level": policy.risk_level, "preview": preview}
    contract = interaction_response_contract({**state, "artifact_ledger": ledger, **active_draft_patch(pending.get("handle")), "pending_confirmation_id": pending.get("confirmation_id"), "pending_confirmation_version": pending.get("confirmation_version")})
    if contract is None:
        raise RuntimeError("pending authority offer could not produce a response contract")
    return _gateway_observation_update(state, queue, ledger, result, phase="offer_confirmation", status="ActionAuthorityRequired", pending_offer=pending, response_contract=contract)


def _gateway_observation_update(
    state: dict[str, Any], queue: list[dict[str, Any]], ledger: list[dict[str, Any]], result: dict[str, Any], *,
    phase: str, status: str, pending_offer: dict[str, Any] | None = None, pending_handle: str | None = None,
    response_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace = [*(state.get("tool_trace") or []), {"plan_id": str((state.get("current_turn_plan") or {}).get("plan_id") or ""), "loop_step": int(state.get("agent_loop_step") or 0), "name": "action_gateway", "args": {"offer_handle": result.get("offer_handle")}, "result": {"ok": result.get("decision") not in {"rejected"}, "data": result}, "classification": "transaction_gateway"}]
    return {
        "artifact_ledger": ledger, "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)), "action_queue": queue,
        "action_gateway_result": result, "tool_trace": trace,
        **active_draft_patch(str((pending_offer or {}).get("handle") or pending_handle or "") or None),
        "pending_confirmation_id": (pending_offer or {}).get("confirmation_id") if pending_offer else None,
        "pending_confirmation_version": (pending_offer or {}).get("confirmation_version") if pending_offer else None,
        "response_contract": response_contract, "commit_authority": None, "phase": phase, "status": status,
        "decision_chain": _append_decision(state, stage="action_gateway", decision=str(result.get("decision") or ""), details=result),
    }


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


def action_gateway_node(state: dict[str, Any], *, deps: TransactionExecutionDeps) -> dict[str, Any]:
    return advance_transaction_gateway(state, deps=deps)
