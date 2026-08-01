from __future__ import annotations

"""Resolve locked project Python entrypoints without dereferencing virtualenv links."""

import os
import sys
from pathlib import Path
from typing import Mapping

_PROJECTS = {
    "agent": "services/agent-service",
    "business": "services/business-service",
}


def locked_project_python(
    workspace: Path,
    project: str = "agent",
    *,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return an executable project interpreter while preserving the venv entrypoint."""

    if project not in _PROJECTS:
        raise ValueError(f"unknown locked Python project: {project}")
    source = os.environ if env is None else env
    project_env = "QUALITY_AGENT_PYTHON" if project == "agent" else "QUALITY_BUSINESS_PYTHON"
    candidates = [
        str(source.get(project_env) or "").strip(),
        str(source.get("QUALITY_PYTHON_EXECUTABLE") or "").strip() if project == "agent" else "",
        str(Path(workspace) / _PROJECTS[project] / ".venv" / "bin" / "python"),
        str(sys.executable),
    ]
    checked: list[str] = []
    for raw in candidates:
        if not raw:
            continue
        candidate = Path(raw).expanduser().absolute()
        checked.append(str(candidate))
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError(
        f"no executable locked {project} Python interpreter; checked: " + ", ".join(checked)
    )
