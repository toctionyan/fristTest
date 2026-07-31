from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "architecture-skill" / "scripts" / "verify_convergence.py"


def _module():
    spec = importlib.util.spec_from_file_location("architecture_cycle_debt_verifier", VERIFIER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load architecture convergence verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ArchitectureCycleDebtTests(unittest.TestCase):
    def setUp(self) -> None:
        self.verifier = _module()
        self.policy = {
            "mode": "ratchet",
            "baseline_components": [
                {
                    "id": "core-cycle",
                    "members": ["context", "lifecycle", "runtime"],
                    "owner": "architecture",
                    "target": "remove the cycle",
                    "review_after": "2026-10-31",
                }
            ],
        }

    def test_unchanged_cycle_is_visible_debt_not_false_clean(self) -> None:
        report = self.verifier._assess_dependency_cycle_debt(
            [["context", "lifecycle", "runtime"]], self.policy
        )
        self.assertEqual(report["status"], "UNCHANGED")
        self.assertEqual(report["matches"][0]["classification"], "UNCHANGED")
        self.assertEqual(report["untracked_or_expanded_cycles"], [])

    def test_cycle_shrink_is_accepted_as_measurable_reduction(self) -> None:
        report = self.verifier._assess_dependency_cycle_debt(
            [["context", "lifecycle"]], self.policy
        )
        self.assertEqual(report["status"], "REDUCED")
        self.assertEqual(report["matches"][0]["removed_members"], ["runtime"])
        self.assertEqual(report["member_delta"], -1)

    def test_new_cycle_is_rejected(self) -> None:
        report = self.verifier._assess_dependency_cycle_debt(
            [["storage", "transaction"]], self.policy
        )
        self.assertEqual(report["status"], "VIOLATION")
        self.assertEqual(
            report["untracked_or_expanded_cycles"], [["storage", "transaction"]]
        )

    def test_expanded_cycle_is_rejected(self) -> None:
        report = self.verifier._assess_dependency_cycle_debt(
            [["context", "lifecycle", "runtime", "storage"]], self.policy
        )
        self.assertEqual(report["status"], "VIOLATION")

    def test_removed_cycle_resolves_debt(self) -> None:
        report = self.verifier._assess_dependency_cycle_debt([], self.policy)
        self.assertEqual(report["status"], "RESOLVED")
        self.assertEqual(report["resolved_components"][0]["id"], "core-cycle")

    def test_current_workspace_reports_resolved_architecture_debt(self) -> None:
        policy = json.loads(
            (ROOT / "governance" / "architecture-policy.json").read_text(encoding="utf-8")
        )
        policy["enforce_clean_artifacts"] = False
        report = self.verifier.verify(ROOT, policy)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["architecture_status"], "PASS")
        self.assertEqual(report["architecture_debt_status"], "RESOLVED")
        debt = report["checks"]["dependency_cycle_debt"]
        self.assertEqual(debt["status"], "RESOLVED")
        self.assertEqual(debt["current_member_count"], 0)
        self.assertEqual(debt["current_cycles"], [])


if __name__ == "__main__":
    unittest.main()
