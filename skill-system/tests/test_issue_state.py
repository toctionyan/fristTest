
from __future__ import annotations
import unittest
from pathlib import Path
import sys
CONTROLLER=Path(__file__).resolve().parents[1]/"controller"; sys.path.insert(0,str(CONTROLLER))
from issue_state import merge_issue_state

class IssueStateTest(unittest.TestCase):
    def previous(self): return [{"issue_id":"QI-"+"a"*20,"gate_id":"gate-a","status":"OPEN"}]
    def test_missing_issue_is_not_automatically_resolved(self):
        merged=merge_issue_state(self.previous(),[],[]); self.assertEqual(merged[0]["status"],"NOT_RERUN")
    def test_only_explicit_pass_resolves(self):
        merged=merge_issue_state(self.previous(),[],[{"id":"gate-a","status":"PASS"}]); self.assertEqual(merged[0]["status"],"RESOLVED_BY_FULL_JUDGE")

if __name__=="__main__": unittest.main()
