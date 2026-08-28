from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
SCRIPTS = ROOT / "scripts"
for path in (CONTROLLER, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from p4_8_evidence_producer import _server_artifact  # noqa: E402
from stage2b1_external_issuer import (  # noqa: E402
    ExternalIssuerVerificationError,
    STAGE2B1_PREDICATE_TYPE,
    STAGE2B1_REPOSITORY,
    STAGE2B1_SIGNER_WORKFLOW,
    STAGE2B1_SOURCE_REF,
    reverify_external_issuer_proof,
    verify_github_artifact_attestation,
    verify_p4_8_payload_attestation,
    verify_p4_8_source_artifact,
)


RUN_ID = 901
RUN_ATTEMPT = 2
HEAD_SHA = "a" * 40
RUNNER_INVOCATION = (
    f"https://github.com/{STAGE2B1_REPOSITORY}"
    f"/actions/runs/{RUN_ID}/attempts/{RUN_ATTEMPT}"
)


def response(value: object) -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")


def completed_run(*, attempt: int = RUN_ATTEMPT) -> dict[str, object]:
    return {
        "id": RUN_ID,
        "run_attempt": attempt,
        "name": "p4-8-evidence-producer",
        "path": ".github/workflows/p4-8-evidence-producer.yml",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": HEAD_SHA,
        "status": "completed",
        "conclusion": "success",
        "repository": {"full_name": STAGE2B1_REPOSITORY},
        "head_repository": {"full_name": STAGE2B1_REPOSITORY},
    }


def artifact_metadata(digest: str, *, attempt: int = RUN_ATTEMPT) -> dict[str, object]:
    return {
        "id": 7701,
        "name": f"p4-8-evidence-payload-{RUN_ID}-{attempt}",
        "digest": digest,
        "expired": False,
        "workflow_run": {
            "id": RUN_ID,
            "run_attempt": attempt,
            "head_branch": "main",
            "head_sha": HEAD_SHA,
        },
    }


def attestation(subject_digest: str, *, attempt: int = RUN_ATTEMPT) -> list[dict[str, object]]:
    return [
        {
            "verificationResult": {
                "statement": {
                    "predicateType": STAGE2B1_PREDICATE_TYPE,
                    "subject": [{"digest": {"sha256": subject_digest.removeprefix("sha256:")}}],
                },
                "signature": {
                    "certificate": {
                        "issuer": "github",
                        "runnerInvocationURI": (
                            f"https://github.com/{STAGE2B1_REPOSITORY}"
                            f"/actions/runs/{RUN_ID}/attempts/{attempt}"
                        ),
                    }
                },
                "verifiedTimestamps": [{"timestamp": "2026-08-28T00:00:00Z"}],
            }
        }
    ]


class P48ExternalIssuerReverifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="p4-8-reverify-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.archive = self.root / "payload.zip"
        self.archive.write_bytes(b"exact downloaded artifact archive")
        self.artifact_digest = "sha256:" + hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.expected = {
            "repository": STAGE2B1_REPOSITORY,
            "signer_workflow": STAGE2B1_SIGNER_WORKFLOW,
            "subject_digest": self.artifact_digest,
            "predicate_type": STAGE2B1_PREDICATE_TYPE,
            "source_digest": HEAD_SHA,
            "source_ref": STAGE2B1_SOURCE_REF,
        }

    def _source_proof(self):
        with patch(
            "stage2b1_external_issuer.subprocess.run",
            side_effect=[
                response(completed_run()),
                response(artifact_metadata(self.artifact_digest)),
            ],
        ):
            return verify_p4_8_source_artifact(
                source_run_id=RUN_ID,
                source_run_attempt=RUN_ATTEMPT,
                artifact_id="7701",
                expected_head_sha=HEAD_SHA,
                expected_artifact_digest=self.artifact_digest,
            )

    def _external_proof(self):
        with patch(
            "stage2b1_external_issuer.subprocess.run",
            return_value=response(attestation(self.artifact_digest)),
        ):
            return verify_p4_8_payload_attestation(
                self.archive,
                source_run_id=RUN_ID,
                source_run_attempt=RUN_ATTEMPT,
                source_digest=HEAD_SHA,
                subject_digest=self.artifact_digest,
            )

    def test_reverify_reloads_exact_server_run_artifact_and_attestation(self) -> None:
        source = self._source_proof()
        proof = self._external_proof()
        with patch(
            "stage2b1_external_issuer.subprocess.run",
            side_effect=[
                response(completed_run()),
                response(artifact_metadata(self.artifact_digest)),
                response(attestation(self.artifact_digest)),
            ],
        ) as run_mock:
            result = reverify_external_issuer_proof(
                proof,
                self.archive,
                source_artifact_proof=source,
            )

        self.assertEqual(result, proof)
        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertEqual(
            commands[0],
            [
                "gh",
                "api",
                f"repos/{STAGE2B1_REPOSITORY}/actions/runs/{RUN_ID}/attempts/{RUN_ATTEMPT}",
            ],
        )
        self.assertEqual(
            commands[1],
            ["gh", "api", f"repos/{STAGE2B1_REPOSITORY}/actions/artifacts/7701"],
        )
        self.assertIn("--signer-workflow", commands[2])
        self.assertIn(STAGE2B1_SIGNER_WORKFLOW, commands[2])

    def test_verified_proofs_cannot_be_cloned_with_dataclass_replace(self) -> None:
        source = self._source_proof()
        with self.assertRaises(TypeError):
            replace(source, artifact_digest="sha256:" + "0" * 64)

        proof = self._external_proof()
        with self.assertRaises(TypeError):
            replace(proof, subject_digest="sha256:" + "0" * 64)

    def test_reverify_has_no_caller_policy_mapping_or_weak_invocation_path(self) -> None:
        source = self._source_proof()
        proof = self._external_proof()
        with self.assertRaises(TypeError):
            reverify_external_issuer_proof(
                proof,
                self.archive,
                expected=self.expected,
            )
        with self.assertRaises(TypeError):
            verify_github_artifact_attestation(
                self.archive,
                expected=self.expected,
            )
        with self.assertRaises(ExternalIssuerVerificationError):
            verify_github_artifact_attestation(
                self.archive,
                expected=self.expected,
                expected_runner_invocation="https://github.com/attacker/repo/actions/runs/901/attempts/2",
            )
        self.assertIsNotNone(source)

    def test_wrong_attempt_from_server_run_is_fail_closed(self) -> None:
        with patch(
            "stage2b1_external_issuer.subprocess.run",
            return_value=response(completed_run(attempt=1)),
        ):
            with self.assertRaisesRegex(
                ExternalIssuerVerificationError, "source_run_identity_mismatch"
            ):
                verify_p4_8_source_artifact(
                    source_run_id=RUN_ID,
                    source_run_attempt=RUN_ATTEMPT,
                    artifact_id="7701",
                    expected_head_sha=HEAD_SHA,
                    expected_artifact_digest=self.artifact_digest,
                )

    def test_missing_server_run_or_artifact_is_fail_closed(self) -> None:
        with patch(
            "stage2b1_external_issuer.subprocess.run", return_value=response(None)
        ):
            with self.assertRaisesRegex(
                ExternalIssuerVerificationError, "source_run_response_invalid"
            ):
                verify_p4_8_source_artifact(
                    source_run_id=RUN_ID,
                    source_run_attempt=RUN_ATTEMPT,
                    artifact_id="7701",
                    expected_head_sha=HEAD_SHA,
                    expected_artifact_digest=self.artifact_digest,
                )

        with patch(
            "stage2b1_external_issuer.subprocess.run",
            side_effect=[response(completed_run()), response(None)],
        ):
            with self.assertRaisesRegex(
                ExternalIssuerVerificationError, "artifact_metadata_invalid"
            ):
                verify_p4_8_source_artifact(
                    source_run_id=RUN_ID,
                    source_run_attempt=RUN_ATTEMPT,
                    artifact_id="7701",
                    expected_head_sha=HEAD_SHA,
                    expected_artifact_digest=self.artifact_digest,
                )

    def test_wrong_attestation_attempt_and_archive_bytes_are_fail_closed(self) -> None:
        with patch(
            "stage2b1_external_issuer.subprocess.run",
            return_value=response(attestation(self.artifact_digest, attempt=1)),
        ):
            with self.assertRaisesRegex(
                ExternalIssuerVerificationError, "attestation_matching_proof_count_invalid"
            ):
                verify_p4_8_payload_attestation(
                    self.archive,
                    source_run_id=RUN_ID,
                    source_run_attempt=RUN_ATTEMPT,
                    source_digest=HEAD_SHA,
                    subject_digest=self.artifact_digest,
                )

        self.archive.write_bytes(b"tampered archive")
        with self.assertRaisesRegex(
            ExternalIssuerVerificationError, "artifact_archive_digest_mismatch"
        ):
            verify_p4_8_payload_attestation(
                self.archive,
                source_run_id=RUN_ID,
                source_run_attempt=RUN_ATTEMPT,
                source_digest=HEAD_SHA,
                subject_digest=self.artifact_digest,
            )

    def test_duplicate_artifact_candidates_are_not_selected(self) -> None:
        selected = artifact_metadata(self.artifact_digest)
        duplicate = artifact_metadata("sha256:" + "0" * 64)
        duplicate["id"] = 7702
        with self.assertRaisesRegex(
            ValueError, "artifact_name_not_unique_for_run"
        ):
            with patch(
                "p4_8_evidence_producer._run_gh_json",
                side_effect=[
                    selected,
                    {"total_count": 2, "artifacts": [selected, duplicate]},
                ],
            ):
                _server_artifact(
                    identity={
                        "run_id": RUN_ID,
                        "run_attempt": RUN_ATTEMPT,
                        "head_sha": HEAD_SHA,
                    },
                    artifact_id="7701",
                )


if __name__ == "__main__":
    unittest.main()
