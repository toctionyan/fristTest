from __future__ import annotations

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from stage_acceptance_reducer import ACCEPTABLE_PREVIEW, BLOCKED, reduce_stage_acceptance
from stage_acceptance_taskrun import (
    STAGE_ACCEPTANCE_BLOCKED_PHASE,
    STAGE_ACCEPTANCE_PREVIEW_PHASE,
    StageAcceptanceTaskRunError,
    project_stage_acceptance_to_taskrun,
)
from stage_evidence_receipt import build_stage_evidence_receipt
from task_run import TaskRunStore


class StageAcceptanceTaskRunTests(unittest.TestCase):
    binding = {
        "stage_id": "stage2b1",
        "accepted_state_id": "accepted-state-17",
        "product_source_ref": "git-commit-sha1:" + "a" * 40,
        "protected_snapshot_digest": "sha256:" + "b" * 64,
        "control_plane_ref": "git-commit-sha1:" + "c" * 40,
        "execution_repo_ref": "git-commit-sha1:" + "d" * 40,
    }

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="stage-acceptance-taskrun-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.store = TaskRunStore.open_or_create(
            self.root / "task-run.json",
            task_id="stage2b1-task",
            task_kind="stage-acceptance",
            binding=self.binding,
            required_conditions=["stage-accepted", "quality-green"],
            current_workspace_fingerprint="workspace-1",
        )

    def receipt(self, artifact_id: str = "artifact-1") -> dict[str, object]:
        return build_stage_evidence_receipt(
            **self.binding,
            workflow_run_attempt={"run_id": 17, "attempt": 1},
            artifact={"id": artifact_id, "digest": "sha256:" + "e" * 64},
            result="PASS",
            producer="quality",
            policy="stage2b1-p3-evidence-receipt@1",
        )

    def decision(self, *, result: str = "PASS") -> dict[str, object]:
        receipt = self.receipt()
        if result != "PASS":
            receipt = build_stage_evidence_receipt(
                **self.binding,
                workflow_run_attempt={"run_id": 17, "attempt": 1},
                artifact={"id": "artifact-1", "digest": "sha256:" + "e" * 64},
                result=result,
                producer="quality",
                policy="stage2b1-p3-evidence-receipt@1",
            )
        return reduce_stage_acceptance(
            [receipt],
            required_receipt_ids=["artifact-1"],
            **self.binding,
            expected_receipt_bindings={
                "artifact-1": {
                    "artifact": receipt["artifact"],
                    "workflow_run_attempt": receipt["workflow_run_attempt"],
                    "policy": receipt["policy"],
                }
            },
        )

    def project(self, decision: dict[str, object]) -> dict[str, object]:
        return project_stage_acceptance_to_taskrun(
            self.store,
            decision,
            expected_binding=self.binding,
            evidence_refs=["receipt:artifact-1", "decision:" + str(decision["decision_id"])],
            workspace_fingerprint="workspace-1",
        )

    def test_acceptable_preview_projects_validating_without_completion(self) -> None:
        decision = self.decision()
        self.assertEqual(decision["status"], ACCEPTABLE_PREVIEW)
        checkpoint = self.project(decision)
        self.assertEqual(checkpoint["phase"], STAGE_ACCEPTANCE_PREVIEW_PHASE)
        self.assertEqual(self.store.payload["status"], "RUNNING")
        self.assertFalse(self.store.completion_decision().eligible)
        self.assertEqual(
            self.store.payload["conditions"],
            {
                "stage-accepted": {"satisfied": False, "evidence_refs": [], "updated_at": None},
                "quality-green": {"satisfied": False, "evidence_refs": [], "updated_at": None},
            },
        )
        self.assertFalse(checkpoint["metadata"]["completion_authority_changed"])
        self.assertFalse(checkpoint["metadata"]["stage_acceptance_write"])

    def test_blocked_preview_projects_blocked_without_satisfying_conditions(self) -> None:
        decision = self.decision(result="FAIL")
        self.assertEqual(decision["status"], BLOCKED)
        checkpoint = self.project(decision)
        self.assertEqual(checkpoint["phase"], STAGE_ACCEPTANCE_BLOCKED_PHASE)
        self.assertEqual(self.store.payload["status"], "BLOCKED")
        self.assertFalse(self.store.completion_decision().eligible)
        self.assertTrue(all(not row["satisfied"] for row in self.store.payload["conditions"].values()))

    def test_repeating_same_projection_is_idempotent(self) -> None:
        decision = self.decision()
        first = self.project(decision)
        revision = self.store.payload["revision"]
        second = self.project(decision)
        self.assertEqual(first, second)
        self.assertEqual(self.store.payload["revision"], revision)
        self.assertEqual(len(self.store.payload["checkpoints"]), 2)

    def test_binding_mismatch_does_not_write(self) -> None:
        decision = self.decision()
        before = copy.deepcopy(self.store.payload)
        wrong = dict(self.binding, control_plane_ref="git-commit-sha1:" + "f" * 40)
        with self.assertRaisesRegex(StageAcceptanceTaskRunError, "binding mismatch"):
            project_stage_acceptance_to_taskrun(
                self.store,
                decision,
                expected_binding=wrong,
                evidence_refs=["receipt:artifact-1"],
                workspace_fingerprint="workspace-1",
            )
        self.assertEqual(self.store.payload, before)

    def test_unknown_expected_binding_does_not_write(self) -> None:
        decision = self.decision()
        before = copy.deepcopy(self.store.payload)
        with self.assertRaisesRegex(StageAcceptanceTaskRunError, "unknown fields"):
            project_stage_acceptance_to_taskrun(
                self.store,
                decision,
                expected_binding=dict(self.binding, extra="unexpected"),
                evidence_refs=["receipt:artifact-1"],
                workspace_fingerprint="workspace-1",
            )
        self.assertEqual(self.store.payload, before)

    def test_changed_projection_for_same_decision_is_rejected(self) -> None:
        decision = self.decision()
        self.project(decision)
        before = copy.deepcopy(self.store.payload)
        with self.assertRaisesRegex(StageAcceptanceTaskRunError, "previously projected"):
            project_stage_acceptance_to_taskrun(
                self.store,
                decision,
                expected_binding=self.binding,
                evidence_refs=["different-evidence"],
                workspace_fingerprint="workspace-1",
            )
        self.assertEqual(self.store.payload, before)

    def test_invalid_decision_and_terminal_taskrun_fail_closed(self) -> None:
        with self.assertRaisesRegex(StageAcceptanceTaskRunError, "decision is invalid"):
            project_stage_acceptance_to_taskrun(
                self.store,
                {"status": ACCEPTABLE_PREVIEW},
                expected_binding=self.binding,
                evidence_refs=["receipt:artifact-1"],
                workspace_fingerprint="workspace-1",
            )

        self.store.mark_condition("stage-accepted", evidence_refs=["receipt:artifact-1"])
        self.store.mark_condition("quality-green", evidence_refs=["receipt:artifact-1"])
        self.store.checkpoint(
            status="RUNNING",
            phase="RUNNING",
            workspace_fingerprint="workspace-1",
        )
        self.store.complete(workspace_fingerprint="workspace-1", evidence_refs=["receipt:artifact-1"])
        before = copy.deepcopy(self.store.payload)
        with self.assertRaisesRegex(StageAcceptanceTaskRunError, "terminal TaskRun"):
            self.project(self.decision())
        self.assertEqual(self.store.payload, before)


if __name__ == "__main__":
    unittest.main()
