from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import github_failure_ingest_control_plane as ingest  # noqa: E402


class BaselineCountDriftIngestTests(unittest.TestCase):
    def test_count_only_baseline_failure_is_governance_drift_not_source_repair(self) -> None:
        log = """
======================================================================
FAIL: test_baseline_matches_current_git_tracked_protected_snapshot
----------------------------------------------------------------------
Traceback (most recent call last):
  File "skill-system/tests/test_product_source_baseline_binding.py", line 52, in test_baseline_matches_current_git_tracked_protected_snapshot
    self.assertEqual(int(baseline.get("file_count") or 0), len(current))
AssertionError: 621 != 624
"""
        event = {
            "repository": {"full_name": "toctionyan/fristTest"},
            "workflow_run": {
                "id": 31940266964,
                "run_attempt": 1,
                "name": "quality",
                "conclusion": "failure",
                "event": "pull_request",
                "head_sha": "a" * 40,
                "head_branch": "repair/example",
                "head_repository": {"full_name": "toctionyan/fristTest"},
                "pull_requests": [
                    {
                        "number": 1348,
                        "head": {"ref": "repair/example"},
                        "base": {"ref": "main"},
                    }
                ],
            },
        }
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            report = ingest.build_report(
                event,
                workspace=workspace,
                artifact_files=[(workspace / "skill-control-plane.log", log)],
                changed_files=["services/agent-service/src/example.py"],
            )

        self.assertEqual(report["classification"], "protected_baseline_drift")
        self.assertFalse(report["repair_allowed"])
        self.assertEqual(report["candidate_paths"], [])
        rows = [
            row
            for row in report["failed_gates"]
            if row.get("failure_kind") == "protected_baseline_drift"
        ]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["gate_id"], "protected-product-source-baseline")
        self.assertEqual(rows[0]["implicated_paths"], [])
        self.assertIn("recorded=621 current=624", rows[0]["summary"])

    def test_unrelated_assertion_count_is_not_misclassified_as_baseline_drift(self) -> None:
        log = "AssertionError: 621 != 624\n"
        rows = ingest._protected_baseline_count_failures(
            [(Path("unrelated.log"), log)]
        )
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
