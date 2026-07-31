
from __future__ import annotations
import unittest
from pathlib import Path
import sys
CONTROLLER=Path(__file__).resolve().parents[1]/"controller"; sys.path.insert(0,str(CONTROLLER))
from progress import evaluate_progress

class ProgressTest(unittest.TestCase):
    def test_newly_exposed_downstream_is_progress(self):
        result=evaluate_progress({"failed_gate_ids":["A"],"upstream_skipped_gate_ids":["B","C"],"last_failure_count":1},{"failed_gate_ids":["B"],"upstream_skipped_gate_ids":["C"]})
        self.assertTrue(result["improved"]); self.assertEqual(result["newly_exposed_downstream_gate_ids"],["B"])
    def test_traded_unrelated_failure_is_not_progress(self):
        result=evaluate_progress({"failed_gate_ids":["A"],"upstream_skipped_gate_ids":[],"last_failure_count":1},{"failed_gate_ids":["Z"],"upstream_skipped_gate_ids":[]})
        self.assertFalse(result["improved"]); self.assertEqual(result["unexpected_new_gate_ids"],["Z"])

if __name__=="__main__": unittest.main()
