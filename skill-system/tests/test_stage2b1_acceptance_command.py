from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
for path in (CONTROLLER, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from stage2b1_acceptance import (  # noqa: E402
    Stage2B1AcceptanceCommandError,
    record_stage_acceptance,
)
from stage_acceptance_reducer import reduce_stage_acceptance  # noqa: E402
from stage_acceptance_taskrun import project_stage_acceptance_to_taskrun  # noqa: E402
from stage_evidence_receipt import build_stage_evidence_receipt  # noqa: E402
from stage_acceptance_writer import (  # noqa: E402
    StageAcceptanceWriteError,
    contract_digest,
)
from task_run import TaskRunStore  # noqa: E402
from durable_human_gate import seal_human_decision  # noqa: E402


def trusted_decision(raw: dict[str, object]) -> dict[str, object]:
    body = {
        "schema": "stage-acceptance-decision@2",
        "input_digest": raw["input_digest"],
        "status": raw["status"],
        "reasons": list(raw["reasons"]),  # type: ignore[arg-type]
        "receipt_refs": list(raw["receipt_refs"]),  # type: ignore[arg-type]
        "proof_refs": [
            "provenance:test",
            "external-issuer:test",
            "protected-approval:test",
        ],
    }
    body["decision_id"] = "sha256:" + hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return body


class Stage2B1AcceptanceCommandTests(unittest.TestCase):
    binding = {
        "stage_id": "stage2b1",
        "accepted_state_id": "accepted-state-17",
        "product_source_ref": "git-commit-sha1:" + "a" * 40,
        "protected_snapshot_digest": "sha256:" + "b" * 64,
        "control_plane_ref": "git-commit-sha1:" + "c" * 40,
        "execution_repo_ref": "git-commit-sha1:" + "d" * 40,
    }

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="stage2b1-acceptance-command-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.task_path = self.root / "task-run.json"
        self.store = TaskRunStore.open_or_create(
            self.task_path,
            task_id="stage2b1-task",
            task_kind="stage-acceptance",
            binding=self.binding,
            required_conditions=["stage-accepted", "quality-green"],
            current_workspace_fingerprint="workspace-1",
        )
        self.contract_path = self.root / "active-change.json"
        contract = json.loads(
            (ROOT / "governance" / "active-change.json").read_text(encoding="utf-8")
        )
        self.contract_path.write_text(json.dumps(contract), encoding="utf-8")
        self.contract_digest = contract_digest(contract)
        self.binding_path = self.root / "expected-binding.json"
        self.binding_path.write_text(json.dumps(self.binding), encoding="utf-8")
        self.gate_path = self.root / "human-gate.json"
        self.human_decision_path = self.root / "human-decision.json"
        self.reducer_decision_path = self.root / "stage-acceptance-decision.json"
        gate = {
            "schema": "durable-human-gate@1",
            "gate_id": "gate-stage2b1-acceptance",
            "task_id": self.store.payload["task_id"],
            "workflow_id": "stage-acceptance",
            "step_id": "stage2b1-acceptance",
            "question": "Accept the verified Stage2B1 evidence?",
            "waiting_outcome": "WAITING_FOR_HUMAN",
            "options": ["ACCEPT_STAGE2B1", "REJECT_STAGE2B1"],
            "routes": {
                "WAITING_FOR_HUMAN": "HUMAN_GATE",
                "ACCEPT_STAGE2B1": "STAGE_ACCEPTANCE",
                "REJECT_STAGE2B1": "STAGE_REJECTION",
            },
            "authority_effect": False,
        }
        gate["gate_sha256"] = hashlib.sha256(
            json.dumps(gate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        decision = seal_human_decision(
            gate,
            selected_outcome="ACCEPT_STAGE2B1",
            actor="reviewer",
            decided_at="2026-08-28T00:00:00+00:00",
        )
        self.gate_path.write_text(json.dumps(gate), encoding="utf-8")
        self.human_decision_path.write_text(json.dumps(decision), encoding="utf-8")
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
        self.decision = trusted_decision(self.raw_decision)
        project_stage_acceptance_to_taskrun(
            self.store,
            self.decision,
            expected_binding=self.binding,
            evidence_refs=["receipt:artifact-1", "decision:" + self.decision["decision_id"]],
            workspace_fingerprint="workspace-1",
        )
        self.reducer_decision_path.write_text(json.dumps(self.decision), encoding="utf-8")

    def _record(self, **overrides: Path | str) -> dict[str, object]:
        values: dict[str, object] = {
            "workspace": self.root,
            "task_run_path": self.task_path,
            "decision_path": self.reducer_decision_path,
            "expected_binding_path": self.binding_path,
            "change_contract_path": self.contract_path,
            "change_contract_digest": self.contract_digest,
            "human_gate_path": self.gate_path,
            "human_decision_path": self.human_decision_path,
        }
        values.update(overrides)
        return record_stage_acceptance(**values)  # type: ignore[arg-type]

    def test_explicit_command_records_only_existing_task_condition(self) -> None:
        before = hashlib.sha256(self.contract_path.read_bytes()).hexdigest()
        result = self._record()
        persisted = TaskRunStore(
            self.task_path,
            json.loads(self.task_path.read_text(encoding="utf-8")),
        )
        self.assertEqual(result["status"], "RECORDED")
        self.assertTrue(persisted.payload["conditions"]["stage-accepted"]["satisfied"])
        self.assertFalse(persisted.completion_decision().eligible)
        self.assertFalse(result["active_change_written"])
        self.assertFalse(result["governance_state_changed"])
        self.assertEqual(hashlib.sha256(self.contract_path.read_bytes()).hexdigest(), before)

        revision = persisted.payload["revision"]
        self.assertEqual(self._record(), result)
        persisted.reload()
        self.assertEqual(persisted.payload["revision"], revision)

    def test_missing_explicit_input_fails_without_mutating_task(self) -> None:
        before = json.loads(self.task_path.read_text(encoding="utf-8"))
        with self.assertRaises(Stage2B1AcceptanceCommandError):
            self._record(decision_path=self.root / "missing-decision.json")
        self.assertEqual(json.loads(self.task_path.read_text(encoding="utf-8")), before)

    def test_legacy_v1_decision_is_rejected_without_mutating_task(self) -> None:
        self.reducer_decision_path.write_text(
            json.dumps(self.raw_decision),
            encoding="utf-8",
        )
        before = json.loads(self.task_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(
            Stage2B1AcceptanceCommandError,
            "must use stage-acceptance-decision@2",
        ):
            self._record()
        self.assertEqual(json.loads(self.task_path.read_text(encoding="utf-8")), before)

    def test_symlinked_explicit_input_fails_closed(self) -> None:
        decision_link = self.root / "decision-link.json"
        decision_link.symlink_to(self.reducer_decision_path)
        with self.assertRaises(Stage2B1AcceptanceCommandError):
            self._record(decision_path=decision_link)

        gate_link = self.root / "gate-link.json"
        gate_link.symlink_to(self.gate_path)
        with self.assertRaisesRegex(StageAcceptanceWriteError, "missing or unsafe"):
            self._record(human_gate_path=gate_link)


if __name__ == "__main__":
    unittest.main()
