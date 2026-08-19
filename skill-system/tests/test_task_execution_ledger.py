from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from execution_progress import build_execution_progress  # noqa: E402
from task_execution_ledger import (  # noqa: E402
    TaskExecutionLedgerError,
    projection_inputs,
    read_execution_ledger,
    record_execution_attempt,
    set_execution_plan,
)
from task_run import TaskRunStore  # noqa: E402


class TaskExecutionLedgerTests(unittest.TestCase):
    def _store(self, root: Path) -> TaskRunStore:
        return TaskRunStore.open_or_create(
            root / "task-run.json",
            task_id="transparent-long-task",
            task_kind="engineering",
            binding={
                "repository": "toctionyan/fristTest",
                "branch": "feature/test",
                "base_sha": "a" * 40,
            },
            required_conditions=("main_ci", "landed_acceptance"),
        )

    def test_failed_attempt_is_durable_after_later_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self._store(Path(temp))
            set_execution_plan(
                store,
                stages=[
                    {"id": "pr-ci", "label": "PR exact-head CI"},
                    {"id": "main-ci", "label": "main push CI"},
                    {"id": "acceptance", "label": "landed-system acceptance"},
                ],
            )
            record_execution_attempt(
                store,
                stage_id="pr-ci",
                status="PASS",
                evidence_refs=["run:pr:1"],
            )
            record_execution_attempt(
                store,
                stage_id="main-ci",
                status="FAIL",
                detail="protected baseline drift",
                evidence_refs=["run:main:1"],
            )
            record_execution_attempt(
                store,
                stage_id="main-ci",
                status="PASS",
                evidence_refs=["run:main:2"],
            )
            planned, attempts = projection_inputs(store.payload)
            progress = build_execution_progress(
                task=store.payload,
                planned_stages=planned,
                attempt_history=attempts,
            )

        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[1]["status"], "FAIL")
        self.assertEqual(attempts[2]["status"], "PASS")
        self.assertEqual(progress["summary"]["recovered_failure_count"], 1)
        self.assertEqual(progress["recovered_failures"][0]["failed_attempts"], [1])
        self.assertFalse(progress["authority_effect"])

    def test_attempt_cannot_skip_sequence_or_invent_unplanned_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self._store(Path(temp))
            set_execution_plan(store, stages=[{"id": "ci", "label": "CI"}])
            with self.assertRaises(TaskExecutionLedgerError):
                record_execution_attempt(
                    store,
                    stage_id="other",
                    status="FAIL",
                    evidence_refs=["run:1"],
                )
            with self.assertRaises(TaskExecutionLedgerError):
                record_execution_attempt(
                    store,
                    stage_id="ci",
                    status="FAIL",
                    evidence_refs=["run:2"],
                    attempt=2,
                )

    def test_execution_ledger_cannot_claim_task_completion_or_production(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self._store(Path(temp))
            set_execution_plan(store, stages=[{"id": "ci", "label": "CI"}])
            record_execution_attempt(
                store,
                stage_id="ci",
                status="PASS",
                evidence_refs=["run:1"],
            )
            ledger = read_execution_ledger(store.payload)

        self.assertFalse(ledger["authority_effect"])
        self.assertFalse(ledger["production_closed"])
        self.assertNotEqual(store.payload["status"], "COMPLETED")
        self.assertFalse(store.payload["conditions"]["main_ci"]["satisfied"])
        self.assertFalse(store.payload["conditions"]["landed_acceptance"]["satisfied"])


if __name__ == "__main__":
    unittest.main()
