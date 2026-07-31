from __future__ import annotations

"""Single anchored path contract for source identity and clean releases.

Directory *names* are never enough to classify a path as generated runtime
state. ``runtime`` is also a legitimate Python/test package name in this
workspace, so only the two service-root artifact directories are excluded.
"""

from pathlib import Path


RUNTIME_ARTIFACT_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("services", "agent-service", "runtime"),
    ("services", "business-service", "runtime"),
)
RUNTIME_ARTIFACT_LABELS = {
    "/".join(prefix) for prefix in RUNTIME_ARTIFACT_PREFIXES
}


def is_runtime_artifact_path(relative: Path) -> bool:
    """Return true only for configured service-root runtime artifacts."""
    parts = relative.parts
    return any(parts[: len(prefix)] == prefix for prefix in RUNTIME_ARTIFACT_PREFIXES)
