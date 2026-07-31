from __future__ import annotations

"""Canonical operation-level TransactionDraft data contract.

An ``offer`` ledger row is the persisted ``TransactionDraft`` carrier and is
identified by ``draft_id == offer.handle``. This module is pure data algebra: it
performs no authorization, persistence, repository access or business commit.
``draft_state`` remains the sole lifecycle authority and presentation is derived.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

DRAFT_NEEDS_INPUT = "NEEDS_INPUT"
DRAFT_READY = "READY"
DRAFT_AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"
DRAFT_AUTHORIZED = "AUTHORIZED"
DRAFT_COMMITTING = "COMMITTING"
DRAFT_COMMITTED = "COMMITTED"
DRAFT_FAILED_RETRYABLE = "FAILED_RETRYABLE"
DRAFT_FAILED_FINAL = "FAILED_FINAL"
DRAFT_SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
DRAFT_EXPIRED = "EXPIRED"
DRAFT_REVOKED = "REVOKED"
DRAFT_REQUIRES_REVIEW = "REQUIRES_REVIEW"

TRANSACTION_CONTRACT_VERSION = 1

DRAFT_STATES = {
    DRAFT_NEEDS_INPUT,
    DRAFT_READY,
    DRAFT_AWAITING_AUTHORIZATION,
    DRAFT_AUTHORIZED,
    DRAFT_COMMITTING,
    DRAFT_COMMITTED,
    DRAFT_FAILED_RETRYABLE,
    DRAFT_FAILED_FINAL,
    DRAFT_SUBMISSION_UNKNOWN,
    DRAFT_EXPIRED,
    DRAFT_REVOKED,
    DRAFT_REQUIRES_REVIEW,
}

TERMINAL_DRAFT_STATES = {
    DRAFT_COMMITTED,
    DRAFT_FAILED_FINAL,
    DRAFT_EXPIRED,
    DRAFT_REVOKED,
    DRAFT_REQUIRES_REVIEW,
}

# Display fields are derived from canonical ``draft_state`` only.
_DISPLAY_PROJECTION: dict[str, tuple[str, str]] = {
    DRAFT_NEEDS_INPUT: ("needs_input", "needs_input"),
    DRAFT_READY: ("ready", "ready"),
    DRAFT_AWAITING_AUTHORIZATION: ("pending_confirmation", "awaiting_authority"),
    DRAFT_AUTHORIZED: ("pending_confirmation", "authorized"),
    DRAFT_COMMITTING: ("pending_confirmation", "committing"),
    DRAFT_COMMITTED: ("executed", "committed"),
    DRAFT_FAILED_RETRYABLE: ("failed", "failed_retryable"),
    DRAFT_FAILED_FINAL: ("failed", "failed_final"),
    DRAFT_SUBMISSION_UNKNOWN: ("submission_unknown", "submission_unknown"),
    # Preserve the established client-facing superseded vocabulary while
    # keeping the canonical transaction state explicit.
    DRAFT_EXPIRED: ("superseded", "interaction_superseded"),
    DRAFT_REVOKED: ("declined", "authority_rejected"),
    DRAFT_REQUIRES_REVIEW: ("review_required", "requires_review"),
}

def draft_state_for_offer(offer: dict[str, Any]) -> str:
    """Return the current-contract lifecycle state for an offer."""
    explicit = str(offer.get("draft_state") or "").upper()
    return explicit if explicit in DRAFT_STATES else DRAFT_READY


def display_projection(draft_state: str) -> tuple[str, str]:
    """Map a canonical draft state to non-authoritative display values."""
    return _DISPLAY_PROJECTION.get(str(draft_state or "").upper(), _DISPLAY_PROJECTION[DRAFT_READY])


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(_clean(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def canonical_command_payload(offer: dict[str, Any]) -> dict[str, Any]:
    """Return only effect-bearing canonical command material.

    Natural-language messages, labels, UI prose, timestamps and display-only
    preview text are intentionally excluded.  Business Service remains the
    final authoritative validator, but this digest proves that the command
    shown/authorized by Agent is the command submitted by Agent.
    """
    envelope = offer.get("business_command_envelope") if isinstance(offer.get("business_command_envelope"), dict) else None
    if envelope:
        return {
            "contract": str(envelope.get("contract") or "business_adapter.commit@1"),
            "method": str(envelope.get("method") or "POST"),
            "path": str(envelope.get("path") or ""),
            "action_id": str(envelope.get("action_id") or offer.get("action_id") or ""),
            "operation": str(envelope.get("operation") or offer.get("operation") or ""),
            "target": _clean(dict(envelope.get("target") or {})),
            # Command envelopes use ``input``. Omitting it from the digest
            # would let a form value change without changing the command digest.
            "payload": _clean(dict(envelope.get("input") if isinstance(envelope.get("input"), dict) else envelope.get("payload") or {})),
            "actor_scope": _clean(dict(envelope.get("actor_scope") or {})),
        }
    preview = offer.get("preview") if isinstance(offer.get("preview"), dict) else {}
    snapshot = preview.get("snapshot") if isinstance(preview.get("snapshot"), dict) else {}
    values = dict(offer.get("input_values") or {})
    return {
        "tenant_id": str((offer.get("scope") or {}).get("tenant_id") or "default"),
        "user_id": str((offer.get("scope") or {}).get("user_id") or ""),
        "thread_id": str((offer.get("scope") or {}).get("thread_id") or ""),
        "action_id": str(offer.get("action_id") or ""),
        "operation": str(offer.get("operation") or ""),
        "target_handle": str(offer.get("target_handle") or ""),
        "operation_capability": _clean(dict(offer.get("operation_capability_snapshot") or {})),
        "input_values": _clean(values),
        "expected_version": values.get("expected_version"),
        # Business preview is not business truth.  Only the deterministic
        # decision/snapshot identifiers that affect the command contract bind
        # the revision.
        "policy_snapshot": {
            "decision": str(preview.get("decision") or ""),
            "snapshot_version": snapshot.get("version"),
            "policy_version": preview.get("policy_version"),
        },
    }


def command_digest_for_offer(offer: dict[str, Any]) -> str:
    return sha256(_canonical_json(canonical_command_payload(offer)).encode("utf-8")).hexdigest()


def ensure_transaction_draft(offer: dict[str, Any], *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize an offer carrier into one canonical TransactionDraft.

    ``previous`` is the existing ledger row when an update is appended.  A
    changed effect-bearing payload increments revision and invalidates any old
    grant by construction because the digest changes.
    """
    row = deepcopy(offer)
    if str(row.get("kind") or "") != "offer":
        return row
    prior = deepcopy(previous) if isinstance(previous, dict) else None
    state = draft_state_for_offer(row)
    row["draft_id"] = str(row.get("draft_id") or row.get("handle") or "")
    row["transaction_schema_version"] = 2
    row["transaction_contract_version"] = TRANSACTION_CONTRACT_VERSION
    row["draft_state"] = state
    old_payload = canonical_command_payload(prior) if prior else None
    new_payload = canonical_command_payload(row)
    revision = int(row.get("draft_revision") or (prior or {}).get("draft_revision") or 1)
    if prior and old_payload != new_payload:
        revision = max(int(prior.get("draft_revision") or 1) + 1, revision + (0 if int(row.get("draft_revision") or 0) > int(prior.get("draft_revision") or 0) else 1))
        row["grant_invalidated_reason"] = "effect_bearing_payload_changed"
        row["active_grant_id"] = None
    row["draft_revision"] = max(1, revision)
    row["command_payload"] = new_payload
    row["command_digest"] = command_digest_for_offer(row)
    # Canonical runtime drafts retain only draft_state. Display fields are
    # generated at the API/Ledger presentation boundary.
    row.pop("status", None)
    row.pop("action_state", None)
    return row


def transition_draft(offer: dict[str, Any], draft_state: str, *, reason: str | None = None, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    state = str(draft_state or "").upper()
    if state not in DRAFT_STATES:
        raise ValueError(f"unsupported draft_state: {draft_state}")
    row = deepcopy(offer)
    row["draft_state"] = state
    # Runtime state is canonical-only. Display projection fields are never
    # persisted back into offers after a transition.
    row.pop("status", None)
    row.pop("action_state", None)
    if reason:
        row["draft_state_reason"] = str(reason)
    return ensure_transaction_draft(row, previous=previous)


def is_reusable_draft(offer: dict[str, Any]) -> bool:
    # Reusability is determined only from the canonical lifecycle state.
    state = str(offer.get("draft_state") or "").upper()
    return state in {DRAFT_NEEDS_INPUT, DRAFT_READY, DRAFT_AWAITING_AUTHORIZATION}


def is_cancellable_draft(offer: dict[str, Any]) -> bool:
    """A review-only draft can be explicitly closed, never submitted."""
    state = str(offer.get("draft_state") or "").upper()
    return state in {DRAFT_NEEDS_INPUT, DRAFT_READY, DRAFT_AWAITING_AUTHORIZATION, DRAFT_REQUIRES_REVIEW}
