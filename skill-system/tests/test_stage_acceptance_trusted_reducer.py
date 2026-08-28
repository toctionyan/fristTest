from __future__ import annotations

import copy
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from stage2b1_protected_approval import verify_protected_approval  # noqa: E402
from stage2b1_external_issuer import verify_github_artifact_attestation  # noqa: E402
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
        self.external_expected = {
            "repository": "toctionyan/fristTest",
            "signer_workflow": "toctionyan/fristTest/.github/workflows/quality.yml",
            "subject_digest": self.receipt["artifact"]["digest"],
            "predicate_type": "https://slsa.dev/provenance/v1",
            "source_digest": "e" * 40,
            "source_ref": "refs/pull/1/merge",
        }
        attestation_output = [
            {
                "verificationResult": {
                    "statement": {
                        "predicateType": self.external_expected["predicate_type"],
                        "subject": [{"name": "stage2b1-acceptance-decision", "digest": {"sha256": "f" * 64}}],
                    },
                    "signature": {"certificate": {"issuer": "github"}},
                    "verifiedTimestamps": [{"timestamp": "2026-08-28T00:00:00Z"}],
                }
            }
        ]
        with tempfile.NamedTemporaryFile() as artifact_file:
            artifact_file.write(b"verified artifact")
            artifact_file.flush()
            with patch(
                "stage2b1_external_issuer.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=json.dumps(attestation_output)),
            ):
                self.external = verify_github_artifact_attestation(
                    artifact_file.name,
                    expected=self.external_expected,
                )

    def _reduce(self, *, provenance=None, external=None, approval=None):
        return reduce_trusted_stage_acceptance(
            [self.receipt],
            required_receipt_ids=["1001"],
            **self.binding,
            expected_receipt_bindings=self.expected_receipt_bindings,
            verified_provenance=provenance,
            verified_external_issuers=external,
            expected_external_issuer_bindings=(
                {"1001": self.external_expected}
                if external is not None
                else None
            ),
            verified_protected_approval=approval,
            expected_protected_approval=self.approval_expected,
        )

    def test_only_fixed_verified_objects_can_produce_preview(self) -> None:
        decision = self._reduce(
            provenance={"1001": self.provenance},
            external={"1001": self.external},
            approval=self.approval,
        )
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
        first = self._reduce(
            provenance={"1001": self.provenance},
            external={"1001": self.external},
            approval=self.approval,
        )
        second = self._reduce(
            provenance={"1001": self.provenance},
            external={"1001": self.external},
            approval=self.approval,
        )
        self.assertEqual(first, second)
        self.assertNotIn("producer_issuer_validator", inspect.signature(reduce_trusted_stage_acceptance).parameters)

    def test_decision_tampering_fails_validation(self) -> None:
        decision = self._reduce(
            provenance={"1001": self.provenance},
            external={"1001": self.external},
            approval=self.approval,
        )
        changed = copy.deepcopy(decision)
        changed["proof_refs"] = []
        with self.assertRaises(ValueError):
            validate_trusted_stage_acceptance_decision(changed)

    def test_missing_external_issuer_proof_is_blocked(self) -> None:
        decision = self._reduce(provenance={"1001": self.provenance}, approval=self.approval)
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn("external_issuer_proof_required", decision["reasons"])

    def test_tampered_external_proof_is_blocked(self) -> None:
        tampered = copy.copy(self.external)
        object.__setattr__(tampered, "subject_digest", "sha256:" + "0" * 64)
        decision = self._reduce(
            provenance={"1001": self.provenance},
            external={"1001": tampered},
            approval=self.approval,
        )
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn("external_issuer_invalid:1001", decision["reasons"])

    def test_tampered_typed_provenance_is_blocked(self) -> None:
        tampered = copy.copy(self.provenance)
        object.__setattr__(tampered, "artifact_digest", "sha256:" + "0" * 64)
        decision = self._reduce(
            provenance={"1001": tampered},
            external={"1001": self.external},
            approval=self.approval,
        )
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn("trusted_provenance_invalid:1001", decision["reasons"])

    def test_tampered_typed_approval_is_blocked(self) -> None:
        tampered = copy.copy(self.approval)
        object.__setattr__(tampered, "reviewer_login", "attacker")
        decision = self._reduce(
            provenance={"1001": self.provenance},
            external={"1001": self.external},
            approval=tampered,
        )
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn("trusted_protected_approval_invalid", decision["reasons"])


if __name__ == "__main__":
    unittest.main()
