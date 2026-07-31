from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import repair_loop  # type: ignore


class RepairPlanningTest(unittest.TestCase):
    def test_only_independent_failed_roots_are_selected(self) -> None:
        summary = {
            "results": [
                {"id": "A", "status": "FAIL"},
                {"id": "B", "status": "FAIL"},
                {"id": "C", "status": "FAIL"},
            ]
        }
        policy = {
            "steps": [
                {"id": "A", "depends_on": []},
                {"id": "B", "depends_on": ["A"]},
                {"id": "C", "depends_on": []},
            ]
        }
        self.assertEqual(repair_loop._root_failure_gates(summary, policy), ["A", "C"])


if __name__ == "__main__":
    unittest.main()
