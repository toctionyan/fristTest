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


def grant_consumption_decision(
    grant: dict[str, Any] | None,
    *,
    attempt: dict[str, Any] | None,
    receipt: dict[str, Any] | None,
    attempt_id: str | None,
    receipt_handle: str | None,
) -> tuple[bool, str]:
    """Validate the single legal Grant consumption boundary.

    Consumption means a successful business effect is durably known.  The
    exact Grant must therefore already own the exact ACKED Attempt and the
    exact SUCCESS Receipt.  No caller may use ``consume_grant`` as a generic
    state setter.
    """
    if not isinstance(grant, dict) or not grant:
        return False, "grant_missing"
    grant_state = str(grant.get("state") or "").upper()
    requested_attempt = str(attempt_id or "")
    requested_receipt = str(receipt_handle or "")

    if grant_state == "CONSUMED":
        if (
            requested_attempt
            and requested_receipt
            and str(grant.get("attempt_id") or "") == requested_attempt
            and str(grant.get("receipt_handle") or "") == requested_receipt
        ):
            return True, "already_consumed_same_binding"
        return False, "consumed_grant_binding_immutable"

    if grant_state != "RESERVED":
        return False, f"grant_not_reserved:{grant_state or 'UNKNOWN'}"
    if not requested_attempt:
        return False, "consume_attempt_id_required"
    if not requested_receipt:
        return False, "consume_receipt_handle_required"
    if str(grant.get("attempt_id") or "") != requested_attempt:
        return False, "grant_attempt_binding_mismatch"

    if not isinstance(attempt, dict) or not attempt:
        return False, "consume_attempt_missing"
    if str(attempt.get("attempt_id") or "") != requested_attempt:
        return False, "consume_attempt_identity_mismatch"
    if str(attempt.get("grant_id") or "") != str(grant.get("grant_id") or ""):
        return False, "consume_attempt_grant_mismatch"
    for field in ("tenant_id", "user_id", "thread_id", "draft_id"):
        if str(attempt.get(field) or "") != str(grant.get(field) or ""):
            return False, f"consume_attempt_scope_mismatch:{field}"
    if int(attempt.get("draft_revision") or 0) != int(grant.get("draft_revision") or 0):
        return False, "consume_attempt_revision_mismatch"
    if str(attempt.get("command_digest") or "") != str(grant.get("command_digest") or ""):
        return False, "consume_attempt_command_mismatch"
    if str(attempt.get("state") or "").upper() != ATTEMPT_ACKED:
        return False, "consume_attempt_not_acked"
    if str(attempt.get("receipt_handle") or "") != requested_receipt:
        return False, "consume_attempt_receipt_mismatch"

    if not isinstance(receipt, dict) or not receipt:
        return False, "consume_receipt_missing"
    if str(receipt.get("attempt_id") or "") != requested_attempt:
        return False, "consume_receipt_attempt_mismatch"
    if str(receipt.get("receipt_handle") or "") != requested_receipt:
        return False, "consume_receipt_handle_mismatch"
    if str(receipt.get("receipt_state") or "").upper() != "SUCCESS":
        return False, "consume_receipt_not_success"
    for field in ("tenant_id", "user_id", "thread_id", "draft_id"):
        if str(receipt.get(field) or "") != str(grant.get(field) or ""):
            return False, f"consume_receipt_scope_mismatch:{field}"
    result = receipt.get("business_result") if isinstance(receipt.get("business_result"), dict) else {}
    if result.get("success") is not True:
        return False, "consume_receipt_result_not_success"
    return True, "grant_consumption_valid"


def grant_issue_decision(
    draft: dict[str, Any] | None,
    *,
    tenant_id: str, user_id: str, thread_id: str, draft_id: str,
    draft_revision: int, command_digest: str, confirmation_id: str,
) -> tuple[bool, str]:
    if not isinstance(draft, dict) or not draft:
        return False, "canonical_Draft_missing"
    if str(draft.get("draft_id") or "") != str(draft_id):
        return False, "canonical_Draft_identity_mismatch"
    for field, expected in (("tenant_id", tenant_id), ("user_id", user_id), ("thread_id", thread_id)):
        if str(draft.get(field) or "") != str(expected or ""):
            return False, f"canonical_Draft_scope_mismatch:{field}"
    if str(draft.get("draft_state") or "").upper() != "AWAITING_AUTHORIZATION":
        return False, "canonical_Draft_not_awaiting_authority"
    if int(draft.get("draft_revision") or 0) != int(draft_revision):
        return False, "canonical_Draft_revision_mismatch"
    if str(draft.get("command_digest") or "") != str(command_digest or ""):
        return False, "canonical_Draft_command_mismatch"
    projection = draft.get("projection") if isinstance(draft.get("projection"), dict) else {}
    durable_confirmation = str(projection.get("confirmation_id") or "")
    if durable_confirmation and durable_confirmation != str(confirmation_id or ""):
        return False, "canonical_Draft_confirmation_mismatch"
    return True, "grant_issue_valid"


