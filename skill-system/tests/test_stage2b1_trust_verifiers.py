from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from stage2b1_protected_approval import (  # noqa: E402
    PROTECTED_APPROVAL_OBSERVATION_SCHEMA,
    Stage2B1ProtectedApprovalError,
    verify_protected_approval,
)
from stage2b1_provenance import (  # noqa: E402
    PROVENANCE_OBSERVATION_SCHEMA,
    Stage2B1ProvenanceError,
    verify_artifact_provenance,
)


class Stage2B1TrustVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.common = {
            "stage_id": "stage2b1",
            "accepted_state_id": "accepted-state-17",
            "product_source_ref": "git-commit-sha1:" + "a" * 40,
            "protected_snapshot_digest": "sha256:" + "b" * 64,
            "control_plane_ref": "git-commit-sha1:" + "c" * 40,
            "execution_repo_ref": "git-commit-sha1:" + "d" * 40,
        }
        self.provenance = {
            "schema": PROVENANCE_OBSERVATION_SCHEMA,
            "repository": "toctionyan/fristTest",
            "workflow_path": ".github/workflows/source.yml",
            "workflow_id": 101,
            "event": "pull_request",
            "ref": "refs/pull/1/merge",
            "head_sha": "e" * 40,
            "run_id": 901,
            "run_attempt": 2,
            "artifact": {
                "id": "1001",
                "name": "stage2b1-acceptance-decision",
                "digest": "sha256:" + "f" * 64,
                "archive_digest": "sha256:" + "f" * 64,
                "content_digest": "sha256:" + "1" * 64,
                "source_run_id": 901,
                "source_run_attempt": 2,
            },
        }

    def test_provenance_requires_exact_expected_identity(self) -> None:
        verified = verify_artifact_provenance(
            self.provenance,
            expected={
                "receipt_id": "1001",
                "artifact_id": "1001",
                "artifact_name": "stage2b1-acceptance-decision",
                "artifact_digest": self.provenance["artifact"]["digest"],
                "content_digest": self.provenance["artifact"]["content_digest"],
                "repository": "toctionyan/fristTest",
                "workflow_path": ".github/workflows/source.yml",
                "workflow_id": 101,
                "event": "pull_request",
                "ref": "refs/pull/1/merge",
                "head_sha": "e" * 40,
                "run_id": 901,
                "run_attempt": 2,
            },
        )
        self.assertEqual(verified.artifact_id, "1001")
        self.assertTrue(verified.proof_ref.startswith("provenance:sha256:"))

        changed = copy.deepcopy(self.provenance)
        changed["artifact"]["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(Stage2B1ProvenanceError):
            verify_artifact_provenance(changed, expected={
                "receipt_id": "1001",
                "artifact_id": "1001",
                "artifact_name": "stage2b1-acceptance-decision",
                "artifact_digest": self.provenance["artifact"]["digest"],
                "content_digest": self.provenance["artifact"]["content_digest"],
                "repository": "toctionyan/fristTest",
                "workflow_path": ".github/workflows/source.yml",
                "workflow_id": 101,
                "event": "pull_request",
                "ref": "refs/pull/1/merge",
                "head_sha": "e" * 40,
                "run_id": 901,
                "run_attempt": 2,
            })

    def test_provenance_rejects_unknown_fields_and_run_mismatch(self) -> None:
        unknown = copy.deepcopy(self.provenance)
        unknown["latest"] = True
        with self.assertRaises(Stage2B1ProvenanceError):
            verify_artifact_provenance(unknown, expected={})
        mismatch = copy.deepcopy(self.provenance)
        mismatch["artifact"]["source_run_attempt"] = 1
        with self.assertRaises(Stage2B1ProvenanceError):
            verify_artifact_provenance(mismatch, expected={})

    def _approval(self) -> dict[str, object]:
        return {
            "schema": PROTECTED_APPROVAL_OBSERVATION_SCHEMA,
            **self.common,
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

    def test_protected_approval_requires_independent_approved_review(self) -> None:
        approval = self._approval()
        expected = {
            **self.common,
            "repository": approval["repository"],
            "workflow_path": approval["workflow_path"],
            "workflow_id": approval["workflow_id"],
            "run_id": approval["run_id"],
            "run_attempt": approval["run_attempt"],
            "environment": approval["environment"],
            "ref": approval["ref"],
            "head_sha": approval["head_sha"],
        }
        verified = verify_protected_approval(approval, expected=expected)
        self.assertTrue(verified.proof_ref.startswith("protected-approval:sha256:"))

        for field, value in (("approval_state", "pending"), ("reviewer_login", "toctionyan")):
            changed = copy.deepcopy(approval)
            changed[field] = value
            with self.assertRaises(Stage2B1ProtectedApprovalError):
                verify_protected_approval(changed, expected=expected)

    def test_protected_approval_rejects_unknown_field_and_binding_drift(self) -> None:
        approval = self._approval()
        expected = {
            **self.common,
            "repository": approval["repository"],
            "workflow_path": approval["workflow_path"],
            "workflow_id": approval["workflow_id"],
            "run_id": approval["run_id"],
            "run_attempt": approval["run_attempt"],
            "environment": approval["environment"],
            "ref": approval["ref"],
            "head_sha": approval["head_sha"],
        }
        changed = copy.deepcopy(approval)
        changed["unexpected"] = True
        with self.assertRaises(Stage2B1ProtectedApprovalError):
            verify_protected_approval(changed, expected=expected)
        changed = copy.deepcopy(approval)
        changed["control_plane_ref"] = "git-commit-sha1:" + "9" * 40
        with self.assertRaises(Stage2B1ProtectedApprovalError):
            verify_protected_approval(changed, expected=expected)


if __name__ == "__main__":
    unittest.main()
