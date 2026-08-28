from __future__ import annotations

import copy
import inspect
import sys
import unittest
from pathlib import Path


CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from stage2b1_protected_approval import verify_protected_approval  # noqa: E402
from stage2b1_provenance import verify_artifact_provenance  # noqa: E402
from stage_acceptance_reducer import (  # noqa: E402
    ACCEPTABLE_PREVIEW,
    BLOCKED,
    reduce_trusted_stage_acceptance,
    validate_trusted_stage_acceptance_decision,
)
from stage_evidence_receipt import build_stage_evidence_receipt  # noqa: E402


class TrustedStageAcceptanceReducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = {
            "stage_id": "stage2b1",
            "accepted_state_id": "accepted-state-17",
            "product_source_ref": "git-commit-sha1:" + "a" * 40,
            "protected_snapshot_digest": "sha256:" + "b" * 64,
            "control_plane_ref": "git-commit-sha1:" + "c" * 40,
            "execution_repo_ref": "git-commit-sha1:" + "d" * 40,
        }
        self.receipt = build_stage_evidence_receipt(
            **self.binding,
            workflow_run_attempt={"run_id": 901, "attempt": 2},
            artifact={"id": "1001", "digest": "sha256:" + "f" * 64},
            result="PASS",
            producer="quality",
            policy="stage2b1-p3-evidence-receipt@1",
        )
        self.expected_receipt_bindings = {
            "1001": {
                "artifact": self.receipt["artifact"],
                "workflow_run_attempt": self.receipt["workflow_run_attempt"],
                "policy": self.receipt["policy"],
            }
        }
        self.provenance = verify_artifact_provenance(
            {
                "schema": "stage2b1-github-provenance@1",
                "repository": "toctionyan/fristTest",
                "workflow_path": ".github/workflows/quality.yml",
                "workflow_id": 101,
                "event": "pull_request",
                "ref": "refs/pull/1/merge",
                "head_sha": "e" * 40,
                "run_id": 901,
                "run_attempt": 2,
                "artifact": {
                    "id": "1001",
                    "name": "stage2b1-acceptance-decision",
                    "digest": self.receipt["artifact"]["digest"],
                    "archive_digest": self.receipt["artifact"]["digest"],
                    "content_digest": "sha256:" + "1" * 64,
                    "source_run_id": 901,
                    "source_run_attempt": 2,
                },
            },
            expected={
                "receipt_id": "1001",
                "artifact_id": "1001",
                "artifact_name": "stage2b1-acceptance-decision",
                "artifact_digest": self.receipt["artifact"]["digest"],
                "content_digest": "sha256:" + "1" * 64,
                "repository": "toctionyan/fristTest",
                "workflow_path": ".github/workflows/quality.yml",
                "workflow_id": 101,
                "event": "pull_request",
                "ref": "refs/pull/1/merge",
                "head_sha": "e" * 40,
                "run_id": 901,
                "run_attempt": 2,
            },
        )
        approval = {
            "schema": "stage2b1-protected-approval-observation@1",
            **self.binding,
            "repository": "toctionyan/fristTest",
            "workflow_path": ".github/workflows/governed-stage2b1-acceptance.yml",
            "workflow_id": 202,
            "run_id": 902,
            "run_attempt": 1,
            "environment": "stage2b1-acceptance",
            "environment_id": 303,
            "ref": "refs/heads/main",
            "head_sha": "1" * 40,
            "approval_state": "approved",
            "review_id": 404,
            "reviewer_id": 505,
            "reviewer_login": "independent-reviewer",
            "run_actor_login": "toctionyan",
            "self_review_forbidden": True,
        }
        self.approval_expected = {
            **self.binding,
            "repository": approval["repository"],
            "workflow_path": approval["workflow_path"],
            "workflow_id": approval["workflow_id"],
            "run_id": approval["run_id"],
            "run_attempt": approval["run_attempt"],
            "environment": approval["environment"],
            "ref": approval["ref"],
            "head_sha": approval["head_sha"],
        }
        self.approval = verify_protected_approval(
            approval,
            expected=self.approval_expected,
        )

    def _reduce(self, *, provenance=None, approval=None):
        return reduce_trusted_stage_acceptance(
            [self.receipt],
            required_receipt_ids=["1001"],
            **self.binding,
            expected_receipt_bindings=self.expected_receipt_bindings,
            verified_provenance=provenance,
            verified_protected_approval=approval,
            expected_protected_approval=self.approval_expected,
        )

    def test_only_fixed_verified_objects_can_produce_preview(self) -> None:
        decision = self._reduce(provenance={"1001": self.provenance}, approval=self.approval)
        self.assertEqual(decision["status"], ACCEPTABLE_PREVIEW)
        self.assertEqual(validate_trusted_stage_acceptance_decision(decision), decision)
        self.assertGreaterEqual(len(decision["proof_refs"]), 2)

    def test_missing_trust_evidence_is_blocked(self) -> None:
        decision = self._reduce(provenance=None, approval=None)
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn("trusted_provenance_required", decision["reasons"])
        self.assertIn("trusted_protected_approval_required", decision["reasons"])

    def test_tampered_or_wrong_typed_proof_is_blocked(self) -> None:
        decision = self._reduce(
            provenance={"1001": object()},
            approval=object(),
        )
        self.assertEqual(decision["status"], BLOCKED)

    def test_decision_is_deterministic_and_does_not_accept_callback(self) -> None:
        first = self._reduce(provenance={"1001": self.provenance}, approval=self.approval)
        second = self._reduce(provenance={"1001": self.provenance}, approval=self.approval)
        self.assertEqual(first, second)
        self.assertNotIn("producer_issuer_validator", inspect.signature(reduce_trusted_stage_acceptance).parameters)

    def test_decision_tampering_fails_validation(self) -> None:
        decision = self._reduce(provenance={"1001": self.provenance}, approval=self.approval)
        changed = copy.deepcopy(decision)
        changed["proof_refs"] = []
        with self.assertRaises(ValueError):
            validate_trusted_stage_acceptance_decision(changed)


if __name__ == "__main__":
    unittest.main()