def grant_reservation_decision(
    grant: dict[str, Any] | None, draft: dict[str, Any] | None,
    *,
    tenant_id: str, user_id: str, thread_id: str, draft_id: str,
    draft_revision: int, command_digest: str,
) -> tuple[bool, str]:
    if not isinstance(grant, dict) or not grant:
        return False, "grant_missing"
    if str(grant.get("state") or "").upper() != "ISSUED":
        return False, "grant_not_issued"
    for field, expected in (
        ("tenant_id", tenant_id), ("user_id", user_id), ("thread_id", thread_id), ("draft_id", draft_id)
    ):
        if str(grant.get(field) or "") != str(expected or ""):
            return False, f"grant_request_mismatch:{field}"
    if int(grant.get("draft_revision") or 0) != int(draft_revision):
        return False, "grant_request_revision_mismatch"
    if str(grant.get("command_digest") or "") != str(command_digest or ""):
        return False, "grant_request_command_mismatch"
    issue_ok, reason = grant_issue_decision(
        draft, tenant_id=tenant_id, user_id=user_id, thread_id=thread_id,
        draft_id=draft_id, draft_revision=draft_revision, command_digest=command_digest,
        confirmation_id=str(grant.get("confirmation_id") or ""),
    )
    if not issue_ok:
        return False, "reservation_" + reason
    return True, "grant_reservation_valid"


def existing_attempt_matches_request(
    attempt: dict[str, Any] | None,
    *, grant_id: str, tenant_id: str, user_id: str, thread_id: str,
    draft_id: str, draft_revision: int, action_id: str, command_digest: str,
) -> bool:
    if not isinstance(attempt, dict) or not attempt:
        return False
    for field, expected in (
        ("grant_id", grant_id), ("tenant_id", tenant_id), ("user_id", user_id),
        ("thread_id", thread_id), ("draft_id", draft_id), ("action_id", action_id),
    ):
        if str(attempt.get(field) or "") != str(expected or ""):
            return False
    return (
        int(attempt.get("draft_revision") or 0) == int(draft_revision)
        and str(attempt.get("command_digest") or "") == str(command_digest or "")
    )


def draft_terminal_observation_decision(
    current: dict[str, Any],
    *, target_state: str, attempt: dict[str, Any] | None, receipt: dict[str, Any] | None,
) -> tuple[bool, str]:
    current_state = str(current.get("draft_state") or "").upper()
    target = str(target_state or "").upper()
    effect_terminal = target == "COMMITTED" or (
        current_state in {"COMMITTING", "SUBMISSION_UNKNOWN", "FAILED_RETRYABLE"}
        and target in {"FAILED_RETRYABLE", "FAILED_FINAL"}
    )
    if not effect_terminal:
        return True, "no_business_receipt_required"
    attempt_id = str(current.get("current_attempt_id") or "")
    if not attempt_id:
        return False, "terminal_Draft_attempt_missing"
    if not isinstance(attempt, dict) or str(attempt.get("attempt_id") or "") != attempt_id:
        return False, "terminal_Draft_attempt_not_found"
    for field in ("tenant_id", "user_id", "thread_id", "draft_id"):
        if str(attempt.get(field) or "") != str(current.get(field) or ""):
            return False, f"terminal_Draft_attempt_scope_mismatch:{field}"
    if int(attempt.get("draft_revision") or 0) != int(current.get("draft_revision") or 0):
        return False, "terminal_Draft_attempt_revision_mismatch"
    if str(attempt.get("command_digest") or "") != str(current.get("command_digest") or ""):
        return False, "terminal_Draft_attempt_command_mismatch"
    if not isinstance(receipt, dict) or str(receipt.get("attempt_id") or "") != attempt_id:
        return False, "terminal_Draft_receipt_missing"
    if str(receipt.get("draft_id") or "") != str(current.get("draft_id") or ""):
        return False, "terminal_Draft_receipt_draft_mismatch"
    receipt_state = str(receipt.get("receipt_state") or "").upper()
    result = receipt.get("business_result") if isinstance(receipt.get("business_result"), dict) else {}
    if target == "COMMITTED":
        if receipt_state != "SUCCESS" or result.get("success") is not True:
            return False, "committed_Draft_requires_success_receipt"
    else:
        if receipt_state != "FAILED" or result.get("success") is True:
            return False, "failed_Draft_requires_failed_receipt"
    return True, "terminal_Draft_receipt_valid"
