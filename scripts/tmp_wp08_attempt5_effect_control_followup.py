#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: followup.py WORKSPACE")
    root = Path(sys.argv[1]).resolve()
    path = root / "skill-system/tests/test_wp08_attempt4_followup_repair.py"
    text = path.read_text(encoding="utf-8")
    old = '''    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_edges"][0]["basis_span"] == "它"
'''
    new = '''    adversarial = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }],
        "reason_code": "adversarial_literal_result_reference_confirmed",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind, adversarial]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_edges"][0]["basis_span"] == "它"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_positive_edge_adjudication"
'''
    if text.count(old) != 1:
        raise SystemExit(f"stale positive-dependency assertion anchor count={text.count(old)}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
