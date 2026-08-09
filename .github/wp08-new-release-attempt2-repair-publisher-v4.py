#!/usr/bin/env python3
from __future__ import annotations

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("wp08-new-release-attempt2-repair-publisher-v3.py")), run_name="__main__")

smoke = Path("candidate/services/agent-service/scripts/verify_preprod_conversation_smoke.py").resolve()
text = smoke.read_text(encoding="utf-8")
old = '''            candidate_effect = _effect_identity(row.get("requested_effect"))
            if match_mode == "unregistered_open":
                effect_ok = bool(all(candidate_effect)) and _effect_key(candidate_effect) not in registered_effect_identities
            else:
                effect_ok = candidate_effect == expected_effect
'''
new = '''            candidate_effect = _effect_identity(row.get("requested_effect"))
            if match_mode == "unregistered_open":
                effect_ok = bool(all(candidate_effect)) and _effect_key(candidate_effect) not in registered_effect_identities
            else:
                effect_ok = _effect_identity(row.get("requested_effect")) == expected_effect
'''
if text.count(old) != 1:
    raise SystemExit(f"expected exact-effect branch once, found {text.count(old)}")
smoke.write_text(text.replace(old, new, 1), encoding="utf-8")
print("WP08_ATTEMPT2_EXACT_EFFECT_GOVERNANCE_SENTINEL_PRESERVED")
