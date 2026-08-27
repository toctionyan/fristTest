from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
ROOT = Path(__file__).resolve().parents[2]
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from stage_acceptance_reducer import reduce_stage_acceptance  # noqa: E402
from stage_acceptance_taskrun import project_stage_acceptance_to_taskrun  # noqa: E402
from stage_acceptance_writer import (  # noqa: E402
    STAGE_ACCEPTED_CONDITION,
    StageAcceptanceWriteError,
    contract_digest,
    write_stage_acceptance,
)
from stage_evidence_receipt import build_stage_evidence_receipt  # noqa: E402
from task_run import TaskRunStore  # noqa: E402


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
        receipt = build_stage_evidence_receipt(
            **self.binding,
            workflow_run_attempt={"run_id": 17, "attempt": 1},
            artifact={"id": "artifact-1", "digest": "sha256:" + "e" * 64},
            result="PASS",
            producer="quality",
            policy="stage2b1-p3-evidence-receipt@1",
        )
        self.decision = reduce_stage_acceptance(
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
        project_stage_acceptance_to_taskrun(
            self.store,
            self.decision,
            expected_binding=self.binding,
            evidence_refs=["receipt:artifact-1", "decision:" + self.decision["decision_id"]],
            workspace_fingerprint="workspace-1",
        )

    def authorization(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "authority": "human-review",
            "authorization_ref": "review:stage2b1:p4.3:1",
            "effect": "stage_acceptance_condition_only",
            "change_id": self.contract["change_id"],
            "task_id": self.store.payload["task_id"],
            "decision_id": self.decision["decision_id"],
            "contract_digest": self.contract_digest,
            "accepted": True,
        }
        result.update(overrides)
        return result

    def write(self) -> dict[str, object]:
        return write_stage_acceptance(
            self.store,
            self.decision,
            expected_binding=self.binding,
            change_contract=self.contract,
            change_contract_digest=self.contract_digest,
            human_authorization=self.authorization(),
        )

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
                human_authorization=self.authorization(),
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
                human_authorization=self.authorization(decision_id=wrong["decision_id"]),
            )
        self.assertEqual(self.store.payload, before)

    def test_wrong_human_scope_and_unknown_field_fail_closed(self) -> None:
        before = copy.deepcopy(self.store.payload)
        with self.assertRaisesRegex(StageAcceptanceWriteError, "TaskRun mismatch"):
            self.write_with_auth(task_id="other-task")
        self.assertEqual(self.store.payload, before)
        with self.assertRaisesRegex(StageAcceptanceWriteError, "unknown fields"):
            self.write_with_auth(extra="unexpected")
        self.assertEqual(self.store.payload, before)

    def write_with_auth(self, **overrides: object) -> dict[str, object]:
        return write_stage_acceptance(
            self.store,
            self.decision,
            expected_binding=self.binding,
            change_contract=self.contract,
            change_contract_digest=self.contract_digest,
            human_authorization=self.authorization(**overrides),
        )

    def test_blocked_decision_cannot_write(self) -> None:
        receipt = build_stage_evidence_receipt(
            **self.binding,
            workflow_run_attempt={"run_id": 17, "attempt": 1},
            artifact={"id": "artifact-1", "digest": "sha256:" + "e" * 64},
            result="FAIL",
            producer="quality",
            policy="stage2b1-p3-evidence-receipt@1",
        )
        blocked = reduce_stage_acceptance(
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
        with self.assertRaisesRegex(StageAcceptanceWriteError, "acceptable reducer"):
            self.write_with_decision(blocked)

    def write_with_decision(self, decision: dict[str, object]) -> dict[str, object]:
        return write_stage_acceptance(
            self.store,
            decision,
            expected_binding=self.binding,
            change_contract=self.contract,
            change_contract_digest=self.contract_digest,
            human_authorization=self.authorization(decision_id=decision.get("decision_id")),
        )

    def test_final_condition_is_rejected_instead_of_implicit_completion(self) -> None:
        store = TaskRunStore.open_or_create(
            self.root / "single-condition.json",
            task_id="single-condition",
            task_kind="stage-acceptance",
            binding=self.binding,
            required_conditions=["stage-accepted"],
            current_workspace_fingerprint="workspace-1",
        )
        project_stage_acceptance_to_taskrun(
            store,
            self.decision,
            expected_binding=self.binding,
            evidence_refs=["receipt:artifact-1", "decision:" + self.decision["decision_id"]],
            workspace_fingerprint="workspace-1",
        )
        with self.assertRaisesRegex(StageAcceptanceWriteError, "final TaskRun"):
            write_stage_acceptance(
                store,
                self.decision,
                expected_binding=self.binding,
                change_contract=self.contract,
                change_contract_digest=self.contract_digest,
                human_authorization=self.authorization(task_id="single-condition"),
            )
        self.assertFalse(store.payload["conditions"][STAGE_ACCEPTED_CONDITION]["satisfied"])


if __name__ == "__main__":
    unittest.main()
