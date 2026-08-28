from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
for path in (CONTROLLER, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from p4_8_evidence_producer import finalize_bundle, produce_payload  # noqa: E402
import stage2b1_protected_human_gate as protected_gate  # noqa: E402
from stage2b1_acceptance import (  # noqa: E402
    Stage2B1AcceptanceCommandError,
    record_stage_acceptance,
)


def _archive_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


class Stage2B1AcceptanceCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="stage2b1-producer-preview-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.payload = self.root / "producer" / "payload"
        self.bundle = self.root / "producer" / "bundle"
        self.archive = self.root / "producer" / "payload.zip"
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(b"the exact downloaded GitHub artifact archive")
        self.archive_digest = _archive_digest(self.archive.read_bytes())
        registry = self.root / "skill-system/registry/product-source-baseline.json"
        registry.parent.mkdir(parents=True)
        registry.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "snapshot_format": "protected-git-tree@1",
                    "product_source_ref": "git-commit-sha1:" + "b" * 40,
                    "protected_roots": ["contracts", "services", "web"],
                    "entry_count": 0,
                    "entries": {},
                    "protected_snapshot_digest": "sha256:"
                    + hashlib.sha256(
                        json.dumps(
                            {
                                "entries": {},
                                "protected_roots": ["contracts", "services", "web"],
                                "snapshot_format": "protected-git-tree@1",
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        self.run = {
            "id": 901,
            "run_attempt": 2,
            "workflow_id": 77,
            "name": "p4-8-evidence-producer",
            "path": ".github/workflows/p4-8-evidence-producer.yml",
            "event": "workflow_dispatch",
            "head_sha": "a" * 40,
            "head_branch": "main",
            "status": "in_progress",
            "repository": {"full_name": "toctionyan/fristTest"},
            "head_repository": {"full_name": "toctionyan/fristTest"},
        }
        self.artifact = {
            "id": 7701,
            "name": "p4-8-evidence-payload-901-2",
            "digest": self.archive_digest,
            "expired": False,
            "workflow_run": {
                "id": 901,
                "run_attempt": 2,
                "head_branch": "main",
                "head_sha": "a" * 40,
            },
        }
        context = {
            "GITHUB_REPOSITORY": "toctionyan/fristTest",
            "GITHUB_WORKFLOW": "p4-8-evidence-producer",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_PROTECTED": "true",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "901",
            "GITHUB_RUN_ATTEMPT": "2",
        }
        with patch(
            "p4_8_evidence_producer._server_run", return_value=self.run
        ), patch(
            "p4_8_evidence_producer._server_artifact", return_value=self.artifact
        ):
            produce_payload(
                workspace=self.root,
                output=self.payload,
                environ=context,
            )
            finalize_bundle(
                payload=self.payload,
                output=self.bundle,
                environ=context,
                artifact_id="7701",
                upload_artifact_digest=self.archive_digest,
                downloaded_artifact=self.archive,
            )
        self.approval = self._build_protected_approval()

    def _build_protected_approval(self):
        binding = json.loads(
            (self.bundle / "product-binding.json").read_text(encoding="utf-8")
        )
        binding["evidence_bindings"] = [
            {
                "receipt_id": "7701",
                "artifact_id": "7701",
                "artifact_digest": self.archive_digest,
                "run_id": 901,
                "run_attempt": 2,
            }
        ]
        gate = protected_gate._gate()
        github = {
            "repos/toctionyan/fristTest/actions/runs/902/attempts/1": {
                "id": 902,
                "run_attempt": 1,
                "name": "governed-stage2b1-acceptance",
                "repository": {"full_name": "toctionyan/fristTest"},
                "path": ".github/workflows/governed-stage2b1-acceptance.yml",
                "event": "workflow_dispatch",
                "head_branch": "main",
                "head_sha": "b" * 40,
                "run_started_at": "2026-08-28T00:00:00Z",
                "actor": {"login": "toctionyan", "id": 606},
            },
            "repos/toctionyan/fristTest/environments/stage2b1-acceptance": {
                "id": 303,
                "name": "stage2b1-acceptance",
                "protection_rules": [
                    {
                        "type": "required_reviewers",
                        "prevent_self_review": True,
                        "reviewers": [
                            {
                                "type": "User",
                                "reviewer": {
                                    "login": "independent-reviewer",
                                    "id": 505,
                                },
                            }
                        ],
                    }
                ],
            },
            "repos/toctionyan/fristTest/actions/runs/902/pending_deployments": [],
            "repos/toctionyan/fristTest/actions/runs/902/approvals": [
                {
                    "state": "approved",
                    "user": {"login": "independent-reviewer", "id": 505},
                    "environments": [
                        {
                            "id": 303,
                            "name": "stage2b1-acceptance",
                            "updated_at": "2026-08-28T01:00:00Z",
                        }
                    ],
                }
            ],
        }

        def run(command, **_kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(github[command[-1]]),
                stderr="",
            )

        with patch.dict(
            os.environ,
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_REPOSITORY": "toctionyan/fristTest",
                "GITHUB_RUN_ID": "902",
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_REF_PROTECTED": "true",
                "GITHUB_SHA": "b" * 40,
                "GITHUB_WORKFLOW_REF": (
                    "toctionyan/fristTest/.github/workflows/"
                    "governed-stage2b1-acceptance.yml@refs/heads/main"
                ),
            },
            clear=False,
        ), patch(
            "stage2b1_protected_human_gate.subprocess.run", side_effect=run
        ):
            return protected_gate.verify_stage2b1_protected_approval(binding=binding)

    def _api_responses(self, attestation: bool = True) -> list[SimpleNamespace]:
        completed_run = {
            **self.run,
            "status": "completed",
            "conclusion": "success",
        }
        attestation_output = [
            {
                "verificationResult": {
                    "statement": {
                        "predicateType": "https://slsa.dev/provenance/v1",
                        "subject": [
                            {
                                "name": self.artifact["name"],
                                "digest": {"sha256": self.archive_digest.removeprefix("sha256:")},
                            }
                        ],
                    },
                    "signature": {"certificate": {
                        "issuer": "github",
                        "runnerInvocationURI": "https://github.com/toctionyan/fristTest/actions/runs/901/attempts/2",
                    }},
                    "verifiedTimestamps": [{"timestamp": "2026-08-28T00:00:00Z"}],
                }
            }
        ]
        values = [
            completed_run,
            self.artifact,
        ]
        if attestation:
            values.append(attestation_output)
        return [SimpleNamespace(returncode=0, stdout=json.dumps(value)) for value in values]

    def _record(
        self,
        *,
        api_responses: list[SimpleNamespace] | None = None,
        approval: object | None = None,
        **overrides: object,
    ) -> dict[str, object]:
        values: dict[str, object] = {
            "workspace": self.root,
            "producer_bundle_path": self.bundle,
            "artifact_archive_path": self.archive,
            "source_run_id": 901,
            "source_run_attempt": 2,
            "artifact_id": "7701",
        }
        values.update(overrides)
        with patch(
            "stage2b1_external_issuer.subprocess.run",
            side_effect=api_responses or self._api_responses(),
        ), patch(
            "stage2b1_acceptance.verify_stage2b1_protected_approval",
            return_value=self.approval if approval is None else approval,
        ), patch.dict(
            os.environ,
            {"GITHUB_ACTIONS": "false"},
            clear=False,
        ):
            return record_stage_acceptance(**values)  # type: ignore[arg-type]

    def test_real_producer_bundle_returns_read_only_preview(self) -> None:
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result = self._record()
        self.assertEqual(result["status"], "ACCEPTABLE_PREVIEW")
        self.assertEqual(result["preview_kind"], "p4-8-trusted-acceptance")
        self.assertEqual(result["trusted_receipt"]["schema"], "stage-evidence-receipt@1")
        self.assertEqual(result["trusted_receipt"]["result"], "PASS")
        self.assertEqual(result["trusted_reducer"]["status"], "ACCEPTABLE_PREVIEW")
        self.assertEqual(result["protected_approval"]["status"], "approved")
        self.assertFalse(result["active_change_written"])
        self.assertFalse(result["task_run_written"])
        self.assertFalse(result["governance_state_changed"])
        self.assertFalse(result["authority_effect"])
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_caller_decision_receipt_and_gate_inputs_are_not_an_interface(self) -> None:
        with self.assertRaises(TypeError):
            self._record(decision_path=self.root / "decision.json")

    def test_old_cli_flags_are_rejected(self) -> None:
        with patch("sys.argv", ["stage2b1_acceptance.py", "--workspace", str(self.root), "--decision", "x"]):
            from stage2b1_acceptance import main

            with self.assertRaises(SystemExit):
                main()

    def test_source_run_selector_must_match_producer_bundle(self) -> None:
        with self.assertRaisesRegex(Stage2B1AcceptanceCommandError, "explicit_source_selector_mismatch"):
            self._record(source_run_attempt=1)

    def test_binding_refs_must_match_the_server_owned_producer_head(self) -> None:
        path = self.bundle / "product-binding.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["execution_repo_ref"] = "git-commit-sha1:" + "c" * 40
        path.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            Stage2B1AcceptanceCommandError,
            "execution_repo_ref_not_server_owned",
        ):
            self._record()

    def test_unissued_protected_approval_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            Stage2B1AcceptanceCommandError,
            "trusted_protected_approval_invalid",
        ):
            self._record(approval=object())

    def test_source_api_must_be_exact_completed_p4_8_attempt(self) -> None:
        responses = self._api_responses()
        bad_run = json.loads(responses[0].stdout)
        bad_run["run_attempt"] = 1
        responses[0] = SimpleNamespace(returncode=0, stdout=json.dumps(bad_run))
        with patch("stage2b1_external_issuer.subprocess.run", side_effect=responses), patch.dict(
            os.environ, {"GITHUB_ACTIONS": "false"}, clear=False
        ):
            with self.assertRaisesRegex(Stage2B1AcceptanceCommandError, "producer_external_proof_invalid"):
                record_stage_acceptance(
                    workspace=self.root,
                    producer_bundle_path=self.bundle,
                    artifact_archive_path=self.archive,
                    source_run_id=901,
                    source_run_attempt=2,
                    artifact_id="7701",
                )

    def test_artifact_api_may_omit_run_attempt(self) -> None:
        responses = self._api_responses()
        artifact = json.loads(responses[1].stdout)
        artifact["workflow_run"].pop("run_attempt")
        responses[1] = SimpleNamespace(returncode=0, stdout=json.dumps(artifact))
        result = self._record(api_responses=responses)
        self.assertEqual(result["status"], "ACCEPTABLE_PREVIEW")

    def test_attestation_must_bind_exact_source_run_attempt(self) -> None:
        responses = self._api_responses()
        attestation = json.loads(responses[2].stdout)
        attestation[0]["verificationResult"]["signature"]["certificate"][
            "runnerInvocationURI"
        ] = "https://github.com/toctionyan/fristTest/actions/runs/901/attempts/1"
        responses[2] = SimpleNamespace(returncode=0, stdout=json.dumps(attestation))
        with patch("stage2b1_external_issuer.subprocess.run", side_effect=responses), patch.dict(
            os.environ, {"GITHUB_ACTIONS": "false"}, clear=False
        ):
            with self.assertRaisesRegex(Stage2B1AcceptanceCommandError, "producer_external_proof_invalid"):
                self._record(api_responses=responses)

    def test_archive_bytes_must_match_platform_subject(self) -> None:
        self.archive.write_bytes(b"different archive")
        with self.assertRaisesRegex(Stage2B1AcceptanceCommandError, "artifact_archive_digest_mismatch"):
            self._record()

    def test_attestation_subject_is_checked_against_actual_archive(self) -> None:
        responses = self._api_responses()
        attestation = json.loads(responses[2].stdout)
        attestation[0]["verificationResult"]["statement"]["subject"][0]["digest"]["sha256"] = "0" * 64
        responses[2] = SimpleNamespace(returncode=0, stdout=json.dumps(attestation))
        with patch("stage2b1_external_issuer.subprocess.run", side_effect=responses), patch.dict(
            os.environ, {"GITHUB_ACTIONS": "false"}, clear=False
        ):
            with self.assertRaisesRegex(Stage2B1AcceptanceCommandError, "producer_external_proof_invalid"):
                record_stage_acceptance(
                    workspace=self.root,
                    producer_bundle_path=self.bundle,
                    artifact_archive_path=self.archive,
                    source_run_id=901,
                    source_run_attempt=2,
                    artifact_id="7701",
                )

    def test_bundle_provenance_tampering_fails_closed(self) -> None:
        path = self.bundle / "provenance.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["head_sha"] = "f" * 40
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        with self.assertRaisesRegex(Stage2B1AcceptanceCommandError, "producer_provenance_invalid"):
            self._record()

    def test_product_binding_must_match_trusted_registry(self) -> None:
        path = self.bundle / "product-binding.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["product_source_ref"] = "git-commit-sha1:" + "d" * 40
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        with self.assertRaisesRegex(
            Stage2B1AcceptanceCommandError,
            "product_binding_does_not_match_trusted_baseline",
        ):
            self._record()


if __name__ == "__main__":
    unittest.main()
