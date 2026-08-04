from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system/controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

import task_run_cli  # type: ignore
from task_run import TaskRunStore  # type: ignore


class TaskRunCliTest(unittest.TestCase):
    def _store(self, root: Path) -> TaskRunStore:
        return TaskRunStore.open_or_create(
            root / "run.json",
            task_id="cli-test",
            task_kind="repair-loop",
            binding={"target": "a"},
            required_conditions=("quick", "integration"),
            current_workspace_fingerprint="workspace-a",
        )

    def test_summary_exposes_missing_conditions_and_next_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            store.block(
                code="QUICK_FAILED",
                reason="quick gate failed",
                attempted_strategies=("job-log",),
                next_action="reproduce quick locally",
                workspace_fingerprint="workspace-a",
            )
            report = task_run_cli.summarize(task_run_cli.load_task_run(store.path))
            self.assertEqual(report["status"], "BLOCKED")
            self.assertEqual(report["missing_conditions"], ["quick", "integration"])
            self.assertEqual(report["next_action"], "reproduce quick locally")

    def test_guard_fails_before_completion_and_passes_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            original_argv = sys.argv
            try:
                sys.argv = ["task_run_cli.py", "guard", "--file", str(store.path)]
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(task_run_cli.main(), 2)
                store.checkpoint(
                    status="RUNNING",
                    phase="VALIDATING",
                    workspace_fingerprint="workspace-a",
                    evidence_refs=["evidence/start.json"],
                )
                store.mark_condition("quick", evidence_refs=["evidence/quick.json"])
                store.mark_condition("integration", evidence_refs=["evidence/integration.json"])
                store.complete(
                    workspace_fingerprint="workspace-a",
                    evidence_refs=["evidence/final.json"],
                )
                sys.argv = ["task_run_cli.py", "guard", "--file", str(store.path)]
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(task_run_cli.main(), 0)
                self.assertTrue(json.loads(output.getvalue())["completion_eligible"])
            finally:
                sys.argv = original_argv


if __name__ == "__main__":
    unittest.main()
