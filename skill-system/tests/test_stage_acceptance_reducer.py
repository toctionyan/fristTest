from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from stage_acceptance_reducer import (  # type: ignore
    ACCEPTABLE_PREVIEW,
    BLOCKED,
    reduce_stage_acceptance,
    validate_stage_acceptance_decision,
)
from stage_evidence_receipt import build_stage_evidence_receipt, receipt_digest  # type: ignore


class StageAcceptanceReducerTests(unittest.TestCase):
    @staticmethod
    def receipt(
        artifact_id: str,
        *,
        stage_id: str = "STAGE-2B1-P3",
        accepted_state_id: str = "accepted-state-17",
        product_source_ref: str = "git-commit-sha1:" + "a" * 40,
        protected_snapshot_digest: str = "sha256:" + "b" * 64,
        control_plane_ref: str = "git-commit-sha1:" + "c" * 40,
        execution_repo_ref: str = "toctionyan/fristTest@main",
        workflow_run_id: int = 2185,
        attempt: int = 1,
        artifact_digest: str = "sha256:" + "d" * 64,
        result: str = "PASS",
        policy: str = "stage2b1-p3-evidence-receipt@1",
    ) -> dict[str, object]:
        return build_stage_evidence_receipt(
            stage_id=stage_id,
            accepted_state_id=accepted_state_id,
            product_source_ref=product_source_ref,
            protected_snapshot_digest=protected_snapshot_digest,
            control_plane_ref=control_plane_ref,
            execution_repo_ref=execution_repo_ref,
            workflow_run_attempt={"run_id": workflow_run_id, "attempt": attempt},
            artifact={"id": artifact_id, "digest": artifact_digest},
            result=result,
            producer="github-actions",
            policy=policy,
        )

    def reduce(self, receipts, ids=("artifact-a",)):
        expected = {
            artifact_id: {
                "artifact": {
                    "id": artifact_id,
                    "digest": "sha256:" + ("d" if artifact_id == "artifact-a" else "e") * 64,
                },
                "workflow_run_attempt": {"run_id": 2185, "attempt": 1},
                "policy": "stage2b1-p3-evidence-receipt@1",
            }
            for artifact_id in ids
        }
        return reduce_stage_acceptance(
            receipts,
            required_receipt_ids=ids,
            stage_id="STAGE-2B1-P3",
            accepted_state_id="accepted-state-17",
            product_source_ref="git-commit-sha1:" + "a" * 40,
            protected_snapshot_digest="sha256:" + "b" * 64,
            control_plane_ref="git-commit-sha1:" + "c" * 40,
            execution_repo_ref="toctionyan/fristTest@main",
            expected_receipt_bindings=expected,
        )

    def test_complete_explicit_receipts_are_only_acceptable_preview(self) -> None:
        decision = self.reduce([self.receipt("artifact-a")])
        self.assertEqual(decision["status"], ACCEPTABLE_PREVIEW)
        self.assertEqual(decision["reasons"], [])
        self.assertEqual(validate_stage_acceptance_decision(decision), decision)

    def test_missing_receipt_is_blocked(self) -> None:
        decision = self.reduce([], ids=("artifact-a",))
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn("required_receipt_missing:artifact-a", decision["reasons"])

    def test_fail_result_is_blocked(self) -> None:
        decision = self.reduce([self.receipt("artifact-a", result="FAIL")])
        self.assertEqual(decision["status"], BLOCKED)
        self.assertTrue(any("receipt_result_not_pass" in item for item in decision["reasons"]))

    def test_untrusted_producer_and_policy_are_blocked(self) -> None:
        receipt = self.receipt("artifact-a", policy="caller-invented@1")
        receipt["producer"] = "untrusted-caller"
        receipt["receipt_digest"] = receipt_digest(receipt)
        decision = self.reduce([receipt])
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn("receipt_producer_untrusted:artifact-a", decision["reasons"])
        self.assertIn("receipt_policy_untrusted:artifact-a", decision["reasons"])

    def test_shared_binding_mismatches_are_blocked(self) -> None:
        cases = (
            "stage_id",
            "accepted_state_id",
            "product_source_ref",
            "protected_snapshot_digest",
            "control_plane_ref",
            "execution_repo_ref",
            "policy",
        )
        for field in cases:
            with self.subTest(field=field):
                first = self.receipt("artifact-a")
                second_kwargs = {
                    "stage_id": "STAGE-2B1-P4" if field == "stage_id" else "STAGE-2B1-P3",
                    "accepted_state_id": "accepted-state-18" if field == "accepted_state_id" else "accepted-state-17",
                    "product_source_ref": "git-commit-sha1:" + "e" * 40 if field == "product_source_ref" else "git-commit-sha1:" + "a" * 40,
                    "protected_snapshot_digest": "sha256:" + "e" * 64 if field == "protected_snapshot_digest" else "sha256:" + "b" * 64,
                    "control_plane_ref": "git-commit-sha1:" + "e" * 40 if field == "control_plane_ref" else "git-commit-sha1:" + "c" * 40,
                    "execution_repo_ref": "toctionyan/other@main" if field == "execution_repo_ref" else "toctionyan/fristTest@main",
                    "policy": "different-policy@1" if field == "policy" else "stage2b1-p3-evidence-receipt@1",
                }
                second = self.receipt("artifact-b", **second_kwargs)
                decision = self.reduce([first, second], ids=("artifact-a", "artifact-b"))
                self.assertEqual(decision["status"], BLOCKED)
                self.assertTrue(any(f"receipt_binding_mismatch:{field}" in item for item in decision["reasons"]))

    def test_artifact_workflow_and_policy_expected_mismatches_are_blocked(self) -> None:
        receipt = self.receipt("artifact-a")
        decision = reduce_stage_acceptance(
            [receipt],
            required_receipt_ids=("artifact-a",),
            stage_id="STAGE-2B1-P3",
            accepted_state_id="accepted-state-17",
            product_source_ref="git-commit-sha1:" + "a" * 40,
            protected_snapshot_digest="sha256:" + "b" * 64,
            control_plane_ref="git-commit-sha1:" + "c" * 40,
            execution_repo_ref="toctionyan/fristTest@main",
            expected_receipt_bindings={
                "artifact-a": {
                    "artifact": {"id": "artifact-a", "digest": "sha256:" + "e" * 64},
                    "workflow_run_attempt": {"run_id": 9999, "attempt": 2},
                    "policy": "different-policy@1",
                }
            },
        )
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn("receipt_binding_mismatch:artifact:artifact-a", decision["reasons"])
        self.assertIn("receipt_binding_mismatch:workflow_run_attempt:artifact-a", decision["reasons"])
        self.assertIn("receipt_binding_mismatch:policy:artifact-a", decision["reasons"])

    def test_missing_expected_contract_is_blocked(self) -> None:
        decision = reduce_stage_acceptance(
            [self.receipt("artifact-a")],
            required_receipt_ids=("artifact-a",),
            stage_id="STAGE-2B1-P3",
            accepted_state_id="accepted-state-17",
            product_source_ref="git-commit-sha1:" + "a" * 40,
            protected_snapshot_digest="sha256:" + "b" * 64,
            control_plane_ref="git-commit-sha1:" + "c" * 40,
            execution_repo_ref="toctionyan/fristTest@main",
            expected_receipt_bindings={},
        )
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn("expected_receipt_binding_missing:artifact-a", decision["reasons"])

    def test_duplicate_id_and_same_id_content_mismatch_are_blocked(self) -> None:
        one = self.receipt("artifact-a")
        same = copy.deepcopy(one)
        different = self.receipt("artifact-a", artifact_digest="sha256:" + "e" * 64)
        duplicate = self.reduce([one, same], ids=("artifact-a",))
        self.assertEqual(duplicate["status"], BLOCKED)
        self.assertIn("receipt_id_duplicate:artifact-a", duplicate["reasons"])
        mismatch = self.reduce([one, different], ids=("artifact-a",))
        self.assertIn("receipt_id_duplicate_content_mismatch:artifact-a", mismatch["reasons"])

    def test_tampered_receipt_is_fail_closed(self) -> None:
        tampered = self.receipt("artifact-a")
        tampered["artifact"]["digest"] = "sha256:" + "e" * 64  # type: ignore[index]
        decision = self.reduce([tampered])
        self.assertEqual(decision["status"], BLOCKED)
        self.assertTrue(any("receipt_digest_mismatch" in item for item in decision["reasons"]))

    def test_input_order_does_not_change_decision(self) -> None:
        one = self.receipt("artifact-a")
        two = self.receipt("artifact-b", artifact_digest="sha256:" + "e" * 64)
        left = self.reduce([one, two], ids=("artifact-a", "artifact-b"))
        right = self.reduce([two, one], ids=("artifact-b", "artifact-a"))
        self.assertEqual(left, right)

    def test_same_input_is_repeatable_and_no_latest_or_selection_logic_exists(self) -> None:
        receipt = self.receipt("artifact-a")
        first = self.reduce([receipt])
        second = self.reduce([receipt])
        self.assertEqual(first["decision_id"], second["decision_id"])
        source = (CONTROLLER / "stage_acceptance_reducer.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("latest", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("dispatch", source)


if __name__ == "__main__":
    unittest.main()
