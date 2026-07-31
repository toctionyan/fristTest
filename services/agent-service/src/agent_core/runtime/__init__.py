"""Runtime package public API.

Keep package import lazy.  Outcome and transaction modules may import each
other during ledger/context construction; eagerly importing graph dependencies
here would create an artificial ContextBundle ↔ Runtime import cycle.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .deps import LifecycleRuntimeDeps

__all__ = ["LifecycleRuntimeDeps", "lifecycle_runtime_deps"]


def __getattr__(name: str) -> Any:
    if name in {"LifecycleRuntimeDeps", "lifecycle_runtime_deps"}:
        from .deps import LifecycleRuntimeDeps, lifecycle_runtime_deps

        return {
            "LifecycleRuntimeDeps": LifecycleRuntimeDeps,
            "lifecycle_runtime_deps": lifecycle_runtime_deps,
        }[name]
    raise AttributeError(name)
