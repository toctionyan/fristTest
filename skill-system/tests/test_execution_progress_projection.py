from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

import execution_progress  # noqa: E402


def _task() -> dict:
    return {
        "task_id": "issue167-pack-a",
        "status": "WAITING_EXTERNAL_RESULT",
        "phase": "CI_CERTIFICATION",
        "metadata": {
            "local_first": {
                "counters": {
                    "local_repair_rounds": 2,
                    "local_verification_rounds": 3,
                },
                "budgets": {
                    "local_repair_rounds": 8,
                    "local_verification_rounds": 8,
                },
            }
        },
    }


class ExecutionProgressProjectionTests(unittest.TestCase):
    def test_product_green_is_not_hidden_by_status_publication_failure(self) -> None:
        progress = execution_progress.build_execution_progress(
            task=_task(),
            quality_results=[
                {"id": "python-test-suites", "name": "Python tests", "status": "PASS"},
                {"id": "frontend-tests", "name": "Frontend", "status": "PASS"},
            ],
            github_jobs=[
                {
                    "id": 101,
                    "name": "quality-quick-execution",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "id": 102,
                    "name": "quality-quick-required-status",
                    "status": "completed",
                    "conclusion": "failure",
                },
            ],
            run_id=32147681365,
            workflow="quality",
            head_sha="9b651c4",
        )

        self.assertEqual(progress["schema"], "execution-progress@1")
        self.assertFalse(progress["authority_effect"])
        self.assertEqual(progress["product_verdict"], "PASS")
        self.assertEqual(progress["transport_verdict"], "FAIL")
        self.assertEqual(progress["overall"], "FAILED")
        self.assertEqual(progress["first_failure"]["label"], "quality-quick-required-status")
        self.assertEqual(
            progress["loop"],
            {
                "repair_round": 2,
                "max_repair_rounds": 8,
                "verification_round": 3,
                "max_verification_rounds": 8,
            },
        )

    def test_running_step_is_current_stage_and_missing_evidence_is_never_pass(self) -> None:
        progress = execution_progress.build_execution_progress(
            github_jobs=[
                {
                    "id": 201,
                    "name": "quality-quick-execution",
                    "status": "in_progress",
                    "conclusion": None,
                }
            ],
            github_steps=[
                {
                    "number": 8,
                    "name": "Create quick quality target",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "number": 9,
                    "name": "Run unit, contract and frontend gates",
                    "status": "in_progress",
                    "conclusion": None,
                },
                {
                    "number": 10,
                    "name": "Upload quick evidence",
                    "status": "queued",
                    "conclusion": None,
                },
            ],
            github_step_job_name="quality-quick-execution",
        )

        self.assertEqual(progress["overall"], "RUNNING")
        self.assertEqual(progress["transport_verdict"], "RUNNING")
        self.assertEqual(progress["product_verdict"], "UNKNOWN")
        self.assertIn("quality-quick-execution", progress["current_stage"])
        statuses = {row["label"]: row["status"] for row in progress["stages"]}
        self.assertEqual(statuses["Run unit, contract and frontend gates"], "RUNNING")
        self.assertEqual(statuses["Upload quick evidence"], "PENDING")

    def test_failed_quality_gate_is_the_product_failure_authority(self) -> None:
        progress = execution_progress.build_execution_progress(
            quality_results=[
                {"id": "static", "name": "Static", "status": "PASS"},
                {
                    "id": "authority",
                    "name": "Transaction Authority",
                    "status": "FAIL",
                    "stderr": "wrong-thread replay accepted",
                },
            ],
            github_jobs=[
                {
                    "id": 301,
                    "name": "quality-quick-execution",
                    "status": "completed",
                    "conclusion": "failure",
                }
            ],
        )

        self.assertEqual(progress["product_verdict"], "FAIL")
        self.assertEqual(progress["transport_verdict"], "FAIL")
        self.assertEqual(progress["first_failure"]["label"], "Transaction Authority")
        self.assertEqual(progress["first_failure"]["source"], "quality-gate")
        rendered = execution_progress.render_progress_text(progress)
        self.assertIn("✅ Static", rendered)
        self.assertIn("❌ Transaction Authority", rendered)
        self.assertIn("产品判定：FAIL", rendered)
        self.assertIn("执行/传输判定：FAIL", rendered)

    def test_skipped_only_evidence_is_not_promoted_to_pass(self) -> None:
        progress = execution_progress.build_execution_progress(
            quality_results=[
                {"id": "integration", "name": "Integration", "status": "SKIPPED"},
            ],
            github_jobs=[
                {
                    "id": 401,
                    "name": "quality-integration",
                    "status": "completed",
                    "conclusion": "skipped",
                }
            ],
        )

        self.assertEqual(progress["stages"][0]["status"], "SKIPPED")
        self.assertEqual(progress["product_verdict"], "NOT_RUN")
        self.assertEqual(progress["transport_verdict"], "NOT_RUN")
        self.assertEqual(progress["overall"], "SKIPPED")
        rendered = execution_progress.render_progress_text(progress)
        self.assertIn("⏭️ Integration", rendered)
        self.assertIn("⏭️ quality-integration", rendered)
        self.assertIn("产品判定：NOT_RUN", rendered)

    def test_pass_plus_skipped_remains_valid_success(self) -> None:
        progress = execution_progress.build_execution_progress(
            quality_results=[
                {"id": "quick", "name": "Quick", "status": "PASS"},
                {"id": "integration", "name": "Integration", "status": "SKIPPED"},
            ]
        )
        self.assertEqual(progress["product_verdict"], "PASS")
        self.assertEqual(progress["overall"], "COMPLETED")

    def test_projection_is_read_only_and_does_not_claim_completion_from_task_state(self) -> None:
        task = {
            "task_id": "t1",
            "status": "COMPLETED",
            "phase": "COMPLETED",
            "metadata": {},
        }
        progress = execution_progress.build_execution_progress(task=task)

        self.assertEqual(progress["task"]["status"], "COMPLETED")
        self.assertEqual(progress["overall"], "UNKNOWN")
        self.assertEqual(progress["product_verdict"], "UNKNOWN")
        self.assertEqual(progress["transport_verdict"], "UNKNOWN")
        self.assertFalse(progress["authority_effect"])


if __name__ == "__main__":
    unittest.main()
