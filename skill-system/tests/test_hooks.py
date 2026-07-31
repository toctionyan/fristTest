
from __future__ import annotations
import unittest
from pathlib import Path
import sys
CONTROLLER=Path(__file__).resolve().parents[1]/"controller"; sys.path.insert(0,str(CONTROLLER))
from contract import ChangeContract, SKILL_ONLY_ALLOWED, SKILL_ONLY_FORBIDDEN
from scope_guard import path_decision, command_decision, normalize_path

class HookTest(unittest.TestCase):
    def contract(self):
        return ChangeContract(Path("contract.json"),{"allowed_paths":list(SKILL_ONLY_ALLOWED),"forbidden_paths":list(SKILL_ONLY_FORBIDDEN),"change_id":"x","target_kind":"repair","profile":"skill-only","status":"approved"})
    def test_blocks_product_source(self): self.assertFalse(path_decision(self.contract(),"services/agent-service/x.py")[0])
    def test_allows_skill_source(self): self.assertTrue(path_decision(self.contract(),"skill-system/core/x.md")[0])
    def test_blocks_destructive_command(self): self.assertFalse(command_decision("git reset --hard HEAD")[0])
    def test_blocks_shell_redirection(self): self.assertFalse(command_decision("echo changed > services/x.py")[0])
    def test_blocks_inline_python_mutation(self): self.assertFalse(command_decision("python3 -c \"open(\'x\',\'w\').write(\'y\')\"")[0])

    def test_dot_directory_is_not_stripped(self):
        workspace = Path("/workspace")
        self.assertEqual(normalize_path(".quality/evidence.json", workspace), ".quality/evidence.json")
        allowed, reason = path_decision(self.contract(), ".quality/evidence.json")
        self.assertFalse(allowed)
        self.assertIn("protected", reason)

if __name__=="__main__": unittest.main()
