
from __future__ import annotations
import unittest
from pathlib import Path
import sys
CONTROLLER=Path(__file__).resolve().parents[1]/"controller"; sys.path.insert(0,str(CONTROLLER))
from fixer_env import build_fixer_environment

class FixerEnvTest(unittest.TestCase):
    def test_secrets_are_not_forwarded(self):
        env=build_fixer_environment({"PATH":"/bin","OPENAI_API_KEY":"secret","GITHUB_TOKEN":"token"},issue_file=Path("i"),repair_plan=Path("p"),evidence_dir=Path("e"),target=Path("t"))
        self.assertEqual(env["PATH"],"/bin"); self.assertNotIn("OPENAI_API_KEY",env); self.assertNotIn("GITHUB_TOKEN",env)

if __name__=="__main__": unittest.main()
