from __future__ import annotations

"""Authority-free helpers for appending deterministic decision trace rows."""

from typing import Any


def append_decision(
    state: dict[str, Any],
    *,
    stage: str,
    decision: str,
    details: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return a new decision chain without mutating lifecycle state."""
    return [
        *(state.get("decision_chain") or []),
        {"stage": stage, "decision": decision, "details": dict(details)},
    ]


__all__ = ["append_decision"]
