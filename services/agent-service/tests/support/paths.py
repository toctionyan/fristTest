from __future__ import annotations

from pathlib import Path


def agent_root(anchor: str | Path) -> Path:
    path = Path(anchor).resolve()
    for candidate in (path.parent, *path.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "src" / "agent_core").is_dir():
            return candidate
    raise RuntimeError(f"cannot locate Agent service root from {path}")


def workspace_root(anchor: str | Path) -> Path:
    path = Path(anchor).resolve()
    for candidate in (path.parent, *path.parents):
        if (candidate / "architecture-skill").is_dir() and (candidate / "services").is_dir():
            return candidate
    raise RuntimeError(f"cannot locate workspace root from {path}")
