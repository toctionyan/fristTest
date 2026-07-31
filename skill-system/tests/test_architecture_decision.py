from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from change_contract_cli import _validate_architecture_inputs  # type: ignore


class ArchitectureDecisionTest(unittest.TestCase):
    def decision(self, strategies: list[str]) -> dict:
        return {
            "schema_version": 1,
            "change_id": "design-1",
            "problem": "The current project baseline locks a reference implementation into the universal gate.",
            "options": [
                {
                    "id": value,
                    "strategy": value,
                    "name": value,
                    "correctness": "compare behavior and authority boundaries",
                    "complexity": "bounded",
                    "migration": "explicit",
                    "rollback": "restore baseline",
                    "verification": "counterexamples and architecture fitness",
                }
                for value in strategies
            ],
            "selected_option": "evolutionary",
            "rejected_reasons": {},
            "deletions": [],
            "unknowns": [],
            "selected_responsibilities": ["validate behavior without fixing class names"],
            "preserved_hard_invariants": ["business authority remains external"],
            "cutover_strategy": "Run the new path read-only, cut over one formal owner, then delete the old path.",
            "acceptance_claims": ["the gate accepts an approved delta and rejects an unbound delta"],
            "baseline_changes": [],
        }

    def test_design_requires_three_strategy_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "decision.json"
            path.write_text(json.dumps(self.decision(["conservative", "evolutionary", "redesign"])), encoding="utf-8")
            _validate_architecture_inputs(root, {
                "change_id": "design-1",
                "target_kind": "design",
                "decision_record": "decision.json",
                "variance_records": [],
                "architecture_policy_delta": None,
            })

    def test_design_rejects_missing_redesign_option(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "decision.json"
            path.write_text(json.dumps(self.decision(["conservative", "evolutionary"])), encoding="utf-8")
            with self.assertRaises(SystemExit):
                _validate_architecture_inputs(root, {
                    "change_id": "design-1",
                    "target_kind": "design",
                    "decision_record": "decision.json",
                    "variance_records": [],
                    "architecture_policy_delta": None,
                })


if __name__ == "__main__":
    unittest.main()
