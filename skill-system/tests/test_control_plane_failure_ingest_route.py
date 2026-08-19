from __future__ import annotations

import json
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

LANDED_BASELINE_RED = """
======================================================================
FAIL: test_baseline_contract_and_accepted_snapshot_binding (test_product_source_baseline_binding.ProductSourceBaselineBindingTests.test_baseline_contract_and_accepted_snapshot_binding)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/runner/work/fristTest/fristTest/skill-system/tests/test_product_source_baseline_binding.py", line 38, in test_baseline_contract_and_accepted_snapshot_binding
    self.assertEqual(result.errors, ())
AssertionError: Tuples differ: ('current_file_count_mismatch', 'protected_baseline_drift') != ()
----------------------------------------------------------------------
Ran 617 tests in 31.818s
FAILED (failures=1)
"""


class ControlPlaneFailureIngestRouteTests(unittest.TestCase):
    def _event(
        self,
        *,
        run_id: int = 32210402217,
        event_name: str = "pull_request",
        head_branch: str = "feature/autonomous-engineering-m8-5-human-gate-closure-20260819",
        pull_request: bool = True,
    ):
        pull_requests = []
        if pull_request:
            pull_requests = [
                {
                    "number": 1917,
                    "head": {"ref": head_branch},
                    "base": {
                        "ref": "feature/autonomous-engineering-m8-4-exact-resume-wakeup-20260819"
                    },
                }
            ]
        return {
            "repository": {"full_name": "toctionyan/fristTest"},
            "workflow_run": {
                "id": run_id,
                "run_attempt": 1,
                "name": "quality",
                "conclusion": "failure",
                "event": event_name,
                "head_sha": "a" * 40,
                "head_branch": head_branch,
                "head_repository": {"full_name": "toctionyan/fristTest"},
                "html_url": f"https://github.com/toctionyan/fristTest/actions/runs/{run_id}",
                "pull_requests": pull_requests,
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
        self.assertEqual(report["recovery_disposition"], "AUTO_REPAIR")
        self.assertFalse(report["human_required"])
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

    def test_landed_main_baseline_red_keeps_semantic_authority_class(self) -> None:
        event = self._event(
            run_id=32234006120,
            event_name="push",
            head_branch="main",
            pull_request=False,
        )
        with tempfile.TemporaryDirectory() as temp:
            report = ingest.build_report(
                event,
                workspace=Path(temp),
                artifact_files=[(Path("skill-control-plane.log"), LANDED_BASELINE_RED)],
                changed_files=[],
            )

        self.assertEqual(report["classification"], "protected_baseline_drift")
        self.assertEqual(
            report["repair_route"]["repair_class"],
            "AUTHORITY_ORACLE_CHANGE_REQUIRED",
        )
        self.assertEqual(report["repair_domain"], "NONE")
        self.assertFalse(report["repair_allowed"])
        self.assertFalse(report["repair_route"]["automatic_write_allowed"])
        self.assertEqual(report["recovery_disposition"], "HUMAN_REQUIRED")
        self.assertTrue(report["human_required"])
        self.assertTrue(
            any(
                row.get("failure_kind") == "protected_baseline_drift"
                and row.get("gate_id") == "protected-product-source-baseline"
                for row in report["failed_gates"]
            )
        )

    def test_unknown_quality_failure_enters_bounded_read_only_diagnosis_not_blocked(self) -> None:
        event = self._event(
            run_id=32239990001,
            event_name="push",
            head_branch="main",
            pull_request=False,
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = ingest.build_report(
                event,
                workspace=root,
                artifact_files=[(Path("skill-control-plane.log"), "unexpected terminal RED\n")],
                changed_files=[],
            )
            task_path = root / "task-run.json"
            ingest._create_task_run(report, task_path)
            task = json.loads(task_path.read_text(encoding="utf-8"))

        self.assertEqual(report["classification"], "unknown_failure_without_gate_evidence")
        self.assertFalse(report["repair_allowed"])
        self.assertEqual(report["recovery_disposition"], "AUTO_DIAGNOSE")
        self.assertFalse(report["human_required"])
        self.assertEqual(task["status"], "WAITING_EXTERNAL_RESULT")
        self.assertEqual(task["phase"], "READ_ONLY_DIAGNOSIS_REQUIRED")
        self.assertEqual(task["checkpoints"][-1]["metadata"]["next_action"], "analyze_failure")
        self.assertFalse(task["checkpoints"][-1]["metadata"]["source_write_allowed"])
        self.assertFalse(task["checkpoints"][-1]["metadata"]["human_required"])
        self.assertEqual(task["checkpoints"][-1]["metadata"]["max_diagnosis_attempts"], 2)


if __name__ == "__main__":
    unittest.main()
