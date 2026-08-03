#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from task_ledger_cli import main  # type: ignore


if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("validate")
    raise SystemExit(main())
