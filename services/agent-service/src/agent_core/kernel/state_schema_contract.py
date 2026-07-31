from __future__ import annotations

"""Closed State Schema version values and a read-only compatibility predicate."""

from typing import Any

CURRENT_STATE_SCHEMA_VERSION = 2
LEGACY_STATE_SCHEMA_VERSION = 1


def legacy_fallback_allowed(state: dict[str, Any]) -> bool:
    return int(state.get("state_schema_version") or LEGACY_STATE_SCHEMA_VERSION) < CURRENT_STATE_SCHEMA_VERSION


__all__ = [
    "CURRENT_STATE_SCHEMA_VERSION",
    "LEGACY_STATE_SCHEMA_VERSION",
    "legacy_fallback_allowed",
]
