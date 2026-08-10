from __future__ import annotations

"""Temporary carrier-only import bootstrap for Attempt-3 Stage-1 validation.

The registered Quality job invokes the Agent virtualenv interpreter from the
repository root, while the Agent test suite assumes the service root and src
layout are importable.  This file is present only on the unmergeable carrier;
it adds those exact repository-local paths only when the carrier Stage-1
trigger exists.  It is never part of the product fileset.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TRIGGER = ROOT / ".github" / "wp08-attempt3-stage1-profile-trigger.txt"
if TRIGGER.is_file():
    agent = ROOT / "services" / "agent-service"
    for path in (agent, agent / "src"):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)
