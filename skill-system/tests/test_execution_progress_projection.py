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

    def test_required_task_conditions_prevent_premature_whole_task_completion(self) -> None:
        task = {
            "task_id": "long-task",
            "status": "RUNNING",
            "phase": "POST_MERGE",
            "required_conditions": ["pr_ci", "main_ci", "landed_acceptance"],
            "conditions": {
                "pr_ci": {"satisfied": True, "evidence_refs": ["pr:green"]},
                "main_ci": {"satisfied": True, "evidence_refs": ["main:green"]},
                "landed_acceptance": {"satisfied": False, "evidence_refs": []},
            },
            "metadata": {},
        }
        progress = execution_progress.build_execution_progress(task=task)

        self.assertEqual(progress["overall"], "PENDING")
        self.assertFalse(progress["completion_eligible"])
        self.assertEqual(progress["missing_completion_conditions"], ["landed_acceptance"])
        self.assertEqual(progress["summary"]["completed_steps"], 2)
        self.assertEqual(progress["summary"]["total_steps"], 3)
        rendered = execution_progress.render_progress_text(progress)
        self.assertIn("整体进度：2/3", rendered)
        self.assertIn("⬜ landed_acceptance", rendered)

    def test_failed_attempt_remains_visible_after_later_success(self) -> None:
        progress = execution_progress.build_execution_progress(
            planned_stages=[
                {"id": "main-push-ci", "label": "main push CI", "status": "PASS"},
            ],
            attempt_history=[
                {
                    "sequence": 1,
                    "stage_id": "main-push-ci",
                    "label": "main push CI",
                    "attempt": 1,
                    "status": "FAIL",
                    "detail": "protected baseline drift",
                    "evidence_ref": "run:1",
                },
                {
                    "sequence": 2,
                    "stage_id": "main-push-ci",
                    "label": "main push CI",
                    "attempt": 2,
                    "status": "PASS",
                    "evidence_ref": "run:2",
                },
            ],
        )

        self.assertEqual(progress["overall"], "COMPLETED")
        self.assertEqual(progress["summary"]["recovered_failure_count"], 1)
        self.assertEqual(progress["summary"]["unresolved_failure_count"], 0)
        self.assertEqual(progress["recovered_failures"][0]["failed_attempts"], [1])
        rendered = execution_progress.render_progress_text(progress)
        self.assertIn("已自动恢复失败：1", rendered)
        self.assertIn("失败尝试 1，后续已恢复", rendered)

    def _recoverable_task(self, *, with_execution: bool) -> dict:
        metadata = {
            "engineering_reconciler": {
                "schema": "engineering-task-reconciler@1",
                "last_delivery_key": "1:1:abc",
                "decisions": {
                    "1:1:abc": {
                        "outcome": {
                            "decision": "RETRY_CI",
                            "action": "retry_transient_ci",
                            "allowed": True,
                            "human_required": False,
                        }
                    }
                },
            }
        }
        if with_execution:
            metadata["recovery_execution"] = {
                "schema": "task-recovery-execution@1",
                "status": "RUNNING",
                "action": "retry_transient_ci",
                "evidence_ref": "github-run:9001",
            }
        return {
            "task_id": "auto-recover",
            "status": "FAILED_RECOVERABLE",
            "phase": "CI_RED",
            "metadata": metadata,
        }

    def test_authorized_recovery_without_executor_evidence_is_ready_not_running(self) -> None:
        progress = execution_progress.build_execution_progress(
            task=self._recoverable_task(with_execution=False),
            planned_stages=[{"id": "ci", "label": "CI", "status": "FAIL"}],
            attempt_history=[
                {"stage_id": "ci", "label": "CI", "attempt": 1, "status": "FAIL"}
            ],
        )

        self.assertEqual(progress["overall"], "RECOVERY_READY")
        self.assertTrue(progress["recovery"]["authorized"])
        self.assertTrue(progress["recovery"]["ready"])
        self.assertFalse(progress["recovery"]["active"])
        self.assertFalse(progress["human"]["required"])
        rendered = execution_progress.render_progress_text(progress)
        self.assertIn("自动恢复：已授权/待执行", rendered)
        self.assertIn("当前没有运行中执行器证据", rendered)
        self.assertNotIn("自动恢复：正在执行", rendered)

    def test_recovery_is_running_only_with_durable_executor_evidence(self) -> None:
        progress = execution_progress.build_execution_progress(
            task=self._recoverable_task(with_execution=True),
            planned_stages=[{"id": "ci", "label": "CI", "status": "FAIL"}],
            attempt_history=[
                {"stage_id": "ci", "label": "CI", "attempt": 1, "status": "FAIL"}
            ],
        )

        self.assertEqual(progress["overall"], "RECOVERING")
        self.assertTrue(progress["recovery"]["active"])
        self.assertFalse(progress["recovery"]["ready"])
        self.assertFalse(progress["human"]["required"])
        rendered = execution_progress.render_progress_text(progress)
        self.assertIn("自动恢复：正在执行", rendered)
        self.assertIn("github-run:9001", rendered)

    def test_running_recovery_without_executor_evidence_fails_closed(self) -> None:
        task = self._recoverable_task(with_execution=False)
        task["metadata"]["recovery_execution"] = {
            "schema": "task-recovery-execution@1",
            "status": "RUNNING",
            "action": "retry_transient_ci",
        }
        with self.assertRaises(execution_progress.ExecutionProgressError):
            execution_progress.build_execution_progress(
                task=task,
                planned_stages=[{"id": "ci", "label": "CI", "status": "FAIL"}],
            )

    def test_true_human_blocker_is_prominent_and_never_hidden_by_other_green_stages(self) -> None:
        task = {
            "task_id": "human-gate",
            "status": "BLOCKED",
            "phase": "AUTHORITY_ORACLE_CHANGE_REQUIRED",
            "metadata": {
                "engineering_reconciler": {
                    "schema": "engineering-task-reconciler@1",
                    "last_delivery_key": "2:1:def",
                    "decisions": {
                        "2:1:def": {
                            "outcome": {
                                "decision": "STOP_NON_PRODUCT_AUTHORITY",
                                "action": None,
                                "allowed": False,
                                "human_required": True,
                            }
                        }
                    },
                }
            },
            "blockers": [
                {
                    "code": "AUTHORITY_ORACLE_CHANGE_REQUIRED",
                    "reason": "baseline acceptance requires authority",
                }
            ],
        }
        progress = execution_progress.build_execution_progress(
            task=task,
            planned_stages=[
                {"id": "pr-ci", "label": "PR CI", "status": "PASS"},
                {"id": "baseline", "label": "Baseline acceptance", "status": "BLOCKED"},
            ],
        )

        self.assertEqual(progress["overall"], "BLOCKED")
        self.assertTrue(progress["human"]["required"])
        self.assertTrue(progress["summary"]["needs_user_action"])
        rendered = execution_progress.render_progress_text(progress)
        self.assertIn("⛔ BLOCKED", rendered)
        self.assertIn("需要你介入：是", rendered)
        self.assertIn("baseline acceptance requires authority", rendered)


if __name__ == "__main__":
    unittest.main()
