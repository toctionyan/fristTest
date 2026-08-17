from __future__ import annotations

"""Read-through projection recovery for durable pending transactions.

The transaction repository remains the only authority for Draft lifecycle.
This module may restore a lost Workflow/UI projection, but it never creates a
Draft, Grant or Attempt and never interprets user language as authorization.
"""

from typing import Any

from agent_core.ledger import append_entries
from agent_core.storage.repositories.base import TransactionScope
from agent_core.transaction.active_draft import active_draft_patch, get_active_draft_id

_AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"


def _scope(state: dict[str, Any]) -> TransactionScope:
    return TransactionScope(
        tenant_id=str(state.get("current_tenant_id") or "default"),
        user_id=str(state.get("current_user_id") or ""),
        thread_id=str(state.get("current_thread_id") or "") or None,
    )


def _authoritative_offer(row: dict[str, Any]) -> dict[str, Any] | None:
    projection = row.get("projection") if isinstance(row.get("projection"), dict) else {}
    draft_id = str(row.get("draft_id") or "")
    if not draft_id or not projection:
        return None
    offer = dict(projection)
    offer.update(
        {
            "kind": "offer",
            "handle": draft_id,
            "draft_id": draft_id,
            "draft_revision": int(row.get("draft_revision") or projection.get("draft_revision") or 1),
            "draft_state": str(row.get("draft_state") or ""),
            "action_id": str(row.get("action_id") or projection.get("action_id") or ""),
            "command_digest": str(row.get("command_digest") or projection.get("command_digest") or ""),
        }
    )
    if isinstance(row.get("command_envelope"), dict):
        offer["business_command_envelope"] = dict(row["command_envelope"])
    return offer


def _recoverable_authority_offer(row: dict[str, Any]) -> dict[str, Any] | None:
    if str(row.get("draft_state") or "").upper() != _AWAITING_AUTHORIZATION:
        return None
    offer = _authoritative_offer(row)
    if offer is None:
        return None
    # Reuse the exact persisted UI challenge. Missing challenge metadata fails
    # closed rather than minting a new token during Workflow recovery.
    if not str(offer.get("authority_protocol") or ""):
        return None
    if not str(offer.get("confirmation_id") or ""):
        return None
    if int(offer.get("confirmation_version") or 0) < 1:
        return None
    if int(offer.get("authority_revision") or 0) < 1:
        return None
    return offer


def restore_awaiting_authority_projection(
    state: dict[str, Any], *, transactions: Any
) -> dict[str, Any] | None:
    """Restore one unambiguous pending-authority card from its source of truth.

    A focused Draft is resolved exactly. If the ephemeral focus was also lost,
    recovery is allowed only when this thread has exactly one durable
    ``AWAITING_AUTHORIZATION`` Draft. Multiple candidates are never guessed.
    """
    scope = _scope(state)
    if not scope.user_id:
        return None
    focused = str(get_active_draft_id(state) or "")
    try:
        if focused:
            row = transactions.get_draft_for_scope(scope=scope, draft_id=focused)
            candidates = [row] if isinstance(row, dict) else []
        else:
            candidates = transactions.list_drafts_for_scope(
                scope=scope,
                states={_AWAITING_AUTHORIZATION},
                limit=2,
            )
    except Exception:
        return None
    if len(candidates) != 1:
        return None
    offer = _recoverable_authority_offer(dict(candidates[0]))
    if offer is None:
        return None
    draft_id = str(offer.get("draft_id") or offer.get("handle") or "")
    ledger = append_entries(list(state.get("artifact_ledger") or []), [offer])
    return {
        "artifact_ledger": ledger,
        **active_draft_patch(draft_id),
        "pending_confirmation_id": offer.get("confirmation_id"),
        "pending_confirmation_version": offer.get("confirmation_version"),
    }
