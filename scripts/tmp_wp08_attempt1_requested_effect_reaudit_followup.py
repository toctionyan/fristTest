#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import py_compile
import sys


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: tmp_wp08_attempt1_requested_effect_reaudit_followup.py <source-root>")
    root = Path(sys.argv[1]).resolve()

    new_test = root / "skill-system/tests/test_wp08_attempt1_requested_effect_reaudit.py"
    text = new_test.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '    assert "capability availability must not be used as evidence" in policy\n',
        '    assert "capability availability must not be used as " in policy\n',
        label="static source string-literal assertion",
    )
    new_test.write_text(text, encoding="utf-8")

    final_test = root / "skill-system/tests/test_wp08_final_product_closure_repair.py"
    text = final_test.read_text(encoding="utf-8")
    old = '''    def test_non_scope_semantic_mismatch_does_not_gain_extra_reaudit(self) -> None:
        from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

        text = "查一下键盘订单"
        goals = [
            _goal(
                "g1",
                text,
                depends_on=[],
                effect={"domain": "order", "operation": "list", "object_type": "order"},
            )
        ]
        first = _response({
            "verdict": "exact",
            "evidence_spans": [text],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "outcome_preserved",
        })
        effect_mismatch = _response({
            "verdict": "incomplete",
            "evidence_spans": [text],
            "missing_spans": [text],
            "dependency_decisions": [],
            "reason_code": "requested-effect fidelity",
        })
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=[first, effect_mismatch]
        ) as invoke:
            verdict = ModelGoalAlignmentVerifier().verify(
                user_text=text,
                goals=goals,
                known_tools=set(),
            )

        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(verdict.verdict, "incomplete")
        self.assertNotEqual(
            verdict.details.get("verifier_repair_kind"),
            "candidate_blind_dependency_scope_constraint_reaudit",
        )
'''
    new = '''    def test_requested_effect_mismatch_gets_bounded_reaudit_and_stays_fail_closed(self) -> None:
        from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

        text = "查一下键盘订单"
        goals = [
            _goal(
                "g1",
                text,
                depends_on=[],
                effect={"domain": "order", "operation": "list", "object_type": "order"},
            )
        ]
        first = _response({
            "verdict": "exact",
            "evidence_spans": [text],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "outcome_preserved",
        })
        effect_mismatch = _response({
            "verdict": "incomplete",
            "evidence_spans": [text],
            "missing_spans": [text],
            "dependency_decisions": [],
            "reason_code": "requested-effect fidelity",
        })
        confirmed_mismatch = _response({
            "verdict": "incomplete",
            "evidence_spans": [text],
            "missing_spans": [text],
            "dependency_decisions": [],
            "reason_code": "requested_effect_fidelity",
        })
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=[first, effect_mismatch, confirmed_mismatch]
        ) as invoke:
            verdict = ModelGoalAlignmentVerifier().verify(
                user_text=text,
                goals=goals,
                known_tools=set(),
            )

        self.assertEqual(invoke.call_count, 3)
        self.assertEqual(verdict.verdict, "incomplete")
        self.assertEqual(
            verdict.details.get("verifier_repair_kind"),
            "candidate_blind_dependency_requested_effect_reaudit",
        )
        self.assertNotEqual(
            verdict.details.get("verifier_repair_kind"),
            "candidate_blind_dependency_scope_constraint_reaudit",
        )
'''
    text = replace_once(text, old, new, label="preserved final-closure requested-effect counterexample")
    final_test.write_text(text, encoding="utf-8")

    py_compile.compile(str(new_test), doraise=True)
    py_compile.compile(str(final_test), doraise=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
