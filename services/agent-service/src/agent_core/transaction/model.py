from __future__ import annotations

"""Compatibility exports for the canonical TransactionDraft data contract.

The pure draft data algebra is owned by :mod:`agent_core.operations.draft`.
Transaction continues to own authority, persistence, commit, reconciliation and
recovery.  This module intentionally contains no second implementation so
existing imports remain stable during the dependency cutover.
"""

from agent_core.operations.draft import (
    DRAFT_AUTHORIZED,
    DRAFT_AWAITING_AUTHORIZATION,
    DRAFT_COMMITTED,
    DRAFT_COMMITTING,
    DRAFT_EXPIRED,
    DRAFT_FAILED_FINAL,
    DRAFT_FAILED_RETRYABLE,
    DRAFT_NEEDS_INPUT,
    DRAFT_READY,
    DRAFT_REQUIRES_REVIEW,
    DRAFT_REVOKED,
    DRAFT_STATES,
    DRAFT_SUBMISSION_UNKNOWN,
    TERMINAL_DRAFT_STATES,
    TRANSACTION_CONTRACT_VERSION,
    canonical_command_payload,
    command_digest_for_offer,
    display_projection,
    draft_state_for_offer,
    ensure_transaction_draft,
    is_cancellable_draft,
    is_reusable_draft,
    transition_draft,
)

__all__ = [
    "DRAFT_AUTHORIZED",
    "DRAFT_AWAITING_AUTHORIZATION",
    "DRAFT_COMMITTED",
    "DRAFT_COMMITTING",
    "DRAFT_EXPIRED",
    "DRAFT_FAILED_FINAL",
    "DRAFT_FAILED_RETRYABLE",
    "DRAFT_NEEDS_INPUT",
    "DRAFT_READY",
    "DRAFT_REQUIRES_REVIEW",
    "DRAFT_REVOKED",
    "DRAFT_STATES",
    "DRAFT_SUBMISSION_UNKNOWN",
    "TERMINAL_DRAFT_STATES",
    "TRANSACTION_CONTRACT_VERSION",
    "canonical_command_payload",
    "command_digest_for_offer",
    "display_projection",
    "draft_state_for_offer",
    "ensure_transaction_draft",
    "is_cancellable_draft",
    "is_reusable_draft",
    "transition_draft",
]
