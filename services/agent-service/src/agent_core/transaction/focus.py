from __future__ import annotations

"""Transaction interaction focus separated from durable transaction state.

A conversation may own multiple durable, non-terminal drafts.  Only one draft
may be the current UI interaction focus.  ``focused_draft_id`` is the canonical
runtime pointer; ``active_draft_id`` is emitted only as a compatibility
projection for older clients/checkpoints.
"""

from typing import Any, Mapping, MutableMapping

from agent_core.storage.repositories.base import TransactionScope

OPEN_DRAFT_STATES = frozenset(
    {
        "NEEDS_INPUT",
        "READY",
        "AWAITING_AUTHORIZATION",
        "AUTHORIZED",
        "COMMITTING",
        "SUBMISSION_UNKNOWN",
        "RECONCILIATION_REQUIRED",
        "FAILED_RETRYABLE",
    }
)


def _normalized(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def get_focused_draft_id(state: Mapping[str, Any]) -> str | None:
    """Return the canonical interaction focus.

    Presence of ``focused_draft_id`` is authoritative even when its value is
    null.  This prevents a stale legacy ``active_draft_id`` from resurrecting a
    cleared interaction.  Older checkpoints that lack the new key are read once
    through the compatibility projection.
    """

    if "focused_draft_id" in state:
        return _normalized(state.get("focused_draft_id"))
    return _normalized(state.get("active_draft_id"))


def focused_draft_patch(draft_id: str | None) -> dict[str, Any]:
    value = _normalized(draft_id)
    return {
        "focused_draft_id": value,
        # Output-only compatibility projection.  Runtime readers use the
        # canonical focused_draft_id when present.
        "active_draft_id": value,
    }


def set_focused_draft_id(state: MutableMapping[str, Any], draft_id: str | None) -> None:
    state.update(focused_draft_patch(draft_id))


def list_open_drafts(store: Any, *, scope: TransactionScope, limit: int = 50) -> list[dict[str, Any]]:
    """Return durable non-terminal drafts for one tenant/user/thread scope."""

    return [
        dict(row)
        for row in store.list_drafts_for_scope(
            scope=scope,
            states=set(OPEN_DRAFT_STATES),
            limit=limit,
        )
    ]


def next_focus_after_terminal(
    store: Any,
    *,
    scope: TransactionScope,
    terminal_draft_id: str,
) -> str | None:
    """Select another focus only when the choice is unambiguous.

    Never guess between multiple open drafts.  The UI/lifecycle query must ask
    the user to choose instead.
    """

    candidates = [
        row
        for row in list_open_drafts(store, scope=scope)
        if str(row.get("draft_id") or "") != str(terminal_draft_id or "")
    ]
    if len(candidates) != 1:
        return None
    return _normalized(candidates[0].get("draft_id"))
