from __future__ import annotations

from copy import deepcopy
from collections.abc import Set
from typing import Any

from agent_core.modules import current_runtime_registry
from agent_core.transaction.authority import validate_ui_authority
from agent_core.business import BusinessServiceError
from agent_core.ledger import append_entries, artifact_entry, authority_entry, find_handle, ledger_cards, receipt_entry, scope_for_state
from agent_core.kernel.decision_trace import append_decision as _append_decision
from agent_core.transaction.deps import TransactionExecutionDeps
from agent_core.transaction import DRAFT_REQUIRES_REVIEW, command_digest_for_offer, transition_draft
from agent_core.transaction.active_draft import active_draft_patch, get_active_draft_id
from agent_core.transaction.failure import classify_business_failure
from agent_core.transaction.coordinator import (
    record_transaction_receipt,
    reserve_grant_and_start_attempt,
    stable_command_id,
    stable_idempotency_key,
    transaction_store,
)
from agent_core.transaction.gateway_runtime import _actor_context_from_state, _mark_offer_awaiting_authority, _refresh_offer_preflight
from agent_core.transaction.capability_snapshot import snapshot_matches_registry
from agent_core.transaction.target_contract import allowed_target_resource_types, target_unavailable_message

class _RegisteredActionIdSet(Set[str]):
    """Live read-only view; module import never freezes Composition Root state."""

    def _values(self) -> set[str]:
        return set(current_runtime_registry().preparable_action_ids())

    def __contains__(self, value: object) -> bool:
        return value in self._values()

    def __iter__(self):
        return iter(sorted(self._values()))

    def __len__(self) -> int:
        return len(self._values())


COMMITTABLE_TRANSACTION_ACTION_IDS: Set[str] = _RegisteredActionIdSet()

def _idempotency_key(state: dict[str, Any], offer: dict[str, Any]) -> str:
    # Bound to the canonical effect-bearing command, not display text or a
    # mutable UI token.  Business Service independently hashes its request.
    return stable_idempotency_key(state, offer)


