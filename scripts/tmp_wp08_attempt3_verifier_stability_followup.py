#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
path = root / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
text = path.read_text(encoding="utf-8")
old = "A historical reference belongs in reference_expression; a true current-turn result reference belongs only in the already preserved dependency proof."
new = "A historical reference belongs in reference_expression; a true current-turn Goal result reference belongs only in the already preserved dependency proof."
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected exactly one scope re-audit wording anchor, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
