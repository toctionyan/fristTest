from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
ROOT = Path(__file__).resolve().parents[2]
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from stage_acceptance_reducer import (  # noqa: E402
    TrustedStageAcceptanceDecision,
    _trusted_decision,
    reduce_stage_acceptance,
)
from stage_acceptance_taskrun import project_stage_acceptance_to_taskrun  # noqa: E402
from stage_acceptance_writer import (  # noqa: E402
    STAGE_ACCEPTED_CONDITION,
    StageAcceptanceWriteError,
    contract_digest,
    write_stage_acceptance,
)
from stage_evidence_receipt import build_stage_evidence_receipt  # noqa: E402
from task_run import TaskRunStore  # noqa: E402


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
            "GITHUB_RUN_ID": "17",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_PROTECTED": "true",
            "GITHUB_SHA": "1" * 40,
            "GITHUB_WORKFLOW_REF": "toctionyan/fristTest/.github/workflows/governed-stage2b1-acceptance.yml@refs/heads/main",
        },
        clear=False,
    ), patch("stage2b1_protected_human_gate.subprocess.run", side_effect=run):
        from stage2b1_protected_human_gate import verify_stage2b1_protected_approval

        return verify_stage2b1_protected_approval(binding=binding)


class StageAcceptanceWriterTests(unittest.TestCase):
    binding = {
        "stage_id": "stage2b1",
        "accepted_state_id": "accepted-state-17",
        "product_source_ref": "git-commit-sha1:" + "a" * 40,
        "protected_snapshot_digest": "sha256:" + "b" * 64,
        "control_plane_ref": "git-commit-sha1:" + "c" * 40,
        "execution_repo_ref": "git-commit-sha1:" + "d" * 40,
    }

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="stage-acceptance-writer-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.store = TaskRunStore.open_or_create(
            self.root / "task-run.json",
            task_id="stage2b1-task",
            task_kind="stage-acceptance",
            binding=self.binding,
            required_conditions=["stage-accepted", "quality-green"],
            current_workspace_fingerprint="workspace-1",
        )
        self.contract = json.loads(
            (ROOT / "governance" / "active-change.json").read_text(encoding="utf-8")
        )
        self.contract_digest = contract_digest(self.contract)
        self.verification = object()
        gate = {
            "schema": "durable-human-gate@1",
            "gate_id": "gate-stage2b1-acceptance",
            "task_id": self.store.payload["task_id"],
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
        gate["gate_sha256"] = hashlib.sha256(
            json.dumps(
                gate,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.gate = gate
        github = {
            "repos/toctionyan/fristTest/actions/runs/17/attempts/1": {
                "id": 17,
                "run_attempt": 1,
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
            "repos/toctionyan/fristTest/actions/runs/17/pending_deployments": [],
            "repos/toctionyan/fristTest/actions/runs/17/approvals": [{
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
        self.github = github
        self.approval_evidence_bindings = [
            {
                "receipt_id": "artifact-1",
                "artifact_id": "artifact-1",
                "artifact_digest": "sha256:" + "e" * 64,
                "run_id": 17,
                "run_attempt": 1,
            }
        ]
        self.approval_binding = {
            **self.binding,
            "evidence_bindings": self.approval_evidence_bindings,
        }
        self.approval = verify_approval(gate, self.approval_binding, github)
        receipt = build_stage_evidence_receipt(
            **self.binding,
            workflow_run_attempt={"run_id": 17, "attempt": 1},
            artifact={"id": "artifact-1", "digest": "sha256:" + "e" * 64},
            result="PASS",
            producer="quality",
            policy="stage2b1-p3-evidence-receipt@1",
        )
        self.raw_decision = reduce_stage_acceptance(
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
        self.decision = trusted_decision(
            self.raw_decision,
            proof_refs=[
                "provenance:test",
                "external-issuer:test",
                "protected-approval:" + self.approval.as_dict()["approval_sha256"],
            ],
            binding={
                "common": dict(self.binding),
                "task_id": self.store.payload["task_id"],
                "receipts": [],
                "provenance": [],
                "external_issuer": [],
                "protected_approval": {
                    "approval_sha256": self.approval.as_dict()["approval_sha256"],
                    "gate_id": self.gate["gate_id"],
                    "task_id": self.store.payload["task_id"],
                    "run_id": 17,
                    "run_attempt": 1,
                    "evidence_bindings": self.approval_evidence_bindings,
                },
            },
        )
        with patch(
            "stage_acceptance_taskrun.reverify_trusted_stage_acceptance_decision",
            return_value=self.decision,
        ):
            project_stage_acceptance_to_taskrun(
                self.store,
                self.decision,
                expected_binding=self.binding,
                workspace_fingerprint="workspace-1",
                verification=self.verification,
            )

    def write(self) -> dict[str, object]:
        with patch(
            "stage_acceptance_writer.reverify_trusted_stage_acceptance_decision",
            return_value=self.decision,
        ):
            return write_stage_acceptance(
                self.store,
                self.decision,
                expected_binding=self.binding,
                change_contract=self.contract,
                change_contract_digest=self.contract_digest,
                verification=self.verification,
            )

    def approval_for_task(self, task_id: str):
        del task_id
        return self.approval

    def test_records_existing_condition_without_completing_task(self) -> None:
        result = self.write()
        self.assertEqual(result["status"], "RECORDED")
        self.assertTrue(self.store.payload["conditions"][STAGE_ACCEPTED_CONDITION]["satisfied"])
        self.assertFalse(self.store.completion_decision().eligible)
        self.assertEqual(self.store.payload["status"], "RUNNING")
        self.assertFalse(result["active_change_written"])
        self.assertFalse(result["governance_state_changed"])

    def test_repeat_is_idempotent_without_revision_change(self) -> None:
        first = self.write()
        revision = self.store.payload["revision"]
        second = self.write()
        self.assertEqual(first, second)
        self.assertEqual(self.store.payload["revision"], revision)

    def test_selects_matching_preview_instead_of_last_checkpoint(self) -> None:
        preview = copy.deepcopy(
            next(
                checkpoint
                for checkpoint in self.store.payload["checkpoints"]
                if isinstance(checkpoint, dict)
                and isinstance(checkpoint.get("metadata"), dict)
                and checkpoint["metadata"].get("stage_acceptance_decision_id")
                == self.decision["decision_id"]
            )
        )
        unrelated = copy.deepcopy(preview)
        unrelated["metadata"]["stage_acceptance_decision_id"] = "other-decision"
        self.store.payload["checkpoints"].append(unrelated)
        result = self.write()
        self.assertEqual(result["status"], "RECORDED")

    def test_duplicate_matching_previews_are_ambiguous(self) -> None:
        preview = next(
            checkpoint
            for checkpoint in self.store.payload["checkpoints"]
            if isinstance(checkpoint, dict)
            and isinstance(checkpoint.get("metadata"), dict)
            and checkpoint["metadata"].get("stage_acceptance_decision_id")
            == self.decision["decision_id"]
        )
        self.store.payload["checkpoints"].append(
            copy.deepcopy(preview)
        )
        with self.assertRaisesRegex(StageAcceptanceWriteError, "ambiguous"):
            self.write()

    def test_legacy_v1_decision_is_rejected_without_mutation(self) -> None:
        before = copy.deepcopy(self.store.payload)
        with self.assertRaisesRegex(StageAcceptanceWriteError, "decision is invalid"):
            self.write_with_decision(self.raw_decision)
        self.assertEqual(self.store.payload, before)

    def test_contract_is_only_read_and_digest_is_bound(self) -> None:
        path = ROOT / "governance" / "active-change.json"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write()
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        changed = copy.deepcopy(self.contract)
        changed["goal"] = "changed"
        with self.assertRaisesRegex(StageAcceptanceWriteError, "digest mismatch"):
            write_stage_acceptance(
                self.store,
                self.decision,
                expected_binding=self.binding,
                change_contract=changed,
                change_contract_digest=self.contract_digest,
                verification=self.verification,
            )

    def test_decision_or_preview_mismatch_fails_closed(self) -> None:
        wrong = dict(self.decision, input_digest="sha256:" + "f" * 64)
        before = copy.deepcopy(self.store.payload)
        with self.assertRaises(StageAcceptanceWriteError):
            write_stage_acceptance(
                self.store,
                wrong,
                expected_binding=self.binding,
                change_contract=self.contract,
                change_contract_digest=self.contract_digest,
                verification=self.verification,
            )
        self.assertEqual(self.store.payload, before)

    def test_missing_reverification_inputs_fail_closed(self) -> None:
        before = copy.deepcopy(self.store.payload)
        with self.assertRaisesRegex(StageAcceptanceWriteError, "independently reverified"):
            write_stage_acceptance(
                self.store,
                self.decision,
                expected_binding=self.binding,
                change_contract=self.contract,
                change_contract_digest=self.contract_digest,
                verification=object(),
            )
        self.assertEqual(self.store.payload, before)

    def test_writer_requires_typed_proof_and_has_no_caller_gate_paths(self) -> None:
        signature = inspect.signature(write_stage_acceptance)
        self.assertNotIn("human_gate_path", signature.parameters)
        self.assertNotIn("human_decision_path", signature.parameters)
        self.assertNotIn("workspace", signature.parameters)
        self.assertNotIn("protected_approval", signature.parameters)
        self.assertIn("verification", signature.parameters)

    def test_blocked_decision_cannot_write(self) -> None:
        receipt = build_stage_evidence_receipt(
            **self.binding,
            workflow_run_attempt={"run_id": 17, "attempt": 1},
            artifact={"id": "artifact-1", "digest": "sha256:" + "e" * 64},
            result="FAIL",
            producer="quality",
            policy="stage2b1-p3-evidence-receipt@1",
        )
        blocked = trusted_decision(reduce_stage_acceptance(
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
        ), binding={
            "common": dict(self.binding),
            "task_id": self.store.payload["task_id"],
            "receipts": [],
            "provenance": [],
            "external_issuer": [],
            "protected_approval": None,
        })
        with self.assertRaisesRegex(StageAcceptanceWriteError, "acceptable reducer"):
            self.write_with_decision(blocked)

    def write_with_decision(self, decision: object) -> dict[str, object]:
        with patch(
            "stage_acceptance_writer.reverify_trusted_stage_acceptance_decision",
            return_value=decision,
        ):
            return write_stage_acceptance(
                self.store,
                decision,
                expected_binding=self.binding,
                change_contract=self.contract,
                change_contract_digest=self.contract_digest,
                verification=self.verification,
            )

    def test_final_condition_is_rejected_instead_of_implicit_completion(self) -> None:
        approval = self.approval_for_task("single-condition")
        decision = trusted_decision(
            self.raw_decision,
            proof_refs=[
                "provenance:test",
                "external-issuer:test",
                "protected-approval:" + approval.as_dict()["approval_sha256"],
            ],
            binding={
                "common": dict(self.binding),
                "task_id": "single-condition",
                "receipts": [],
                "provenance": [],
                "external_issuer": [],
                "protected_approval": {
                    "approval_sha256": approval.as_dict()["approval_sha256"],
                    "gate_id": self.gate["gate_id"],
                    "task_id": "single-condition",
                    "run_id": 17,
                    "run_attempt": 1,
                    "evidence_bindings": self.approval_evidence_bindings,
                },
            },
        )
        store = TaskRunStore.open_or_create(
            self.root / "single-condition.json",
            task_id="single-condition",
            task_kind="stage-acceptance",
            binding=self.binding,
            required_conditions=["stage-accepted"],
            current_workspace_fingerprint="workspace-1",
        )
        with patch(
            "stage_acceptance_taskrun.reverify_trusted_stage_acceptance_decision",
            return_value=decision,
        ):
            project_stage_acceptance_to_taskrun(
                store,
                decision,
                expected_binding=self.binding,
                workspace_fingerprint="workspace-1",
                verification=self.verification,
            )
        with patch(
            "stage_acceptance_writer.reverify_trusted_stage_acceptance_decision",
            return_value=decision,
        ):
            with self.assertRaisesRegex(StageAcceptanceWriteError, "final TaskRun"):
                write_stage_acceptance(
                    store,
                    decision,
                    expected_binding=self.binding,
                    change_contract=self.contract,
                    change_contract_digest=self.contract_digest,
                    verification=self.verification,
                )
        self.assertFalse(store.payload["conditions"][STAGE_ACCEPTED_CONDITION]["satisfied"])


if __name__ == "__main__":
    unittest.main()
