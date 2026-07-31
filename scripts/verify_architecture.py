#!/usr/bin/env python3
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "architecture-skill" / "scripts" / "verify_convergence.py"
sys.argv = [str(SCRIPT), "--workspace-root", str(ROOT)]
runpy.run_path(str(SCRIPT), run_name="__main__")
