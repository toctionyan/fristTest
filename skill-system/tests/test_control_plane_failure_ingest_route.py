from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
CONTROL = ROOT / "skill-system" / "controller"
for entry in (str(SCRIPTS), str(CONTROL)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import github_failure_ingest_control_plane as ingest  # noqa: E402


CONTROL_PATH = "scripts/verify_engineering_bounded_autonomy_closure.py"
TEST_PATH = "skill-system/tests/test_engineering_bounded_autonomy_closure.py"
INCIDENT_LOG = """
......................................................................................................................F..F
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


class ControlPlaneFailureIngestRouteTests(unittest.TestCase):
    def _event(self):
        return {
            "repository": {"full_name": "toctionyan/fristTest"},
            "workflow_run": {
                "id": 32210402217,
                "run_attempt": 1,
                "name": "quality",
                "conclusion": "failure",
                "event": "pull_request",
                "head_sha": "a" * 40,
                "head_branch": "feature/autonomous-engineering-m8-5-human-gate-closure-20260819",
                "head_repository": {"full_name": "toctionyan/fristTest"},
                "html_url": "https://github.com/toctionyan/fristTest/actions/runs/32210402217",
                "pull_requests": [
                    {
                        "number": 1917,
                        "head": {
                            "ref": "feature/autonomous-engineering-m8-5-human-gate-closure-20260819"
                        },
                        "base": {
                            "ref": "feature/autonomous-engineering-m8-4-exact-resume-wakeup-20260819"
                        },
                    }
                ],
            },
        }

    def test_m85_incident_becomes_control_plane_repairable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = ingest.build_report(
                self._event(),
                workspace=Path(temp),
                artifact_files=[(Path("skill-control-plane.log"), INCIDENT_LOG)],
                changed_files=[CONTROL_PATH, TEST_PATH],
            )
        self.assertEqual(report["classification"], "control_plane_implementation")
        self.assertEqual(report["repair_domain"], "CONTROL_PLANE_IMPLEMENTATION")
        self.assertTrue(report["repair_allowed"])
        self.assertEqual(report["candidate_paths"], [CONTROL_PATH])
        self.assertEqual(report["repair_route"]["allowed_write_paths"], [CONTROL_PATH])
        self.assertFalse(report["repair_route"]["test_write_allowed"])
        self.assertIn(TEST_PATH, report["source_changed_files"])
        self.assertNotIn(TEST_PATH, report["candidate_paths"])
        self.assertTrue(
            any(
                row.get("gate_id") == "skill-control-plane"
                and row.get("failure_kind") == "control_plane_implementation"
                for row in report["failed_gates"]
            )
        )
        binding = ingest._task_immutable_binding(report)
        self.assertEqual(binding["repair_domain"], "CONTROL_PLANE_IMPLEMENTATION")
        self.assertEqual(
            binding["repair_route_sha256"], report["repair_route"]["route_sha256"]
        )

    def test_without_exact_implementation_pairing_report_stays_nonrepairable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = ingest.build_report(
                self._event(),
                workspace=Path(temp),
                artifact_files=[(Path("skill-control-plane.log"), INCIDENT_LOG)],
                changed_files=[TEST_PATH],
            )
        self.assertFalse(report["repair_allowed"])
        self.assertNotEqual(report["classification"], "control_plane_implementation")
        self.assertEqual(report["candidate_paths"], [])
        self.assertFalse(report["repair_route"]["automatic_write_allowed"])


if __name__ == "__main__":
    unittest.main()
