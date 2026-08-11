#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
path = root / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
text = path.read_text(encoding="utf-8")
old = "a true current-turn result reference"
new = "a true current-turn Goal result reference"
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected exactly one scope re-audit phrase, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
