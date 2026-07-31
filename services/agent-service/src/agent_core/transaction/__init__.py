"""Agent-side transaction protocol primitives.

This package owns the *command lifecycle* only.  It does not own business facts,
permissions or business state transitions; those remain in Business Service.
"""

from .availability import check_transaction_repository_available, transaction_repository_unavailable_outcome
from .model import (
    DRAFT_STATES,
    TERMINAL_DRAFT_STATES,
    TRANSACTION_CONTRACT_VERSION,
    DRAFT_REQUIRES_REVIEW,
    canonical_command_payload,
    command_digest_for_offer,
    display_projection,
    ensure_transaction_draft,
    is_reusable_draft,
    transition_draft,
)

__all__ = [
    "check_transaction_repository_available",
    "transaction_repository_unavailable_outcome",
    "DRAFT_STATES",
    "TERMINAL_DRAFT_STATES",
    "TRANSACTION_CONTRACT_VERSION",
    "DRAFT_REQUIRES_REVIEW",
    "canonical_command_payload",
    "command_digest_for_offer",
    "display_projection",
    "ensure_transaction_draft",
    "is_reusable_draft",
    "transition_draft",
]
