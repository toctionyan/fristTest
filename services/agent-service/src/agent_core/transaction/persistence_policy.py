from __future__ import annotations

from typing import Any


ATTEMPT_STARTED = "STARTED"
ATTEMPT_SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
ATTEMPT_RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
ATTEMPT_ACKED = "ACKED"
ATTEMPT_FAILED_RETRYABLE = "FAILED_RETRYABLE"
ATTEMPT_FAILED_FINAL = "FAILED_FINAL"

SEALED_ATTEMPT_STATES = {
    ATTEMPT_ACKED,
    ATTEMPT_FAILED_RETRYABLE,
    ATTEMPT_FAILED_FINAL,
}

_ALLOWED_ATTEMPT_TRANSITIONS: dict[str, set[str]] = {
    ATTEMPT_STARTED: {
        ATTEMPT_STARTED,
        ATTEMPT_SUBMISSION_UNKNOWN,
        ATTEMPT_RECONCILIATION_REQUIRED,
        ATTEMPT_ACKED,
        ATTEMPT_FAILED_RETRYABLE,
        ATTEMPT_FAILED_FINAL,
    },
    ATTEMPT_SUBMISSION_UNKNOWN: {
        ATTEMPT_SUBMISSION_UNKNOWN,
        ATTEMPT_RECONCILIATION_REQUIRED,
        ATTEMPT_ACKED,
        ATTEMPT_FAILED_RETRYABLE,
        ATTEMPT_FAILED_FINAL,
    },
    ATTEMPT_RECONCILIATION_REQUIRED: {
        ATTEMPT_RECONCILIATION_REQUIRED,
        ATTEMPT_ACKED,
        ATTEMPT_FAILED_RETRYABLE,
        ATTEMPT_FAILED_FINAL,
    },
    ATTEMPT_ACKED: {ATTEMPT_ACKED},
    ATTEMPT_FAILED_RETRYABLE: {ATTEMPT_FAILED_RETRYABLE},
    ATTEMPT_FAILED_FINAL: {ATTEMPT_FAILED_FINAL},
}


def validate_receipt_binding(
    *,
    attempt: dict[str, Any] | None,
    grant: dict[str, Any] | None,
    tenant_id: str,
    user_id: str,
    thread_id: str,
    draft_id: str,
    attempt_id: str | None,
    receipt_state: str,
    business_result: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Prove that a Receipt closes one exact persisted Attempt/Grant chain."""
    if not attempt_id:
        return False, "receipt_attempt_id_required"
    if not isinstance(attempt, dict) or not attempt:
        return False, "receipt_attempt_missing"
    if str(attempt.get("attempt_id") or "") != str(attempt_id):
        return False, "receipt_attempt_identity_mismatch"
    for field, expected in (
        ("tenant_id", tenant_id),
        ("user_id", user_id),
        ("thread_id", thread_id),
        ("draft_id", draft_id),
    ):
        if str(attempt.get(field) or "") != str(expected or ""):
            return False, f"receipt_attempt_scope_mismatch:{field}"

    grant_id = str(attempt.get("grant_id") or "")
    if not grant_id or not isinstance(grant, dict) or not grant:
        return False, "receipt_grant_missing"
    if str(grant.get("grant_id") or "") != grant_id:
        return False, "receipt_grant_identity_mismatch"
    for field, expected in (
        ("tenant_id", tenant_id),
        ("user_id", user_id),
        ("thread_id", thread_id),
        ("draft_id", draft_id),
    ):
        if str(grant.get(field) or "") != str(expected or ""):
            return False, f"receipt_grant_scope_mismatch:{field}"
    if int(grant.get("draft_revision") or 0) != int(attempt.get("draft_revision") or 0):
        return False, "receipt_grant_revision_mismatch"
    if str(grant.get("command_digest") or "") != str(attempt.get("command_digest") or ""):
        return False, "receipt_grant_command_mismatch"
    if str(grant.get("state") or "").upper() not in {"RESERVED", "CONSUMED"}:
        return False, "receipt_grant_not_reserved"

    state = str(receipt_state or "").upper()
    if state not in {"SUCCESS", "FAILED"}:
        return False, "receipt_state_invalid"
    result = business_result if isinstance(business_result, dict) else {}
    if state == "SUCCESS" and result.get("success") is not True:
        return False, "success_receipt_requires_success_result"
    if state == "FAILED" and result.get("success") is True:
        return False, "failed_receipt_conflicts_with_success_result"

    attempt_state = str(attempt.get("state") or "").upper()
    if attempt_state == ATTEMPT_ACKED and state != "SUCCESS":
        return False, "acked_attempt_conflicts_with_failed_receipt"
    if attempt_state in {ATTEMPT_FAILED_RETRYABLE, ATTEMPT_FAILED_FINAL} and state != "FAILED":
        return False, "failed_attempt_conflicts_with_success_receipt"
    return True, "receipt_binding_valid"


def attempt_persistence_update_decision(
    current: dict[str, Any] | None,
    *,
    target_state: str,
    business_result: dict[str, Any] | None,
    receipt_handle: str | None,
    receipt: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Validate one mutation of an immutable Attempt identity."""
    if not isinstance(current, dict) or not current:
        return False, "attempt_missing"
    current_state = str(current.get("state") or "").upper()
    incoming_state = str(target_state or "").upper()
    if current_state in SEALED_ATTEMPT_STATES:
        return False, "attempt_sealed"
    allowed = _ALLOWED_ATTEMPT_TRANSITIONS.get(current_state, {current_state})
    if incoming_state not in allowed:
        return False, f"illegal_attempt_transition:{current_state}->{incoming_state}"

    receipt_state = str((receipt or {}).get("receipt_state") or "").upper()
    if incoming_state in SEALED_ATTEMPT_STATES:
        if not receipt_state:
            return False, "terminal_attempt_requires_receipt"
        if receipt_state == "SUCCESS" and incoming_state != ATTEMPT_ACKED:
            return False, "success_receipt_requires_acked_attempt"
        if receipt_state == "FAILED" and incoming_state not in {ATTEMPT_FAILED_RETRYABLE, ATTEMPT_FAILED_FINAL}:
            return False, "failed_receipt_requires_failed_attempt"
        if receipt_state not in {"SUCCESS", "FAILED"}:
            return False, "terminal_attempt_receipt_invalid"
    elif receipt_state:
        # Once a business Receipt is durable, stale workers may not push the
        # Attempt back into an uncertain/nonterminal state.
        return False, "receipt_already_terminal"

    existing_result = current.get("business_result") if isinstance(current.get("business_result"), dict) else None
    if existing_result is not None and business_result is not None and existing_result != business_result:
        return False, "attempt_business_result_conflict"
    existing_receipt = str(current.get("receipt_handle") or "")
    incoming_receipt = str(receipt_handle or "")
    if existing_receipt and incoming_receipt and existing_receipt != incoming_receipt:
        return False, "attempt_receipt_handle_conflict"
    return True, "attempt_transition_valid"
