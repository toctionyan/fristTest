from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_existing_candidate_adoption_contract.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_existing_candidate_adoption_contract", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load adoption contract verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ExistingCandidateAdoptionContractTests(unittest.TestCase):
    def test_repository_adoption_contract_is_fail_closed(self) -> None:
        module = _load_module()
        result = module.verify(ROOT)
        self.assertEqual(result["status"], "PASS", result["errors"])
        self.assertEqual(result["source_pr_number"], 1348)
        self.assertEqual(result["exact_blob_count"], 8)
        self.assertFalse(result["candidate_write_authority"])
        self.assertFalse(result["baseline_before_governance_allowed"])
        self.assertFalse(result["automatic_merge_allowed"])
        self.assertFalse(result["production_closed"])


if __name__ == "__main__":
    unittest.main()
