from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import governed_repair_path_policy as policy  # noqa: E402


class ControlPlaneRepairPathPolicyTests(unittest.TestCase):
    def test_historical_product_api_remains_product_only(self) -> None:
        product = "services/agent-service/src/agent_core/example.py"
        self.assertEqual(policy.validate_automatic_repair_paths([product]), (product,))
        with self.assertRaises(policy.RepairPathPolicyError):
            policy.validate_automatic_repair_paths(
                ["scripts/verify_engineering_bounded_autonomy_closure.py"]
            )

    def test_control_domain_allows_only_engineering_verifier_implementation(self) -> None:
        path = "scripts/verify_engineering_bounded_autonomy_closure.py"
        self.assertEqual(
            policy.validate_repair_paths(
                [path], repair_domain=policy.REPAIR_DOMAIN_CONTROL_PLANE
            ),
            (path,),
        )

    def test_control_domain_rejects_test_oracle_workflow_and_generic_script(self) -> None:
        forbidden = [
            "skill-system/tests/test_engineering_bounded_autonomy_closure.py",
            ".github/workflows/quality.yml",
            "governance/task-ledger.json",
            "scripts/github_repair_authority.py",
            "scripts/governed_repair_path_policy.py",
            "scripts/arbitrary_helper.py",
            "scripts/verify_engineering_example.test.py",
        ]
        for path in forbidden:
            with self.subTest(path=path):
                with self.assertRaises(policy.RepairPathPolicyError):
                    policy.validate_repair_paths(
                        [path], repair_domain=policy.REPAIR_DOMAIN_CONTROL_PLANE
                    )

    def test_product_domain_cannot_switch_by_supplying_control_path(self) -> None:
        with self.assertRaises(policy.RepairPathPolicyError):
            policy.validate_repair_paths(
                ["scripts/verify_engineering_bounded_autonomy_closure.py"],
                repair_domain=policy.REPAIR_DOMAIN_PRODUCT,
            )

    def test_unknown_domain_fails_closed(self) -> None:
        with self.assertRaisesRegex(policy.RepairPathPolicyError, "unknown_repair_domain"):
            policy.validate_repair_paths(
                ["scripts/verify_engineering_bounded_autonomy_closure.py"],
                repair_domain="EVERYTHING",
            )

    def test_policy_identity_detects_capability_expansion_mutations(self) -> None:
        matrix = policy.mutation_detection_matrix()
        self.assertTrue(matrix)
        self.assertTrue(all(matrix.values()), matrix)
        self.assertIn("control_plane_pattern_widened", matrix)
        self.assertIn("control_plane_tests_became_writable", matrix)


if __name__ == "__main__":
    unittest.main()
