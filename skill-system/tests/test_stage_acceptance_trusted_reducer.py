from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from stage2b1_protected_human_gate import (  # noqa: E402
    Stage2B1ProtectedApprovalProof,
)
from stage2b1_external_issuer import (  # noqa: E402
    STAGE2B1_PREDICATE_TYPE,
    STAGE2B1_SIGNER_WORKFLOW,
    STAGE2B1_SOURCE_REF,
    verify_github_artifact_attestation,
)
from stage2b1_provenance import verify_artifact_provenance  # noqa: E402
from stage_acceptance_reducer import (  # noqa: E402
    ACCEPTABLE_PREVIEW,
    BLOCKED,
    TrustedStageAcceptanceVerificationInputs,
    _trusted_decision,
    reduce_stage_acceptance,
    reduce_trusted_stage_acceptance,
    reverify_trusted_stage_acceptance_decision,
    validate_trusted_stage_acceptance_decision,
)
from stage_evidence_receipt import build_stage_evidence_receipt  # noqa: E402


def verify_approval(gate, binding, github):
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
            "GITHUB_RUN_ID": "901",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_PROTECTED": "true",
            "GITHUB_SHA": "1" * 40,
            "GITHUB_WORKFLOW_REF": "toctionyan/fristTest/.github/workflows/governed-stage2b1-acceptance.yml@refs/heads/main",
        },
        clear=False,
    ), patch("stage2b1_protected_human_gate.subprocess.run", side_effect=run):
        from stage2b1_protected_human_gate import verify_stage2b1_protected_approval

        return verify_stage2b1_protected_approval(binding=binding)


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
                "workflow_path": ".github/workflows/p4-8-evidence-producer.yml",
                "workflow_id": 101,
                "event": "workflow_dispatch",
                "ref": "refs/heads/main",
                "head_sha": "c" * 40,
                "run_id": 901,
                "run_attempt": 2,
                "artifact": {
                    "id": "1001",
                    "name": "p4-8-evidence-payload-901-2",
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
                "artifact_name": "p4-8-evidence-payload-901-2",
                "artifact_digest": self.receipt["artifact"]["digest"],
                "content_digest": "sha256:" + "1" * 64,
                "repository": "toctionyan/fristTest",
                "workflow_path": ".github/workflows/p4-8-evidence-producer.yml",
                "workflow_id": 101,
                "event": "workflow_dispatch",
                "ref": "refs/heads/main",
                "head_sha": "c" * 40,
                "run_id": 901,
                "run_attempt": 2,
            },
        )
        self.gate = {
            "schema": "durable-human-gate@1",
            "gate_id": "gate-stage2b1-acceptance",
            "task_id": "stage2b1-task",
            "workflow_id": "governed-stage2b1-acceptance",
            "step_id": "stage2b1-acceptance",
            "question": "Approve the verified Stage2B1 environment deployment?",
            "waiting_outcome": "WAITING_FOR_PROTECTED_APPROVAL",
            "options": ["ACCEPT_STAGE2B1", "REJECT_STAGE2B1"],
            "routes": {
                "WAITING_FOR_PROTECTED_APPROVAL": "HUMAN_GATE",
                "ACCEPT_STAGE2B1": "STAGE_ACCEPTANCE",
                "REJECT_STAGE2B1": "STAGE_REJECTION",
            },
            "authority_effect": False,
        }
        self.gate["gate_sha256"] = hashlib.sha256(
            json.dumps(self.gate, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.approval_evidence_bindings = [
            {
                "receipt_id": "1001",
                "artifact_id": "1001",
                "artifact_digest": self.receipt["artifact"]["digest"],
                "run_id": 901,
                "run_attempt": 2,
            }
        ]
        self.approval_binding = {
            **self.binding,
            "evidence_bindings": self.approval_evidence_bindings,
        }
        self.approval_expected = {
            **self.binding,
            "gate_id": self.gate["gate_id"],
            "gate_sha256": self.gate["gate_sha256"],
            "task_id": self.gate["task_id"],
            "repository": "toctionyan/fristTest",
            "workflow_id": "governed-stage2b1-acceptance",
            "workflow_path": ".github/workflows/governed-stage2b1-acceptance.yml",
            "workflow_ref": "toctionyan/fristTest/.github/workflows/governed-stage2b1-acceptance.yml@refs/heads/main",
            "ref": "refs/heads/main",
            "head_sha": "1" * 40,
            "run_id": 901,
            "run_attempt": 2,
            "environment": "stage2b1-acceptance",
            "environment_id": 303,
            "reviewer_login": "independent-reviewer",
            "reviewer_id": 505,
            "run_actor_login": "toctionyan",
            "run_actor_id": 606,
            "status": "approved",
            "evidence_bindings": self.approval_evidence_bindings,
        }
        self.github = {
            "repos/toctionyan/fristTest/actions/runs/901/attempts/2": {
                "id": 901,
                "run_attempt": 2,
                "name": "governed-stage2b1-acceptance",
                "repository": {"full_name": "toctionyan/fristTest"},
                "path": ".github/workflows/governed-stage2b1-acceptance.yml",
                "event": "workflow_dispatch",
                "head_branch": "main",
                "head_sha": "1" * 40,
                "run_started_at": "2026-08-28T00:00:00Z",
                "actor": {"login": "toctionyan", "id": 606},
            },
            "repos/toctionyan/fristTest/environments/stage2b1-acceptance": {
                "id": 303,
                "name": "stage2b1-acceptance",
                "protection_rules": [{
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [{
                        "type": "User",
                        "reviewer": {"login": "independent-reviewer", "id": 505},
                    }],
                }],
            },
            "repos/toctionyan/fristTest/actions/runs/901/pending_deployments": [],
            "repos/toctionyan/fristTest/actions/runs/901/approvals": [{
                "state": "approved",
                "user": {"login": "independent-reviewer", "id": 505},
                "environments": [{
                    "id": 303,
                    "name": "stage2b1-acceptance",
                    "updated_at": "2026-08-28T01:00:00Z",
                }],
            }],
            "repos/toctionyan/fristTest/deployments?environment=stage2b1-acceptance&per_page=100": [{
                "id": 404,
                "environment": "stage2b1-acceptance",
                "sha": "1" * 40,
                "ref": "main",
                "created_at": "2026-08-28T00:30:00Z",
                "updated_at": "2026-08-28T00:30:00Z",
            }],
            "repos/toctionyan/fristTest/deployments/404/statuses?per_page=100": [{
                "id": 405,
                "state": "in_progress",
                "created_at": "2026-08-28T00:30:00Z",
                "updated_at": "2026-08-28T00:30:00Z",
            }],
        }
        self.approval = verify_approval(self.gate, self.approval_binding, self.github)
        self.approval_expected["approval_sha256"] = self.approval.as_dict()["approval_sha256"]
        self.external_expected = {
            "repository": "toctionyan/fristTest",
            "signer_workflow": STAGE2B1_SIGNER_WORKFLOW,
            "subject_digest": self.receipt["artifact"]["digest"],
            "predicate_type": STAGE2B1_PREDICATE_TYPE,
            "source_digest": "c" * 40,
            "source_ref": STAGE2B1_SOURCE_REF,
        }
        attestation_output = [
            {
                "verificationResult": {
                    "statement": {
                        "predicateType": self.external_expected["predicate_type"],
                        "subject": [{"name": "p4-8-evidence-payload-901-2", "digest": {"sha256": "f" * 64}}],
                    },
                    "signature": {"certificate": {
                        "issuer": "github",
                        "runInvocationURI": "https://github.com/toctionyan/fristTest/actions/runs/901/attempts/2",
                    }},
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
            ) as run_mock:
                self.external = verify_github_artifact_attestation(
                    artifact_file.name,
                    expected=self.external_expected,
                    expected_runner_invocation="https://github.com/toctionyan/fristTest/actions/runs/901/attempts/2",
                )
                command = run_mock.call_args.args[0]
                self.assertIn("--source-digest", command)
                self.assertIn("--source-ref", command)
                self.assertIn(self.external_expected["signer_workflow"], command)

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

    def test_reducer_only_accepts_the_new_sealed_proof(self) -> None:
        self.assertIsInstance(self.approval, Stage2B1ProtectedApprovalProof)
        source = Path(__file__).resolve().parents[1].joinpath(
            "controller", "stage_acceptance_reducer.py"
        ).read_text(encoding="utf-8")
        self.assertIn("stage2b1_protected_human_gate", source)
        self.assertNotIn("stage2b1_protected_approval import", source)
        self.assertNotIn("VerifiedProtectedApproval", source)

    def test_only_fixed_verified_objects_can_produce_preview(self) -> None:
        decision = self._reduce(
            provenance={"1001": self.provenance},
            external={"1001": self.external},
            approval=self.approval,
        )
        self.assertEqual(decision["status"], ACCEPTABLE_PREVIEW)
        self.assertEqual(validate_trusted_stage_acceptance_decision(decision), decision)
        self.assertGreaterEqual(len(decision["proof_refs"]), 2)

    def test_protected_approval_cannot_be_cloned_with_dataclass_replace(self) -> None:
        with self.assertRaises(TypeError):
            replace(self.approval, reviewer_login="attacker")

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
        self.assertIn(
            "protected_approval",
            TrustedStageAcceptanceVerificationInputs.__dataclass_fields__,
        )

    def test_provenance_cannot_be_spliced_from_another_control_plane(self) -> None:
        wrong_observation = {
            "schema": "stage2b1-github-provenance@1",
            "repository": "toctionyan/fristTest",
            "workflow_path": ".github/workflows/p4-8-evidence-producer.yml",
            "workflow_id": 101,
            "event": "workflow_dispatch",
            "ref": "refs/heads/main",
            "head_sha": "e" * 40,
            "run_id": 901,
            "run_attempt": 2,
            "artifact": {
                "id": "1001",
                "name": "p4-8-evidence-payload-901-2",
                "digest": self.receipt["artifact"]["digest"],
                "archive_digest": self.receipt["artifact"]["digest"],
                "content_digest": "sha256:" + "1" * 64,
                "source_run_id": 901,
                "source_run_attempt": 2,
            },
        }
        wrong_provenance = verify_artifact_provenance(
            wrong_observation,
            expected={
                **{
                    "receipt_id": "1001",
                    "artifact_id": "1001",
                    "artifact_name": "p4-8-evidence-payload-901-2",
                    "artifact_digest": self.receipt["artifact"]["digest"],
                    "content_digest": "sha256:" + "1" * 64,
                    "repository": "toctionyan/fristTest",
                    "workflow_path": ".github/workflows/p4-8-evidence-producer.yml",
                    "workflow_id": 101,
                    "event": "workflow_dispatch",
                    "ref": "refs/heads/main",
                    "head_sha": "e" * 40,
                    "run_id": 901,
                    "run_attempt": 2,
                }
            },
        )
        decision = self._reduce(
            provenance={"1001": wrong_provenance},
            external={"1001": self.external},
            approval=self.approval,
        )
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn(
            "trusted_provenance_control_plane_mismatch:1001",
            decision["reasons"],
        )

    def test_external_attestation_cannot_be_spliced_from_another_source(self) -> None:
        wrong_expected = dict(self.external_expected, source_digest="e" * 40)
        output = [
            {
                "verificationResult": {
                    "statement": {
                        "predicateType": wrong_expected["predicate_type"],
                        "subject": [{"name": "p4-8-evidence-payload-901-2", "digest": {"sha256": "f" * 64}}],
                    },
                    "signature": {"certificate": {
                        "issuer": "github",
                        "runInvocationURI": "https://github.com/toctionyan/fristTest/actions/runs/901/attempts/2",
                    }},
                    "verifiedTimestamps": [{"timestamp": "2026-08-28T00:00:00Z"}],
                }
            }
        ]
        with tempfile.NamedTemporaryFile() as artifact_file:
            artifact_file.write(b"verified artifact")
            artifact_file.flush()
            with patch(
                "stage2b1_external_issuer.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=json.dumps(output)),
            ):
                wrong_external = verify_github_artifact_attestation(
                    artifact_file.name,
                    expected=wrong_expected,
                    expected_runner_invocation="https://github.com/toctionyan/fristTest/actions/runs/901/attempts/2",
                )
        decision = self._reduce(
            provenance={"1001": self.provenance},
            external={"1001": wrong_external},
            approval=self.approval,
        )
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn(
            "external_issuer_fixed_policy_mismatch:1001:source_digest",
            decision["reasons"],
        )

    def test_protected_approval_cannot_be_replayed_for_another_artifact_scope(self) -> None:
        wrong_scope = {
            **self.binding,
            "evidence_bindings": [
                {
                    "receipt_id": "1001",
                    "artifact_id": "1001",
                    "artifact_digest": "sha256:" + "0" * 64,
                    "run_id": 901,
                    "run_attempt": 2,
                }
            ],
        }
        wrong_approval = verify_approval(self.gate, wrong_scope, self.github)
        decision = self._reduce(
            provenance={"1001": self.provenance},
            external={"1001": self.external},
            approval=wrong_approval,
        )
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn(
            "trusted_protected_approval_evidence_binding_mismatch",
            decision["reasons"],
        )

    def test_receipt_order_does_not_change_a_blocked_decision(self) -> None:
        changed = build_stage_evidence_receipt(
            **self.binding,
            workflow_run_attempt={"run_id": 901, "attempt": 2},
            artifact={"id": "1001", "digest": "sha256:" + "0" * 64},
            result="PASS",
            producer="quality",
            policy="stage2b1-p3-evidence-receipt@1",
        )
        kwargs = {
            "required_receipt_ids": ["1001"],
            **self.binding,
            "expected_receipt_bindings": self.expected_receipt_bindings,
        }
        first = reduce_stage_acceptance([self.receipt, changed], **kwargs)
        second = reduce_stage_acceptance([changed, self.receipt], **kwargs)
        self.assertEqual(first, second)

    def test_legacy_json_human_decision_projection_is_removed(self) -> None:
        import stage2b1_protected_human_gate as gate_module

        self.assertFalse(hasattr(gate_module, "project_stage2b1_decision"))

    def test_write_boundary_recomputes_fixed_evidence_and_rejects_self_signed_decision(self) -> None:
        observation = {
            "schema": "stage2b1-github-provenance@1",
            "repository": "toctionyan/fristTest",
            "workflow_path": ".github/workflows/p4-8-evidence-producer.yml",
            "workflow_id": 101,
            "event": "workflow_dispatch",
            "ref": "refs/heads/main",
            "head_sha": "c" * 40,
            "run_id": 901,
            "run_attempt": 2,
            "artifact": {
                "id": "1001",
                "name": "p4-8-evidence-payload-901-2",
                "digest": self.receipt["artifact"]["digest"],
                "archive_digest": self.receipt["artifact"]["digest"],
                "content_digest": "sha256:" + "1" * 64,
                "source_run_id": 901,
                "source_run_attempt": 2,
            },
        }
        expected_provenance = {
            "receipt_id": "1001",
            "artifact_id": "1001",
            "artifact_name": "p4-8-evidence-payload-901-2",
            "artifact_digest": self.receipt["artifact"]["digest"],
            "content_digest": "sha256:" + "1" * 64,
            "repository": "toctionyan/fristTest",
            "workflow_path": ".github/workflows/p4-8-evidence-producer.yml",
            "workflow_id": 101,
            "event": "workflow_dispatch",
            "ref": "refs/heads/main",
            "head_sha": "c" * 40,
            "run_id": 901,
            "run_attempt": 2,
        }
        verification = TrustedStageAcceptanceVerificationInputs(
            receipts=(self.receipt,),
            required_receipt_ids=("1001",),
            expected_receipt_bindings=self.expected_receipt_bindings,
            provenance_observations={"1001": observation},
            expected_provenance={"1001": expected_provenance},
            attested_artifact_paths={"1001": "/tmp/p4-8-attested-payload"},
            expected_external_issuer_bindings={"1001": self.external_expected},
            protected_approval=self.approval,
            expected_protected_approval=self.approval_expected,
        )
        source_proof = SimpleNamespace(
            artifact_id="1001",
            artifact_digest=self.receipt["artifact"]["digest"],
            source_run_id=901,
            source_run_attempt=2,
            source_head_sha="c" * 40,
        )
        decision = self._reduce(
            provenance={"1001": self.provenance},
            external={"1001": self.external},
            approval=self.approval,
        )
        with patch(
            "stage_acceptance_reducer.verify_p4_8_source_artifact",
            return_value=source_proof,
        ) as source_verify, patch(
            "stage_acceptance_reducer.verify_p4_8_payload_attestation",
            return_value=self.external,
        ) as attestation_verify, patch(
            "stage_acceptance_reducer.reverify_stage2b1_protected_approval",
            return_value=self.approval,
        ) as gate_verify:
            result = reverify_trusted_stage_acceptance_decision(
                decision,
                verification=verification,
                common_binding=self.binding,
            )
        self.assertEqual(result.as_dict(), decision.as_dict())
        source_verify.assert_called_once()
        attestation_verify.assert_called_once()
        gate_verify.assert_called_once()
        self.assertEqual(
            gate_verify.call_args.kwargs["binding"]["evidence_bindings"],
            self.approval_evidence_bindings,
        )

        forged = _trusted_decision(
            input_digest=decision["input_digest"],
            status=decision["status"],
            reasons=["forged"],
            receipt_refs=decision["receipt_refs"],
            proof_refs=decision["proof_refs"],
            binding=decision["binding"],
        )
        with patch(
            "stage_acceptance_reducer.verify_p4_8_source_artifact",
            return_value=source_proof,
        ), patch(
            "stage_acceptance_reducer.verify_p4_8_payload_attestation",
            return_value=self.external,
        ), patch(
            "stage_acceptance_reducer.reverify_stage2b1_protected_approval",
            return_value=self.approval,
        ):
            with self.assertRaisesRegex(ValueError, "reverification_mismatch"):
                reverify_trusted_stage_acceptance_decision(
                    forged,
                    verification=verification,
                    common_binding=self.binding,
                )

    def test_decision_tampering_fails_validation(self) -> None:
        decision = self._reduce(
            provenance={"1001": self.provenance},
            external={"1001": self.external},
            approval=self.approval,
        )
        changed = copy.deepcopy(decision.as_dict())
        changed["proof_refs"] = []
        with self.assertRaises(ValueError):
            validate_trusted_stage_acceptance_decision(changed)

    def test_missing_external_issuer_proof_is_blocked(self) -> None:
        decision = self._reduce(provenance={"1001": self.provenance}, approval=self.approval)
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn("external_issuer_proof_required", decision["reasons"])

    def test_external_issuer_policy_cannot_be_injected(self) -> None:
        with tempfile.NamedTemporaryFile() as artifact_file:
            artifact_file.write(b"verified artifact")
            artifact_file.flush()
            expected = dict(self.external_expected)
            expected["signer_workflow"] = "toctionyan/fristTest/.github/workflows/quality.yml"
            with patch(
                "stage2b1_external_issuer.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout="[]"),
            ):
                with self.assertRaisesRegex(ValueError, "signer_workflow_not_stage2b1_policy"):
                    verify_github_artifact_attestation(
                        artifact_file.name,
                        expected=expected,
                        expected_runner_invocation="https://github.com/toctionyan/fristTest/actions/runs/901/attempts/2",
                    )

    def test_external_binding_map_cannot_contain_unrequired_receipt(self) -> None:
        with_extra = dict(self.external_expected)
        result = reduce_trusted_stage_acceptance(
            [self.receipt],
            required_receipt_ids=["1001"],
            **self.binding,
            expected_receipt_bindings=self.expected_receipt_bindings,
            verified_provenance={"1001": self.provenance},
            verified_external_issuers={"1001": self.external},
            expected_external_issuer_bindings={
                "1001": with_extra,
                "attacker-receipt": with_extra,
            },
            verified_protected_approval=self.approval,
            expected_protected_approval=self.approval_expected,
        )
        self.assertEqual(result["status"], BLOCKED)
        self.assertIn(
            "expected_external_issuer_unexpected:attacker-receipt",
            result["reasons"],
        )

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

    def test_legacy_verified_approval_object_is_blocked(self) -> None:
        decision = self._reduce(
            provenance={"1001": self.provenance},
            external={"1001": self.external},
            approval=object(),
        )
        self.assertEqual(decision["status"], BLOCKED)
        self.assertIn("trusted_protected_approval_required", decision["reasons"])


if __name__ == "__main__":
    unittest.main()
