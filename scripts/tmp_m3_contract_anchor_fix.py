#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "scripts" / "wp08_release_coordinator_contract.py"
text = path.read_text(encoding="utf-8")
old = '        \'payload={"ref": MAIN_BRANCH}\',\n'
new = (
    '        \'dispatch_payload: dict[str, Any] = {"ref": MAIN_BRANCH}\',\n'
    '        "payload=dispatch_payload",\n'
)
count = text.count(old)
if count != 1:
    raise SystemExit(f"current dispatch contract anchor: expected 1, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("M3 static dispatch contract migrated to current adapter structure")
