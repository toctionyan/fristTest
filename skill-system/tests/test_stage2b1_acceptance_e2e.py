from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
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

from durable_human_gate import seal_human_decision  # noqa: E402
from stage_acceptance_reducer import reduce_stage_acceptance  # noqa: E402
from stage_acceptance_taskrun import project_stage_acceptance_to_taskrun  # noqa: E402
from stage_acceptance_writer import contract_digest  # noqa: E402
from stage_evidence_receipt import build_stage_evidence_receipt  # noqa: E402
from task_run import TaskRunStore  # noqa: E402


class Stage2B1AcceptanceE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="stage2b1-acceptance-e2e-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        current_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
        baseline = json.loads(
            (ROOT / "skill-system" / "registry" / "product-source-baseline.json").read_text(
                encoding="utf-8"
            )
        )
        self.binding = {
            "stage_id": "stage2b1",
            "accepted_state_id": "accepted-state-17",
            "product_source_ref": baseline["product_source_ref"],
            "protected_snapshot_digest": baseline["protected_snapshot_digest"],
            "control_plane_ref": "git-commit-sha1:" + current_sha,
            "execution_repo_ref": "git-commit-sha1:" + current_sha,
        }
        self.task_path = self.root / "task-run.json"
        self.store = TaskRunStore.open_or_create(
            self.task_path,
            task_id="stage2b1-e2e-task",
            task_kind="stage-acceptance",
            binding=self.binding,
            required_conditions=["stage-accepted", "quality-green"],
            current_workspace_fingerprint="e2e-workspace",
        )

        self.contract_path = self.root / "active-change.json"
        contract = json.loads(
            (ROOT / "governance" / "active-change.json").read_text(encoding="utf-8")
        )
        self.contract_path.write_text(
            json.dumps(contract, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        self.contract_digest = contract_digest(contract)
        self.binding_path = self.root / "expected-binding.json"
        self.binding_path.write_text(
            json.dumps(self.binding, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

        self.gate_path = self.root / "human-gate.json"
        self.human_decision_path = self.root / "human-decision.json"
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
            json.dumps(
                gate,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        human_decision = seal_human_decision(
            gate,
            selected_outcome="ACCEPT_STAGE2B1",
            actor="reviewer",
            decided_at="2026-08-28T00:00:00+00:00",
        )
        self.gate_path.write_text(json.dumps(gate), encoding="utf-8")
        self.human_decision_path.write_text(
            json.dumps(human_decision),
            encoding="utf-8",
        )

        receipt = build_stage_evidence_receipt(
            **self.binding,
            workflow_run_attempt={"run_id": 901, "attempt": 1},
            artifact={
                "id": "stage2b1-e2e-artifact",
                "digest": "sha256:" + "e" * 64,
            },
            result="PASS",
            producer="quality",
            policy="stage2b1-p3-evidence-receipt@1",
        )
        decision = reduce_stage_acceptance(
            [receipt],
            required_receipt_ids=["stage2b1-e2e-artifact"],
            **self.binding,
            expected_receipt_bindings={
                "stage2b1-e2e-artifact": {
                    "artifact": receipt["artifact"],
                    "workflow_run_attempt": receipt["workflow_run_attempt"],
                    "policy": receipt["policy"],
                }
            },
        )
        self.assertEqual(decision["status"], "ACCEPTABLE_PREVIEW")
        project_stage_acceptance_to_taskrun(
            self.store,
            decision,
            expected_binding=self.binding,
            evidence_refs=[
                "receipt:stage2b1-e2e-artifact",
                "decision:" + decision["decision_id"],
            ],
            workspace_fingerprint="e2e-workspace",
        )
        self.decision_path = self.root / "stage-acceptance-decision.json"
        self.decision_path.write_text(
            json.dumps(decision, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    def _invoke(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "stage2b1_acceptance.py"),
                "--workspace",
                str(self.root),
                "--task-run",
                str(self.task_path),
                "--decision",
                str(self.decision_path),
                "--expected-binding",
                str(self.binding_path),
                "--change-contract",
                str(self.contract_path),
                "--change-contract-digest",
                self.contract_digest,
                "--human-gate",
                str(self.gate_path),
                "--human-decision",
                str(self.human_decision_path),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_real_cli_chain_records_condition_without_governance_completion(self) -> None:
        active_change_path = ROOT / "governance" / "active-change.json"
        active_change_before = active_change_path.read_bytes()
        active_change_state_before = json.loads(active_change_before)

        first = self._invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        first_result = json.loads(first.stdout)
        self.assertEqual(first_result["status"], "RECORDED")
        self.assertFalse(first_result["active_change_written"])
        self.assertFalse(first_result["governance_state_changed"])
        self.assertFalse(first_result["task_completed"])

        persisted = TaskRunStore(
            self.task_path,
            json.loads(self.task_path.read_text(encoding="utf-8")),
        )
        self.assertTrue(
            persisted.payload["conditions"]["stage-accepted"]["satisfied"]
        )
        self.assertFalse(persisted.completion_decision().eligible)
        self.assertEqual(persisted.payload["status"], "RUNNING")
        self.assertEqual(
            json.loads(active_change_path.read_bytes()),
            active_change_state_before,
        )
        self.assertEqual(active_change_path.read_bytes(), active_change_before)

        revision = persisted.payload["revision"]
        second = self._invoke()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout), first_result)
        persisted.reload()
        self.assertEqual(persisted.payload["revision"], revision)
        self.assertFalse(persisted.completion_decision().eligible)


if __name__ == "__main__":
    unittest.main()
