from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_SUPPORT = ROOT / "skill-system" / "tests"
if str(TEST_SUPPORT) not in sys.path:
    sys.path.insert(0, str(TEST_SUPPORT))

import dependency_basis_guard as guard  # noqa: E402


class DependencyBasisContractGuardTests(unittest.TestCase):
    def test_live_contract_projection_guard_is_green(self) -> None:
        result = guard.verify_contract()
        self.assertEqual(result.get("status"), "PASS", result)
        self.assertEqual(result.get("invariant_id"), "DEP-BASIS-CONTRACT-001")
        self.assertEqual(result.get("guard_id"), "dependency-basis-contract")
        self.assertFalse(result.get("authority_effect"))
        self.assertEqual(
            result.get("final_dependency_authority"),
            "deterministic_dependency_proof_reducer",
        )

    def test_real_secondary_projection_mutations_are_killed(self) -> None:
        result = guard.verify_mutation_proof()
        self.assertEqual(result.get("status"), "PASS", result)
        self.assertTrue(result.get("all_mutations_killed"), result)
        self.assertTrue(result.get("workspace_unchanged"), result)
        self.assertFalse(result.get("authority_effect"))
        mutations = result.get("mutations") or []
        self.assertEqual(
            {str(row.get("name")) for row in mutations},
            {
                "runtime_projection_copy_drift",
                "generated_manifest_drift",
                "canonical_rule_changed_without_projection",
            },
        )
        self.assertTrue(all(row.get("killed") is True for row in mutations), result)

    def test_product_semantic_witness_cannot_disappear_silently(self) -> None:
        witness = (
            ROOT
            / "services"
            / "agent-service"
            / "tests"
            / "runtime"
            / "test_release56_dependency_basis_contract.py"
        )
        self.assertTrue(witness.is_file(), "protected dependency-basis semantic witness is missing")

    def test_permanent_guard_is_not_a_trusted_judge_input(self) -> None:
        helper = (ROOT / "skill-system" / "tests" / "dependency_basis_guard.py").relative_to(ROOT).as_posix()
        test = (ROOT / "skill-system" / "tests" / "test_dependency_basis_contract_guard.py").relative_to(ROOT).as_posix()
        for path in (helper, test):
            self.assertFalse(path.startswith("scripts/verify_"), path)
            self.assertFalse(path.startswith("skill-system/profiles/"), path)
            self.assertFalse(path.startswith("skill-system/controller/"), path)


if __name__ == "__main__":
    unittest.main()
