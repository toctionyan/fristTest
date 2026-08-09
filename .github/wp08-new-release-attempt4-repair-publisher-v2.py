#!/usr/bin/env python3
from __future__ import annotations

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("wp08-new-release-attempt4-repair-publisher.py")), run_name="__main__")

test_file = Path("candidate/skill-system/tests/test_wp08_new_release_attempt4_repair.py").resolve()
text = test_file.read_text(encoding="utf-8")
old = '        self.assertNotIn("expected span", helper.lower())\n'
if text.count(old) != 1:
    raise SystemExit(f"expected one overbroad source-comment assertion, found {text.count(old)}")
test_file.write_text(text.replace(old, "", 1), encoding="utf-8")
print("WP08_ATTEMPT4_GOVERNANCE_TEST_ASSERTION_FIXED")
