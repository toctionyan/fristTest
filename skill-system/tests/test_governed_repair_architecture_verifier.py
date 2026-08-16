from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_governed_repair_architecture as architecture  # noqa: E402


class GovernedRepairArchitectureVerifierTests(unittest.TestCase):
    def test_governed_repair_architecture_is_mechanically_enforced(self) -> None:
        result = architecture.verify()
        self.assertEqual(
            result.get("status"),
            "PASS",
            "governed repair architecture drifted: "
            + "; ".join(str(item) for item in result.get("errors") or []),
        )
        self.assertFalse(result.get("merge_allowed"))
        self.assertFalse(result.get("deploy_allowed"))
        self.assertFalse(result.get("production_closed"))


if __name__ == "__main__":
    unittest.main()
