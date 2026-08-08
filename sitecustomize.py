"""Temporary WP-08 repair-carrier import-root shim.

This file exists only on the governed repair branch so repository-root pytest
invocations resolve the Agent service's ``tests`` package exactly as the normal
Agent test working directory does. It is removed together with the temporary
carrier before the repair PR is merged.
"""
from __future__ import annotations

from pathlib import Path
import sys

_AGENT_ROOT = Path(__file__).resolve().parent / "services" / "agent-service"
if _AGENT_ROOT.is_dir() and str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))
