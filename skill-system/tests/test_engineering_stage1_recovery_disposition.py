from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from autonomy_grant import bind_autonomy_grant, create_autonomy_grant  # noqa: E402
from engineering_task_controller import reconcile_stage1_failure  # noqa: E402
from failure_recovery_policy import decide_recovery  # noqa: E402
from task_run import TaskRunStore  # noqa: E402


class EngineeringStage1RecoveryDispositionTests(unittest.TestCase):
    REPOSITORY = "toctionyan/fristTest"
    BRANCH = "feature/diagnosis-test"
    HEAD = "b" * 40
    SIGNATURE = "f" * 64

    def _store_and_grant(self, root: Path) -> tuple[TaskRunStore, dict]:
        binding = {
            "repository": self.REPOSITORY,
            "workflow_name": "quality",
            "workflow_run_id": 12345,
            "workflow_run_attempt": 1,
            "head_sha": self.HEAD,
            "failure_signature": self.SIGNATURE,
            "branch": self.BRANCH,
            "base_sha": self.HEAD,
        }
        store = TaskRunStore.open_or_create(
            root / "task-run.json",
            task_id="stage1-diagnosis",
            task_kind="github-governed-repair",
            binding=binding,
            required_conditions=("classification_complete",),
        )
        store.checkpoint(
            status="RUNNING",
            phase="FAILURE_INGESTED",
            workspace_fingerprint=None,
            evidence_refs=["failure-case.json"],
        )
        store.checkpoint(
            status="WAITING_EXTERNAL_RESULT",
            phase="READ_ONLY_DIAGNOSIS_REQUIRED",
            workspace_fingerprint=None,
            evidence_refs=["failure-case.json"],
        )
        grant = create_autonomy_grant(
            task=store.payload,
            repository=self.REPOSITORY,
            branch=self.BRANCH,
            base_sha=self.HEAD,
            issued_by="toctionyan",
            allowed_actions=("analyze_failure", "retry_transient_ci"),
        )
        bind_autonomy_grant(
            store,
            grant,
            repository=self.REPOSITORY,
            owner_authorization_ref="test-owner-authorization",
        )
        return store, grant

    def _failure_case(self, classification: str, *, repair_class: str) -> dict:
        policy = decide_recovery(
            repair_route={"repair_class": repair_class},
            classification=classification,
            diagnosis_attempt=0,
            max_diagnosis_attempts=2,
        )
        return {
            "schema": "github-failure-ingest@1",
            "status": "INGESTED",
            "repository": self.REPOSITORY,
            "workflow_name": "quality",
            "workflow_run_id": 12345,
            "workflow_run_attempt": 1,
            "head_sha": self.HEAD,
            "failure_signature": self.SIGNATURE,
            "repair_branch": self.BRANCH,
            "same_repository": True,
            "production_closed": False,
            "classification": classification,
            "repair_allowed": False,
            "candidate_paths": [],
            "repair_route": {
                "repair_class": repair_class,
                "automatic_write_allowed": False,
                "human_required": repair_class != "UNKNOWN",
            },
            "recovery_policy": policy,
        }

    def test_unknown_failure_continues_read_only_without_user_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, grant = self._store_and_grant(Path(temp))
            outcome = reconcile_stage1_failure(
                store,
                grant,
                repository=self.REPOSITORY,
                failure_case=self._failure_case(
                    "unknown_failure_without_gate_evidence",
                    repair_class="UNKNOWN",
                ),
                current_head_sha=self.HEAD,
            )

        self.assertEqual(outcome["decision"], "ANALYZE_FAILURE")
        self.assertEqual(outcome["action"], "analyze_failure")
        self.assertTrue(outcome["allowed"])
        self.assertFalse(outcome["human_required"])
        self.assertFalse(outcome["product_write_allowed"])
        self.assertFalse(outcome["merge_allowed"])
        self.assertFalse(outcome["deploy_allowed"])
        self.assertFalse(outcome["production_closed"])

    def test_protected_baseline_remains_human_authority_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, grant = self._store_and_grant(Path(temp))
            outcome = reconcile_stage1_failure(
                store,
                grant,
                repository=self.REPOSITORY,
                failure_case=self._failure_case(
                    "protected_baseline_drift",
                    repair_class="AUTHORITY_ORACLE_CHANGE_REQUIRED",
                ),
                current_head_sha=self.HEAD,
            )

        self.assertEqual(outcome["decision"], "STOP_NON_PRODUCT_AUTHORITY")
        self.assertFalse(outcome["allowed"])
        self.assertTrue(outcome["human_required"])
        self.assertEqual(outcome["failure_class"], "PROTECTED_BASELINE_DRIFT")
        self.assertFalse(outcome["product_write_allowed"])


if __name__ == "__main__":
    unittest.main()
