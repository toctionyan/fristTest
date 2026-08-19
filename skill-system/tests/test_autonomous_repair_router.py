from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

import autonomous_repair_router as router  # noqa: E402


INCIDENT_LOG = r"""
======================================================================
FAIL: test_current_bounded_autonomy_chain_closes_routine_clicks (test_engineering_bounded_autonomy_closure.EngineeringBoundedAutonomyClosureTests.test_current_bounded_autonomy_chain_closes_routine_clicks)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/skill-system/tests/test_engineering_bounded_autonomy_closure.py", line 36, in test_current_bounded_autonomy_chain_closes_routine_clicks
    self.assertEqual(result["status"], "PASS", result["errors"])
AssertionError: 'FAIL' != 'PASS'

----------------------------------------------------------------------
Ran 578 tests in 20.580s
FAILED (failures=2)
"""


class AutonomousRepairRouterTests(unittest.TestCase):
    def _route(self, **overrides):
        values = {
            "workflow_name": "quality",
            "conclusion": "failure",
            "legacy_classification": "unknown_failure_without_gate_evidence",
            "combined_text": INCIDENT_LOG,
            "failed_gates": [],
            "legacy_candidate_paths": [],
            "source_changed_files": [
                "scripts/verify_engineering_bounded_autonomy_closure.py",
                "skill-system/tests/test_engineering_bounded_autonomy_closure.py",
            ],
            "same_repository": True,
        }
        values.update(overrides)
        return router.validate_route(router.route_failure(**values))

    def test_m85_like_failure_routes_to_exact_control_implementation(self) -> None:
        route = self._route()
        self.assertEqual(
            route["repair_class"],
            router.CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE,
        )
        self.assertEqual(route["repair_domain"], router.REPAIR_DOMAIN_CONTROL_PLANE)
        self.assertTrue(route["automatic_write_allowed"])
        self.assertFalse(route["human_required"])
        self.assertEqual(
            route["allowed_write_paths"],
            ["scripts/verify_engineering_bounded_autonomy_closure.py"],
        )
        self.assertFalse(route["test_write_allowed"])
        self.assertFalse(route["acceptance_write_allowed"])
        self.assertFalse(route["oracle_write_allowed"])
        self.assertFalse(route["merge_allowed"])
        self.assertFalse(route["deploy_allowed"])

    def test_test_file_is_evidence_not_write_scope(self) -> None:
        route = self._route(
            source_changed_files=["skill-system/tests/test_engineering_bounded_autonomy_closure.py"]
        )
        self.assertEqual(route["repair_class"], router.TEST_HARNESS_REPAIRABLE)
        self.assertFalse(route["automatic_write_allowed"])
        self.assertTrue(route["human_required"])
        self.assertEqual(route["allowed_write_paths"], [])
        self.assertFalse(route["test_write_allowed"])

    def test_unrelated_verifier_name_cannot_receive_control_write(self) -> None:
        route = self._route(
            source_changed_files=[
                "scripts/verify_engineering_other_contract.py",
                "skill-system/tests/test_engineering_bounded_autonomy_closure.py",
            ]
        )
        self.assertNotEqual(
            route["repair_class"],
            router.CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE,
        )
        self.assertFalse(route["automatic_write_allowed"])

    def test_changed_file_metadata_without_executable_failure_is_not_authority(self) -> None:
        route = self._route(combined_text="all tests passed")
        self.assertEqual(route["repair_class"], router.UNKNOWN)
        self.assertFalse(route["automatic_write_allowed"])

    def test_fork_failure_is_human_gate(self) -> None:
        route = self._route(same_repository=False)
        self.assertEqual(route["repair_class"], router.HUMAN_GATE)
        self.assertTrue(route["human_required"])
        self.assertFalse(route["automatic_write_allowed"])

    def test_protected_baseline_classification_cannot_be_reinterpreted(self) -> None:
        route = self._route(legacy_classification="protected_baseline_drift")
        self.assertEqual(route["repair_class"], router.AUTHORITY_ORACLE_CHANGE_REQUIRED)
        self.assertFalse(route["automatic_write_allowed"])
        self.assertTrue(route["human_required"])

    def test_existing_product_route_is_preserved(self) -> None:
        route = self._route(
            legacy_classification="code_or_contract",
            legacy_candidate_paths=["services/agent-service/src/agent_core/example.py"],
        )
        self.assertEqual(route["repair_class"], router.PRODUCT_CODE_REPAIRABLE)
        self.assertEqual(route["repair_domain"], router.REPAIR_DOMAIN_PRODUCT)
        self.assertEqual(
            route["allowed_write_paths"],
            ["services/agent-service/src/agent_core/example.py"],
        )

    def test_route_digest_tamper_fails_closed(self) -> None:
        route = self._route()
        route["allowed_write_paths"].append("scripts/verify_engineering_other.py")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            router.validate_route(route)


if __name__ == "__main__":
    unittest.main()
