from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from execution_progress import build_execution_progress  # noqa: E402


class ExecutionProgressCompletionAuthorityTests(unittest.TestCase):
    def _task(self, status: str, phase: str) -> dict:
        return {
            "task_id": "whole-task",
            "status": status,
            "phase": phase,
            "required_conditions": ["implementation", "main_ci", "acceptance"],
            "conditions": {
                "implementation": {"satisfied": True, "evidence_refs": ["impl:1"]},
                "main_ci": {"satisfied": True, "evidence_refs": ["main:1"]},
                "acceptance": {"satisfied": True, "evidence_refs": ["acceptance:1"]},
            },
            "metadata": {},
        }

    def test_green_conditions_do_not_complete_task_before_final_taskrun_checkpoint(self) -> None:
        progress = build_execution_progress(task=self._task("VALIDATING", "FINALIZING"))
        self.assertFalse(progress["completion_eligible"])
        self.assertEqual(progress["overall"], "PENDING")

    def test_final_completed_taskrun_with_green_conditions_can_be_complete(self) -> None:
        progress = build_execution_progress(task=self._task("COMPLETED", "COMPLETED"))
        self.assertTrue(progress["completion_eligible"])
        self.assertEqual(progress["overall"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
