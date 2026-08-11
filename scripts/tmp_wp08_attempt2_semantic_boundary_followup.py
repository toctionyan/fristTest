#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
path = root / "skill-system/tests/test_wp08_attempt2_semantic_boundary_repair.py"
text = path.read_text(encoding="utf-8")
old = '    assert "capability_registry" not in guard\n'
new = '    assert "CapabilityRegistry" not in guard\n    assert "capability_registry." not in guard\n'
if text.count(old) != 1:
    raise SystemExit(f"expected one test assertion anchor, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
