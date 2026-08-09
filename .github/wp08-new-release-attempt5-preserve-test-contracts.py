#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path("candidate").resolve()
existing_rel = "skill-system/tests/test_wp08_new_release_attempt5_repair.py"
new_rel = "skill-system/tests/test_wp08_new_release_attempt5_semantic_boundary.py"
existing = ROOT / existing_rel
new = ROOT / new_rel

semantic_counterexamples = existing.read_text(encoding="utf-8")
if "class Attempt5RepairTests" not in semantic_counterexamples:
    raise SystemExit("expected semantic-boundary counterexamples from repair builder")
if "test_blind_inventory_self_audits_false_extra_outcome_without_candidate_disclosure" not in semantic_counterexamples:
    raise SystemExit("expected blind self-audit counterexample")

base_contracts = subprocess.check_output(
    ["git", "show", f"HEAD:{existing_rel}"],
    cwd=ROOT,
).decode("utf-8")
if "class Attempt5EffectGuidanceRepairTests" not in base_contracts:
    raise SystemExit("expected pre-existing attempt-5 effect-guidance regressions")
if "test_failed_attempt5_oracle_remains_exact_collection_discovery" not in base_contracts:
    raise SystemExit("expected pre-existing attempt-5 oracle regression")

existing.write_text(base_contracts, encoding="utf-8")
new.write_text(semantic_counterexamples, encoding="utf-8")
print(json.dumps({
    "status": "PRESERVED",
    "restored": existing_rel,
    "added": new_rel,
    "reason": "keep all prior attempt-5 effect-guidance/oracle assertions while adding semantic-boundary counterexamples in a separate test module",
}, ensure_ascii=False, indent=2))
