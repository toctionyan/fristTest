#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

new_test = root / "skill-system/tests/test_wp08_attempt4_dependency_reference_repair.py"
text = new_test.read_text(encoding="utf-8")
old = '    assert "Runtime does" in repair\n    assert "rewrite the graph" in repair\n'
new = '    assert "Start every unordered Goal pair from independent" in repair\n    assert "rewrite the graph" in repair\n'
if text.count(old) != 1:
    raise SystemExit(f"expected one new-test assertion anchor, found {text.count(old)}")
new_test.write_text(text.replace(old, new, 1), encoding="utf-8")

old_test = root / "services/agent-service/tests/runtime/test_wp08_attempt7_final_authority_and_retry.py"
text = old_test.read_text(encoding="utf-8")
old = '    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"\n'
# This file contains three assertions for this value. Only the true-positive test
# has already been widened to three model calls by the applier; replace the one
# following its basis assertion so other graph-mismatch regressions retain their
# original expected stage.
anchor = '    assert verdict.details["dependency_edges"][0]["basis_span"] == "它"\n' + old
replacement = '    assert verdict.details["dependency_edges"][0]["basis_span"] == "它"\n    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_positive_edge_adjudication"\n'
if text.count(anchor) != 1:
    raise SystemExit(f"expected one true-dependency repair-kind anchor, found {text.count(anchor)}")
old_test.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")
