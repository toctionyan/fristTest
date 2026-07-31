from __future__ import annotations

"""Customer-safe projection for LangGraph ``stream_mode='updates'`` deltas."""

from typing import Any, Callable


class SseStreamAdapter:
    def __init__(self, public_state_projector: Callable[[dict[str, Any]], dict[str, Any] | None]):
        self._project = public_state_projector

    def project_public_update(self, update: Any) -> dict[str, Any] | None:
        """Unwrap ``{node_name: delta}`` and preserve the public event shape.

        The browser receives only public fields plus ``node``.  It never sees
        raw graph state or assumes an intermediate delta is the final result.
        """
        if not isinstance(update, dict):
            return None
        for node, delta in update.items():
            if not isinstance(delta, dict):
                continue
            projected = self._project(delta)
            if not projected:
                continue
            return {"node": str(node), **projected}
        return None
