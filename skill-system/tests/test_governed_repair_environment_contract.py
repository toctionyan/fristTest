from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_governed_repair_environment_contract as environment_contract  # noqa: E402


class GovernedRepairEnvironmentContractTests(unittest.TestCase):
    def test_g6_requires_human_environment_and_exact_pr_ci(self) -> None:
        result = environment_contract.verify(ROOT)
        self.assertEqual(result.get("status"), "PASS", result)
        self.assertTrue(result.get("requires_required_reviewers"), result)
        self.assertTrue(result.get("requires_prevent_self_review"), result)
        self.assertTrue(result.get("requires_exact_pr_pull_request_ci"), result)
        self.assertFalse(result.get("push_or_manual_ci_can_satisfy_g6"), result)
        self.assertFalse(result.get("dispatch_token_authority"), result)
        self.assertFalse(result.get("merge_allowed"), result)
        self.assertFalse(result.get("deploy_allowed"), result)
        self.assertFalse(result.get("production_closed"), result)


if __name__ == "__main__":
    unittest.main()
