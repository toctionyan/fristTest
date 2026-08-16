from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_governed_repair_mutation_proof as mutation_proof  # noqa: E402


class GovernedRepairMutationProofTests(unittest.TestCase):
    def test_secondary_projection_drift_is_machine_blocked_without_product_baseline_write(self) -> None:
        result = mutation_proof.verify(ROOT)
        self.assertEqual(result.get("status"), "PASS", result)
        self.assertTrue(result.get("mutation_killed"), result)
        self.assertTrue(result.get("workspace_unchanged"), result)
        self.assertEqual(result.get("mutation"), "write_grant_lifecycle_projection_drift")
        self.assertFalse(result.get("production_closed"), result)


if __name__ == "__main__":
    unittest.main()
