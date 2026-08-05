from __future__ import annotations

"""Durable transaction reconciliation.

Recovery replays the previously persisted business-command envelope with its
original idempotency key.  This module has no LangGraph dependency; the graph
node supplies the adapter execution and presentation-artifact callbacks.
"""

from typing import Any, Callable

from agent_core.business import BusinessServiceError
from agent_core.ledger import append_entries, find_handle, ledger_cards, receipt_entry, scope_for_state
from agent_core.transaction import command_digest_for_offer, transition_draft
from agent_core.storage.repositories.base import TransactionScope
from agent_core.transaction.focus import focused_draft_patch, get_focused_draft_id, next_focus_after_terminal
from agent_core.transaction.failure import classify_business_failure


def reconcile_attempts(
    state: dict[str, Any],
    *,
    store: Any,
    execute_envelope: Callable[[dict[str, Any], dict[str, Any], str], dict[str, Any]],
    new_resource_artifacts: Callable[[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]], list[dict[str, Any]]],
    record_transaction_receipt_fn: Callable[..., Any],
) -> dict[str, Any] | None:
    """Reconcile incomplete submissions from the persisted command envelope.

    Checkpoint/Ledger loss is not business failure.  Recovery uses the same
    idempotency key and the exact adapter command captured at authorization.
    """
    scope=scope_for_state(state)
    attempts=store.list_reconcilable_attempts(tenant_id=scope["tenant_id"],user_id=scope["user_id"],thread_id=scope["thread_id"],limit=20)
    if not attempts: return None
    ledger=list(state.get("artifact_ledger") or []); additions=[]; reconciled=[]
    for attempt in attempts:
        attempt_id=str(attempt.get("attempt_id") or ""); draft_id=str(attempt.get("draft_id") or "")
        durable=store.get_draft(draft_id) or {}; receipt_record=store.get_receipt_by_attempt(attempt_id)
        projection=dict(durable.get("projection") or {})
        offer=find_handle(ledger,draft_id,scope=scope,allowed_kinds={"offer"},active_only=False) or projection
        if receipt_record:
            state_name="COMMITTED" if str(receipt_record.get("receipt_state") or "") == "SUCCESS" else "FAILED_FINAL"
            if offer:
                restored=transition_draft(offer,state_name); restored["commit_attempt_id"]=attempt_id; additions.append(restored)
            store.transition_attempt(attempt_id,state="ACKED" if state_name=="COMMITTED" else "FAILED_FINAL",business_result=dict(receipt_record.get("business_result") or {}),receipt_handle=str(receipt_record.get("receipt_handle") or "") or None,reconciled=True)
            reconciled.append({"attempt_id":attempt_id,"state":"RECEIPT_REPROJECTED"}); continue
        envelope=dict(attempt.get("business_command_envelope") or durable.get("command_envelope") or {})
        if not envelope:
            # Missing Agent-local data is uncertainty, never proof of rejection.
            store.transition_attempt(attempt_id,state="RECONCILIATION_REQUIRED",error="durable command envelope missing",reconciled=True)
            store.advance_draft(draft_id,draft_state="SUBMISSION_UNKNOWN",current_attempt_id=attempt_id)
            if offer:
                unknown=transition_draft(offer,"SUBMISSION_UNKNOWN",reason="reconcile_envelope_missing"); unknown["commit_attempt_id"]=attempt_id; additions.append(unknown)
            reconciled.append({"attempt_id":attempt_id,"state":"RECONCILIATION_REQUIRED"}); continue
        raw=dict(offer or {})
        raw["business_command_envelope"]=envelope
        expected=str(attempt.get("command_digest") or durable.get("command_digest") or "")
        if expected and command_digest_for_offer(raw) != expected:
            store.transition_attempt(attempt_id,state="RECONCILIATION_REQUIRED",error="recorded command digest unverifiable",reconciled=True)
            store.advance_draft(draft_id,draft_state="SUBMISSION_UNKNOWN",current_attempt_id=attempt_id)
            if offer:
                unknown=transition_draft(offer,"SUBMISSION_UNKNOWN",reason="reconcile_digest_unverifiable"); unknown["commit_attempt_id"]=attempt_id; additions.append(unknown)
            reconciled.append({"attempt_id":attempt_id,"state":"RECONCILIATION_REQUIRED"}); continue
        try:
            result=execute_envelope(state, envelope, str(attempt.get("idempotency_key") or ""))
        except BusinessServiceError as exc:
            result={"success":False,"error":exc.message,"code":exc.status_code}
        except Exception as exc:
            result={"success":False,"error":f"reconcile exception: {exc.__class__.__name__}: {exc}","code":0}
        source=offer or {"kind":"offer","handle":draft_id,"draft_id":draft_id,"draft_revision":int(durable.get("draft_revision") or attempt.get("draft_revision") or 1),"draft_state":"COMMITTING","action_id":str(envelope.get("action_id") or attempt.get("action_id") or ""),"label":str(projection.get("label") or "业务操作"),"scope":scope,"business_command_envelope":envelope,"command_digest":expected}
        if result.get("success"):
            completed=transition_draft(source,"COMMITTED"); completed["commit_attempt_id"]=attempt_id
            receipt=receipt_entry(action_id=str(completed.get("action_id") or ""),result=result,scope=scope,turn=int(state.get("turn_index") or 0),label=str(completed.get("label") or "业务操作"),draft_id=draft_id,attempt_id=attempt_id,idempotency_key=str(attempt.get("idempotency_key") or ""))
            additions.extend([completed,receipt]); additions.extend(new_resource_artifacts(state, ledger, completed, result))
            store.transition_attempt(attempt_id,state="ACKED",business_result=result,receipt_handle=str(receipt.get("handle") or ""),reconciled=True)
            store.advance_draft(draft_id,draft_state="COMMITTED",current_attempt_id=attempt_id)
            record_transaction_receipt_fn(state=state,offer=completed,attempt_id=attempt_id,receipt_handle=str(receipt.get("handle") or ""),receipt_state="SUCCESS",business_result=result)
            grant_id=str(attempt.get("grant_id") or durable.get("active_grant_id") or "")
            if grant_id: store.consume_grant(grant_id,attempt_id=attempt_id,receipt_handle=str(receipt.get("handle") or ""))
            reconciled.append({"attempt_id":attempt_id,"state":"ACKED","receipt_handle":receipt.get("handle")}); continue
        classified=classify_business_failure(code=result.get("code"),error=str(result.get("error") or ""))
        if classified=="SUBMISSION_UNKNOWN":
            # A retryable transport ambiguity is not a terminal business
            # outcome.  Preserve the read-only draft and attempt for another
            # reconciliation pass; never fabricate a failed receipt.
            unknown=transition_draft(source,"SUBMISSION_UNKNOWN",reason="reconciliation_still_unknown"); unknown["commit_attempt_id"]=attempt_id; additions.append(unknown)
            store.transition_attempt(attempt_id,state="SUBMISSION_UNKNOWN",error_code=str(result.get("code") or ""),error=str(result.get("error") or ""),reconciled=True)
            store.advance_draft(draft_id,draft_state="SUBMISSION_UNKNOWN",current_attempt_id=attempt_id)
            reconciled.append({"attempt_id":attempt_id,"state":"SUBMISSION_UNKNOWN"})
            continue
        failed=transition_draft(source,classified,reason="reconciliation_business_rejection"); failed["commit_attempt_id"]=attempt_id
        receipt=receipt_entry(action_id=str(failed.get("action_id") or ""),result=result,scope=scope,turn=int(state.get("turn_index") or 0),label=str(failed.get("label") or "业务操作"),draft_id=draft_id,attempt_id=attempt_id,idempotency_key=str(attempt.get("idempotency_key") or ""))
        additions.extend([failed,receipt]); store.transition_attempt(attempt_id,state=classified,business_result=result,receipt_handle=str(receipt.get("handle") or ""),error_code=str(result.get("code") or ""),error=str(result.get("error") or ""),reconciled=True); store.advance_draft(draft_id,draft_state=classified,current_attempt_id=attempt_id)
        record_transaction_receipt_fn(state=state,offer=failed,attempt_id=attempt_id,receipt_handle=str(receipt.get("handle") or ""),receipt_state="FAILED",business_result=result)
        grant_id=str(attempt.get("grant_id") or durable.get("active_grant_id") or "")
        if grant_id: store.revoke_grant(grant_id,reason=classified)
    if not additions: return None
    merged=append_entries(ledger,additions)
    update: dict[str, Any] = {
        "artifact_ledger": merged,
        "ledger_snapshot": ledger_cards(merged, scope=scope),
        "transaction_reconciliation": reconciled,
    }
    focused = get_focused_draft_id(state)
    terminal_ids = {
        str(item.get("draft_id") or "")
        for item in additions
        if isinstance(item, dict)
        and str(item.get("kind") or "") == "offer"
        and str(item.get("draft_state") or "").upper() in {"COMMITTED", "FAILED_FINAL", "EXPIRED", "REVOKED"}
    }
    if focused and focused in terminal_ids:
        transaction_scope = TransactionScope(
            tenant_id=str(scope.get("tenant_id") or "default"),
            user_id=str(scope.get("user_id") or ""),
            thread_id=str(scope.get("thread_id") or "") or None,
        )
        update.update(
            focused_draft_patch(
                next_focus_after_terminal(
                    store,
                    scope=transaction_scope,
                    terminal_draft_id=focused,
                )
            )
        )
        update.update(
            {
                "pending_confirmation_id": None,
                "pending_confirmation_version": None,
                "response_contract": None,
            }
        )
    return update

