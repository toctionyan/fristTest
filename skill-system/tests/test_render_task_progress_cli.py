from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from task_execution_ledger import record_execution_attempt, set_execution_plan  # noqa: E402
from task_run import TaskRunStore  # noqa: E402


class RenderTaskProgressCliTests(unittest.TestCase):
    def test_cli_shows_recovered_failure_remaining_stage_and_no_user_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_path = root / "task-run.json"
            store = TaskRunStore.open_or_create(
                task_path,
                task_id="cli-progress",
                task_kind="engineering",
                binding={
                    "repository": "toctionyan/fristTest",
                    "branch": "feature/test",
                    "base_sha": "a" * 40,
                },
                required_conditions=("main_ci", "acceptance"),
            )
            set_execution_plan(
                store,
                stages=[
                    {"id": "main-ci", "label": "main push CI"},
                    {"id": "acceptance", "label": "landed acceptance"},
                ],
            )
            record_execution_attempt(
                store,
                stage_id="main-ci",
                status="FAIL",
                detail="first RED",
                evidence_refs=["run:1"],
            )
            record_execution_attempt(
                store,
                stage_id="main-ci",
                status="PASS",
                evidence_refs=["run:2"],
            )
            store.mark_condition("main_ci", evidence_refs=["run:2"])

            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "scripts/render_task_progress.py"),
                    "--task-run",
                    str(task_path),
                    "--format",
                    "json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            projection = json.loads(completed.stdout)

        self.assertEqual(projection["overall"], "PENDING")
        self.assertEqual(projection["summary"]["recovered_failure_count"], 1)
        self.assertEqual(projection["summary"]["completed_steps"], 1)
        self.assertEqual(projection["summary"]["total_steps"], 2)
        self.assertIn("acceptance", projection["missing_completion_conditions"])
        self.assertFalse(projection["summary"]["needs_user_action"])


if __name__ == "__main__":
    unittest.main()
