from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify_local_first_governance.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_local_first_governance", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalFirstVerifierTests(unittest.TestCase):
    def test_repository_local_first_contract_is_consistent(self) -> None:
        result = _load().verify(ROOT)
        self.assertEqual(result["status"], "PASS", result["errors"])
        self.assertFalse(result["production_closed"])


if __name__ == "__main__":
    unittest.main()
