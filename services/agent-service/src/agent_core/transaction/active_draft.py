from __future__ import annotations

"""Canonical Graph interaction pointer helpers."""

from typing import Any, Mapping, MutableMapping


def get_active_draft_id(state: Mapping[str, Any]) -> str | None:
    """Read the only runtime interaction pointer."""
    text = str(state.get("active_draft_id") or "").strip()
    return text or None


def active_draft_patch(draft_id: str | None) -> dict[str, Any]:
    value = str(draft_id or "").strip() or None
    return {"active_draft_id": value}


def set_active_draft_id(state: MutableMapping[str, Any], draft_id: str | None) -> None:
    state.update(active_draft_patch(draft_id))