def _build_business_command_envelope(state: dict[str, Any], offer: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Build the immutable adapter command that an authority actually approves."""
    action = str(offer.get("action_id") or "")
    plugin = current_runtime_registry().operations.get(action)
    if action not in COMMITTABLE_TRANSACTION_ACTION_IDS or plugin is None:
        raise ValueError(f"未实现的业务动作：{action}")
    target_id = str(target.get("resource_id") or "")
    commit_target = {"resource_type": str(target.get("resource_type") or ""), "resource_id": target_id}
    envelope = plugin.build_business_command_envelope(
        actor=_actor_context_from_state(state), target=commit_target,
        input_values=dict(offer.get("input_values") or {}),
        preview=offer.get("preview") if isinstance(offer.get("preview"), dict) else None,
    )
    envelope["command_id"] = str(offer.get("command_id") or stable_command_id(state, offer))
    return envelope


def _execute_business_command_envelope(
    state: dict[str, Any],
    envelope: dict[str, Any],
    *,
    idempotency_key: str,
    deps: TransactionExecutionDeps,
) -> dict[str, Any]:
    action=str(envelope.get("action_id") or "")
    plugin=current_runtime_registry().operations.get(action)
    if action not in COMMITTABLE_TRANSACTION_ACTION_IDS or plugin is None:
        return {"success": False, "error": f"未实现的业务动作：{action}", "code":"INVALID_COMMAND_ENVELOPE"}
    try:
        return plugin.commit_envelope(deps.business_port, _actor_context_from_state(state), envelope=dict(envelope or {}), idempotency_key=idempotency_key)
    except ValueError as exc:
        return {"success": False, "error": str(exc), "code":"INVALID_COMMAND_ENVELOPE"}


def _new_resource_artifacts(
    state: dict[str, Any],
    ledger: list[dict[str, Any]],
    offer: dict[str, Any],
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    plugin = current_runtime_registry().operations.get(str(offer.get("action_id") or ""))
    if plugin is None or not result.get("success"):
        return []
    target = find_handle(
        ledger,
        str(offer.get("target_handle") or ""),
        scope=scope_for_state(state),
        allowed_kinds={"artifact"},
        active_only=False,
    ) or {}
    descriptors = plugin.project_result_artifacts(
        target={
            "resource_type": str(target.get("resource_type") or ""),
            "resource_id": str(target.get("resource_id") or ""),
        },
        result=result,
        existing_target=target,
    )
    rows: list[dict[str, Any]] = []
    for descriptor in descriptors:
        rows.append(
            artifact_entry(
                resource_type=str(descriptor.get("resource_type") or ""),
                resource_id=str(descriptor.get("resource_id") or ""),
                label=str(descriptor.get("label") or descriptor.get("resource_id") or ""),
                facts=dict(descriptor.get("facts") or {}),
                scope=scope_for_state(state),
                turn=int(state.get("turn_index") or 0),
                source="action_gateway_commit",
                freshness_version=int(descriptor.get("freshness_version") or 1),
                handle=str(descriptor.get("handle") or "") or None,
            )
        )
    return rows


def _transaction_commit_update(
    state: dict[str, Any],
    ledger: list[dict[str, Any]],
    offer: dict[str, Any],
    *,
    result: dict[str, Any],
    draft_state: str,
    attempt_id: str | None,
    idempotency_key: str | None,
    status: str,
    write_receipt: bool,
    deps: TransactionExecutionDeps,
) -> dict[str, Any]:
    """Project a durable transaction observation back to the Graph checkpoint."""
    next_offer = transition_draft(offer, draft_state)
    next_offer["updated_turn"] = int(state.get("turn_index") or 0)
    if attempt_id:
        next_offer["commit_attempt_id"] = attempt_id
    additions: list[dict[str, Any]] = [next_offer]
    receipt_handle: str | None = None
    if write_receipt:
        receipt = receipt_entry(
            action_id=str(offer.get("action_id") or ""),
            result=result,
            scope=scope_for_state(state),
            turn=int(state.get("turn_index") or 0),
            label=str(offer.get("label") or "业务操作"),
            draft_id=str(offer.get("draft_id") or offer.get("handle") or ""),
            attempt_id=attempt_id,
            idempotency_key=idempotency_key,
        )
        receipt_handle = str(receipt.get("handle") or "")
        additions.append(receipt)
    resource_artifacts = _new_resource_artifacts(state, ledger, offer, result)
    additions.extend(resource_artifacts)
    ledger = append_entries(ledger, additions)
    if write_receipt:
        record_transaction_receipt(state=state, offer=next_offer, attempt_id=attempt_id, receipt_handle=receipt_handle, receipt_state="SUCCESS" if result.get("success") else "FAILED", business_result=result)
    if attempt_id:
        transaction_store(state).advance_draft(str(next_offer.get("draft_id") or next_offer.get("handle") or ""), draft_state=draft_state, draft_revision=int(next_offer.get("draft_revision") or 1), command_digest=str(next_offer.get("command_digest") or command_digest_for_offer(next_offer)), command_envelope=dict(next_offer.get("business_command_envelope") or {}) or None, projection={key: next_offer.get(key) for key in ("kind","handle","draft_id","draft_revision","draft_state","label","action_id","operation","target_handle","input_values","preview","scope","command_id","command_digest","business_command_envelope") if key in next_offer}, current_attempt_id=attempt_id)
    if receipt_handle and attempt_id:
        transaction_store(state).transition_attempt(
            attempt_id,
            state="ACKED" if result.get("success") else draft_state,
            business_result=result,
            receipt_handle=receipt_handle,
        )
    grant_id = str(offer.get("active_grant_id") or "")
    if result.get("success") and grant_id:
        transaction_store(state).consume_grant(
            grant_id,
            attempt_id=attempt_id,
            receipt_handle=receipt_handle,
        )
    elif draft_state in {"FAILED_RETRYABLE", "FAILED_FINAL"} and grant_id:
        # A grant authorizes exactly one command attempt.  A retry/restart must
        # create a fresh Draft revision/Grant instead of silently reusing it.
        transaction_store(state).revoke_grant(grant_id, reason=draft_state)
    outcome_decision = "committed" if result.get("success") else (
        "submission_unknown" if draft_state == "SUBMISSION_UNKNOWN" else "commit_failed"
    )
    commit_result = {
        "decision": outcome_decision,
        "offer_handle": offer.get("handle"),
        "draft_id": offer.get("draft_id") or offer.get("handle"),
        "draft_state": draft_state,
        "attempt_id": attempt_id,
        "command_id": str(offer.get("command_id") or (offer.get("business_command_envelope") or {}).get("command_id") or "") or None,
        "idempotency_key": idempotency_key,
        "action_id": offer.get("action_id"),
        "message": (
            "业务动作已提交。" if result.get("success") else
            "提交结果正在确认中，请勿重复操作。" if draft_state == "SUBMISSION_UNKNOWN" else
            str(result.get("error") or "业务服务拒绝或返回异常。")
        ),
        "result": result,
    }
    answer = (
        f"已完成{str(offer.get('label') or '该操作')}。" if result.get("success") else
        "提交结果正在确认中，请勿重复操作；刷新后系统会继续对账。" if draft_state == "SUBMISSION_UNKNOWN" else
        f"未能完成{str(offer.get('label') or '该操作')}：{str(result.get('error') or '业务服务拒绝或状态已变化')}。"
    )
    # Keep transaction-control/audit carriers separate from ordinary discourse
    # reference evidence.  Draft/Receipt remain part of RuntimeOutcome proof,
    # while only Business Service-derived resource projections may become a
    # next-turn referent at the final customer-visible release boundary.
    resource_evidence_handles = list(dict.fromkeys(
        str(row.get("handle") or "")
        for row in resource_artifacts
        if str(row.get("handle") or "")
    ))
    runtime_evidence_handles = list(dict.fromkeys(
        value
        for value in [
            str(next_offer.get("handle") or ""),
            receipt_handle or "",
            *resource_evidence_handles,
        ]
        if value
    ))
    runtime_outcome = deps.outcome_factory(
        "commit" if result.get("success") else "submission_unknown" if draft_state == "SUBMISSION_UNKNOWN" else "failure",
        effects="committed" if result.get("success") else "unknown" if draft_state == "SUBMISSION_UNKNOWN" else "none",
        safe_to_continue=bool(result.get("success")),
        correlation_id=str(state.get("correlation_id") or "") or None,
        evidence_handles=runtime_evidence_handles,
        customer_safe_summary=answer,
        next_interaction="show_status" if result.get("success") or draft_state == "SUBMISSION_UNKNOWN" else "none",
        payload={"draft_state": draft_state, "attempt_id": attempt_id, "receipt_handle": receipt_handle, "result": result},
    ).as_dict()
    trace = [*(state.get("tool_trace") or []), {
        "plan_id": str((state.get("current_turn_plan") or {}).get("plan_id") or ""),
        "loop_step": int(state.get("agent_loop_step") or 0),
        "name": "commit_action",
        "args": {"offer_handle": offer.get("handle"), "action_id": offer.get("action_id"), "attempt_id": attempt_id},
        "result": {"ok": bool(result.get("success")), "data": commit_result, "runtime_outcome": runtime_outcome},
        "classification": "transaction_commit",
    }]
    return {
        "artifact_ledger": ledger,
        "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
        **active_draft_patch(None),
        "pending_confirmation_id": None,
        "pending_confirmation_version": None,
        "response_contract": None,
        "offer_execution_result": result,
        "action_gateway_result": commit_result,
        "runtime_outcome": runtime_outcome,
        "answer_evidence_handles": resource_evidence_handles,
        "tool_trace": trace,
        "current_final_answer": answer,
        "commit_authority": None,
        "phase": "action_gateway" if list(state.get("action_queue") or []) and draft_state != "SUBMISSION_UNKNOWN" else "final",
        "status": status,
        "decision_chain": _append_decision(state, stage="commit_action", decision=outcome_decision, details={"offer_handle": offer.get("handle"), "success": bool(result.get("success")), "draft_state": draft_state, "attempt_id": attempt_id}),
    }


def commit_action_node(state: dict[str, Any], *, deps: TransactionExecutionDeps) -> dict[str, Any]:
    ledger = list(state.get("artifact_ledger") or [])
    handle = str(get_active_draft_id(state) or "")
    offer = find_handle(ledger, handle, scope=scope_for_state(state), allowed_kinds={"offer"}, active_only=False)
    if not offer or str(offer.get("draft_state") or "") != "AUTHORIZED":
        return {"current_final_answer": "待提交动作已失效，未执行任何业务写操作。", "response_contract": None, "commit_authority": None, "phase": "final", "status": "ActionUnavailable"}
    if not snapshot_matches_registry(offer):
        review = transition_draft(offer, DRAFT_REQUIRES_REVIEW, reason="operation_capability_snapshot_unavailable")
        review["read_only"] = True
        review["updated_turn"] = int(state.get("turn_index") or 0)
        ledger = append_entries(ledger, [review])
        return {
            "artifact_ledger": ledger,
            "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
            "current_final_answer": "该办理记录使用的能力合同已不可验证，未执行任何业务写操作。",
            "response_contract": None,
            "commit_authority": None,
            **active_draft_patch(None),
            "phase": "final",
            "status": "ActionCapabilityReviewRequired",
        }
    authority = state.get("commit_authority") if isinstance(state.get("commit_authority"), dict) else None
    authority_ok, authority_reason = validate_ui_authority(
        authority=authority,
        offer=offer,
        current_revision=int(state.get("turn_index") or 0),
    )
    if not authority_ok:
        blocked = transition_draft(offer, "REVOKED", reason=authority_reason)
        blocked["updated_turn"] = int(state.get("turn_index") or 0)
        ledger = append_entries(ledger, [blocked])
        if authority and str(authority.get("grant_id") or ""):
            transaction_store(state).revoke_grant(str(authority.get("grant_id") or ""), reason=authority_reason)
        return {
            "artifact_ledger": ledger,
            "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
            "current_final_answer": "业务提交授权无效或已过期，未执行任何业务写操作。",
            "commit_authority": None,
            **active_draft_patch(None),
            "pending_confirmation_id": None,
            "pending_confirmation_version": None,
            "response_contract": None,
            "phase": "final",
            "status": "ActionAuthorityInvalid",
            "decision_chain": _append_decision(state, stage="commit_action", decision="authority_rejected", details={"reason": authority_reason, "offer_handle": handle}),
        }
    target = find_handle(ledger, str(offer.get("target_handle") or ""), scope=scope_for_state(state), allowed_kinds={"artifact"}, allowed_resource_types=allowed_target_resource_types(str(offer.get("action_id") or "")), active_only=False)
    if not target:
        return {"current_final_answer": target_unavailable_message(str(offer.get("action_id") or "")), "response_contract": None, "commit_authority": None, "phase": "final", "status": "ActionTargetUnavailable"}

    # Commit-time latest preflight protects against changed business state.
    preflight, preview, additions = _refresh_offer_preflight(state, offer, target, deps=deps)
    ledger = append_entries(ledger, additions)
    if not preflight.get("success") or preview is None or str(preview.get("decision") or "") not in {"ALLOWED", "NEEDS_REVIEW"}:
        failed = transition_draft(offer, "FAILED_FINAL", reason="commit_time_preflight_rejected")
        failed["updated_turn"] = int(state.get("turn_index") or 0)
        ledger = append_entries(ledger, [failed])
        result = {"success": False, "error": str((preview or {}).get("message") or preflight.get("error") or "业务状态已变化，无法提交。")}
        return _transaction_commit_update(state, ledger, failed, result=result, draft_state="FAILED_FINAL", attempt_id=None, idempotency_key=None, status="ActionCommitPreflightRejected", write_receipt=False, deps=deps)

    refreshed = deepcopy(offer)
    values = dict(refreshed.get("input_values") or {})
    snapshot = preview.get("snapshot") if isinstance(preview.get("snapshot"), dict) else {}
    if snapshot.get("version") is not None:
        values["expected_version"] = int(snapshot.get("version") or values.get("expected_version") or 1)
    refreshed["input_values"] = values
    refreshed["preview"] = preview
    try:
        refreshed["business_command_envelope"] = _build_business_command_envelope(state, refreshed, target)
    except ValueError as exc:
        failed = transition_draft(refreshed, "FAILED_FINAL", reason="commit_command_envelope_invalid")
        return _transaction_commit_update(state, ledger, failed, result={"success":False,"error":str(exc),"code":"INVALID_COMMAND_ENVELOPE"}, draft_state="FAILED_FINAL", attempt_id=None, idempotency_key=None, status="ActionCommitEnvelopeInvalid", write_receipt=False, deps=deps)
    refreshed["command_id"] = str((refreshed.get("business_command_envelope") or {}).get("command_id") or stable_command_id(state, refreshed))
    refreshed["updated_turn"] = int(state.get("turn_index") or 0)
    ledger = append_entries(ledger, [refreshed])
    refreshed = find_handle(ledger, handle, scope=scope_for_state(state), allowed_kinds={"offer"}, active_only=False) or refreshed

    # A preflight can change expected_version or another effect-bearing field.
    # Never use authority for the old command snapshot to submit the new one.
    if str(authority.get("command_digest") or "") != str(refreshed.get("command_digest") or command_digest_for_offer(refreshed)):
        if str(authority.get("grant_id") or ""):
            transaction_store(state).revoke_grant(str(authority.get("grant_id") or ""), reason="command_digest_changed_after_preflight")
        ledger, pending = _mark_offer_awaiting_authority(state, refreshed, ledger)
        contract = interaction_response_contract({**state, "artifact_ledger": ledger, **active_draft_patch(pending.get("handle"))})
        return {
            "artifact_ledger": ledger,
            "ledger_snapshot": ledger_cards(ledger, scope=scope_for_state(state)),
            **active_draft_patch(pending.get("handle")),
            "pending_confirmation_id": pending.get("confirmation_id"),
            "pending_confirmation_version": pending.get("confirmation_version"),
            "response_contract": contract,
            "commit_authority": None,
            "current_final_answer": None,
            "phase": "offer_confirmation",
            "status": "ActionAuthorityReconfirmationRequired",
            "action_gateway_result": {"decision": "reauthorize", "offer_handle": pending.get("handle"), "message": "业务快照已更新，请确认最新内容后再提交。"},
        }

    reservation, attempt_started = reserve_grant_and_start_attempt(state=state, offer=refreshed, authority=authority)
    attempt = dict(attempt_started.get("attempt") or {})
    attempt_id = str(attempt.get("attempt_id") or "") or None
    idempotency_key = str(attempt.get("idempotency_key") or stable_idempotency_key(state, refreshed))
    if not reservation.get("reserved"):
        repository = transaction_store(state)
        existing_receipt = repository.get_receipt_by_attempt(attempt_id) if attempt_id else None
        if isinstance(existing_receipt, dict):
            known_result = existing_receipt.get("business_result") if isinstance(existing_receipt.get("business_result"), dict) else {}
            receipt_state = str(existing_receipt.get("receipt_state") or "").upper()
            if receipt_state == "SUCCESS" and bool(known_result.get("success")):
                return _transaction_commit_update(
                    state, ledger, refreshed, result=dict(known_result), draft_state="COMMITTED",
                    attempt_id=attempt_id, idempotency_key=idempotency_key,
                    status="ActionAlreadyCommitted", write_receipt=False, deps=deps,
                )
            if receipt_state == "FAILED":
                known_attempt_state = str(attempt.get("state") or "").upper()
                known_failure_state = known_attempt_state if known_attempt_state in {"FAILED_RETRYABLE", "FAILED_FINAL"} else "FAILED_FINAL"
                return _transaction_commit_update(
                    state, ledger, refreshed,
                    result=dict(known_result or {"success": False, "error": "业务提交已失败。"}),
                    draft_state=known_failure_state, attempt_id=attempt_id, idempotency_key=idempotency_key,
                    status="ActionAlreadyFailed", write_receipt=False, deps=deps,
                )
        # No Receipt means the exact existing Attempt is genuinely uncertain.
        # Never execute a second business command; reconciliation owns recovery.
        unknown = transition_draft(refreshed, "SUBMISSION_UNKNOWN", reason="grant_already_reserved_or_consumed")
        unknown["commit_attempt_id"] = attempt_id
        ledger = append_entries(ledger, [unknown])
        return _transaction_commit_update(
            state, ledger, unknown,
            result={"success": False, "error": "提交结果正在确认中，请勿重复操作。", "code": "SUBMISSION_UNKNOWN"},
            draft_state="SUBMISSION_UNKNOWN", attempt_id=attempt_id, idempotency_key=idempotency_key,
            status="ActionCommitAlreadyInProgress", write_receipt=False, deps=deps,
        )

    committing = transition_draft(refreshed, "COMMITTING")
    committing["active_grant_id"] = authority.get("grant_id")
    committing["commit_attempt_id"] = attempt_id
    committing["updated_turn"] = int(state.get("turn_index") or 0)
    ledger = append_entries(ledger, [committing])
    offer = find_handle(ledger, handle, scope=scope_for_state(state), allowed_kinds={"offer"}, active_only=False) or committing

    try:
        result = _execute_business_command_envelope(state, dict(offer.get("business_command_envelope") or {}), idempotency_key=idempotency_key, deps=deps)
    except BusinessServiceError as exc:
        result = {"success": False, "error": exc.message, "code": exc.status_code}
    except Exception as exc:
        result = {"success": False, "error": f"业务提交异常：{exc.__class__.__name__}: {exc}", "code": 0}

    if result.get("success"):
        return _transaction_commit_update(state, ledger, offer, result=result, draft_state="COMMITTED", attempt_id=attempt_id, idempotency_key=idempotency_key, status="ActionCommitted", write_receipt=True, deps=deps)

    failed_state = classify_business_failure(code=result.get("code"), error=str(result.get("error") or ""))
    if failed_state == "SUBMISSION_UNKNOWN":
        transaction_store(state).transition_attempt(
            str(attempt_id or ""),
            state=failed_state,
            business_result=None,
            error_code=str(result.get("code") or ""),
            error=str(result.get("error") or ""),
        )
    return _transaction_commit_update(
        state, ledger, offer, result=result, draft_state=failed_state,
        attempt_id=attempt_id, idempotency_key=idempotency_key,
        status="ActionSubmissionUnknown" if failed_state == "SUBMISSION_UNKNOWN" else "ActionCommitFailed",
        write_receipt=failed_state != "SUBMISSION_UNKNOWN", deps=deps,
    )


def _commit_observation_update(
    state: dict[str, Any],
    ledger: list[dict[str, Any]],
    offer: dict[str, Any],
    result: dict[str, Any],
    *,
    status: str,
    deps: TransactionExecutionDeps,
) -> dict[str, Any]:
    """Compatibility wrapper retained for old tests/imports.

    New code calls ``_transaction_commit_update`` directly so the draft state,
    attempt and receipt are always recorded together.
    """
    return _transaction_commit_update(
        state,
        ledger,
        offer,
        result=result,
        draft_state="COMMITTED" if result.get("success") else "FAILED_FINAL",
        attempt_id=None,
        idempotency_key=None,
        status=status,
        write_receipt=False,
        deps=deps,
    )
