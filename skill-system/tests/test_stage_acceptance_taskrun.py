from __future__ import annotations

import copy
import shutil
import sys
import tempfile
import unittest
import inspect
from pathlib import Path
from unittest.mock import patch

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from stage_acceptance_reducer import (
    ACCEPTABLE_PREVIEW,
    BLOCKED,
    TrustedStageAcceptanceDecision,
    _trusted_decision,
    reduce_stage_acceptance,
)
from stage_acceptance_taskrun import (
    STAGE_ACCEPTANCE_BLOCKED_PHASE,
    STAGE_ACCEPTANCE_PREVIEW_PHASE,
    StageAcceptanceTaskRunError,
    project_stage_acceptance_to_taskrun,
)
from stage_evidence_receipt import build_stage_evidence_receipt
from task_run import TaskRunStore


def trusted_decision(
    raw: dict[str, object],
    proof_refs: list[str] | None = None,
    binding: dict[str, object] | None = None,
) -> TrustedStageAcceptanceDecision:
    return _trusted_decision(
        input_digest=str(raw["input_digest"]),
        status=str(raw["status"]),
        reasons=list(raw["reasons"]),  # type: ignore[arg-type]
        receipt_refs=list(raw["receipt_refs"]),  # type: ignore[arg-type]
        proof_refs=proof_refs or [
            "provenance:test",
            "external-issuer:test",
            "protected-approval:test",
        ],
        binding=binding,
    )


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
        # The reducer re-verification boundary is covered independently. These
        # projection tests isolate TaskRun behavior behind that boundary.
        self.verification = object()

    def receipt(self, artifact_id: str = "artifact-1") -> dict[str, object]:
        return build_stage_evidence_receipt(
            **self.binding,
            workflow_run_attempt={"run_id": 17, "attempt": 1},
            artifact={"id": artifact_id, "digest": "sha256:" + "e" * 64},
            result="PASS",
            producer="quality",
            policy="stage2b1-p3-evidence-receipt@1",
        )

    def raw_decision(self, *, result: str = "PASS") -> dict[str, object]:
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

    def decision(self, *, result: str = "PASS") -> TrustedStageAcceptanceDecision:
        return trusted_decision(
            self.raw_decision(result=result),
            binding={
                "common": dict(self.binding),
                "task_id": "stage2b1-task",
                "receipts": [],
                "provenance": [],
                "external_issuer": [],
                "protected_approval": {
                    "approval_sha256": "a" * 64,
                    "gate_id": "gate-stage2b1-acceptance",
                    "task_id": "stage2b1-task",
                    "run_id": 17,
                    "run_attempt": 1,
                    "evidence_bindings": [
                        {
                            "receipt_id": "artifact-1",
                            "artifact_id": "artifact-1",
                            "artifact_digest": "sha256:" + "e" * 64,
                            "run_id": 17,
                            "run_attempt": 1,
                        }
                    ],
                },
            },
        )

    def project(self, decision: TrustedStageAcceptanceDecision) -> dict[str, object]:
        with patch(
            "stage_acceptance_taskrun.reverify_trusted_stage_acceptance_decision",
            return_value=decision,
        ):
            return project_stage_acceptance_to_taskrun(
                self.store,
                decision,
                expected_binding=self.binding,
                workspace_fingerprint="workspace-1",
                verification=self.verification,
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
                workspace_fingerprint="workspace-1",
                verification=self.verification,
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
                workspace_fingerprint="workspace-1",
                verification=self.verification,
            )
        self.assertEqual(self.store.payload, before)

    def test_changed_projection_for_same_decision_is_rejected(self) -> None:
        decision = self.decision()
        self.project(decision)
        self.store.payload["checkpoints"][1]["evidence_refs"] = ["tampered"]
        before = copy.deepcopy(self.store.payload)
        with self.assertRaisesRegex(StageAcceptanceTaskRunError, "previously projected"):
            with patch(
                "stage_acceptance_taskrun.reverify_trusted_stage_acceptance_decision",
                return_value=decision,
            ):
                project_stage_acceptance_to_taskrun(
                    self.store,
                    decision,
                    expected_binding=self.binding,
                    workspace_fingerprint="workspace-1",
                    verification=self.verification,
                )
        self.assertEqual(self.store.payload, before)

    def test_invalid_decision_and_terminal_taskrun_fail_closed(self) -> None:
        with self.assertRaisesRegex(StageAcceptanceTaskRunError, "decision is invalid"):
            project_stage_acceptance_to_taskrun(
                self.store,
                {"status": ACCEPTABLE_PREVIEW},
                expected_binding=self.binding,
                workspace_fingerprint="workspace-1",
                verification=self.verification,
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

    def test_projection_derives_evidence_refs_from_reducer_decision(self) -> None:
        self.assertNotIn(
            "evidence_refs",
            inspect.signature(project_stage_acceptance_to_taskrun).parameters,
        )
        decision = self.decision()
        checkpoint = self.project(decision)
        self.assertEqual(
            checkpoint["evidence_refs"],
            [
                "stage-acceptance-decision:" + str(decision["decision_id"]),
                "stage-evidence-receipt:artifact-1",
                "external-issuer:test",
                "protected-approval:test",
                "provenance:test",
            ],
        )

    def test_legacy_v1_decision_is_rejected_without_mutation(self) -> None:
        before = copy.deepcopy(self.store.payload)
        with self.assertRaisesRegex(StageAcceptanceTaskRunError, "decision is invalid"):
            self.project(self.raw_decision())
        self.assertEqual(self.store.payload, before)

    def test_untrusted_or_incomplete_proof_refs_are_rejected_without_mutation(self) -> None:
        for proof_refs in (
            ["provenance:test", "external-issuer:test"],
            [
                "provenance:test",
                "external-issuer:test",
                "protected-approval:test",
                "receipt:untrusted",
            ],
        ):
            with self.subTest(proof_refs=proof_refs):
                before = copy.deepcopy(self.store.payload)
                with self.assertRaisesRegex(
                    StageAcceptanceTaskRunError,
                    "decision is invalid",
                ):
                    self.project(trusted_decision(self.raw_decision(), proof_refs))
                self.assertEqual(self.store.payload, before)

    def test_projection_reverification_failure_does_not_mutate_taskrun(self) -> None:
        decision = self.decision()
        before = copy.deepcopy(self.store.payload)
        with patch(
            "stage_acceptance_taskrun.reverify_trusted_stage_acceptance_decision",
            side_effect=ValueError("fixed verifier rejected evidence"),
        ):
            with self.assertRaisesRegex(StageAcceptanceTaskRunError, "independently reverified"):
                project_stage_acceptance_to_taskrun(
                    self.store,
                    decision,
                    expected_binding=self.binding,
                    workspace_fingerprint="workspace-1",
                    verification=self.verification,
                )
        self.assertEqual(self.store.payload, before)


if __name__ == "__main__":
    unittest.main()
