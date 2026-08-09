#!/usr/bin/env python3
from __future__ import annotations

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("wp08-new-release-attempt2-repair-publisher-v2.py")), run_name="__main__")

smoke = Path("candidate/services/agent-service/scripts/verify_preprod_conversation_smoke.py").resolve()
text = smoke.read_text(encoding="utf-8")
old = '''def _match_oracle(
    *,
    case_id: str,
    oracle: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    registered_effect_identities: set[str],
) -> None:
'''
new = '''def _match_oracle(
    *,
    case_id: str,
    oracle: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    registered_effect_identities: set[str] | None = None,
) -> None:
    registered_effect_identities = registered_effect_identities or set()
'''
if text.count(old) != 1:
    raise SystemExit(f"expected generated matcher signature once, found {text.count(old)}")
smoke.write_text(text.replace(old, new, 1), encoding="utf-8")
print("WP08_ATTEMPT2_MATCHER_COMPATIBILITY_APPLIED")
