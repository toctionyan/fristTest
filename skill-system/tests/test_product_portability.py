from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from portable_conformance import verify as verify_portable  # type: ignore
from product_scope import profile_for_target, required_profiles_for_product  # type: ignore
from scope_guard import bootstrap_command_allowed  # type: ignore


class ProductPortabilityTest(unittest.TestCase):
    def test_target_kind_selects_product_profile(self) -> None:
        self.assertEqual(profile_for_target("repair"), "product-repair")
        self.assertEqual(profile_for_target("design"), "product-design")

    def test_required_quality_profile_uses_contract_mode(self) -> None:
        profiles = required_profiles_for_product("repair", "integration")
        self.assertIn("product-quality-integration", profiles)
        self.assertNotIn("product-quality-quick", profiles)

    def test_skillctl_lifecycle_is_trusted_bootstrap(self) -> None:
        self.assertTrue(bootstrap_command_allowed("python3 -B skillctl.py product-init --change-id x"))
        self.assertTrue(bootstrap_command_allowed("python3 -B skillctl.py product-baseline"))
        self.assertTrue(bootstrap_command_allowed("python3 -B skillctl.py contract-verify --result CONVERGED"))

    def test_portable_host_files_and_commands_exist(self) -> None:
        self.assertEqual(verify_portable(), [])

    def test_active_change_is_excluded_from_product_candidate_snapshot(self) -> None:
        import importlib.util
        module_path = ROOT / "scripts" / "quality_loop.py"
        spec = importlib.util.spec_from_file_location("quality_loop_snapshot_test", module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(module._snapshot_ignored(Path("governance/active-change.json")))
        self.assertFalse(module._snapshot_ignored(Path("services/agent-service/src/example.py")))


if __name__ == "__main__":
    unittest.main()
