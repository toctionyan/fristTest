from __future__ import annotations

"""Compatibility exports for the canonical transaction interaction focus.

New runtime code should import :mod:`agent_core.transaction.focus`.  These
names remain stable for older modules and clients while delegating all authority
to ``focused_draft_id``.
"""

from typing import Any, Mapping, MutableMapping

from agent_core.transaction.focus import (
    focused_draft_patch,
    get_focused_draft_id,
    set_focused_draft_id,
)


def get_active_draft_id(state: Mapping[str, Any]) -> str | None:
    return get_focused_draft_id(state)


def active_draft_patch(draft_id: str | None) -> dict[str, Any]:
    return focused_draft_patch(draft_id)


def set_active_draft_id(state: MutableMapping[str, Any], draft_id: str | None) -> None:
    set_focused_draft_id(state, draft_id)
