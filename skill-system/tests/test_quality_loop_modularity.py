from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUALITY_LOOP = ROOT / "scripts" / "quality_loop.py"
MODULE_ROOT = ROOT / "scripts" / "quality_control"


class QualityLoopModularityTest(unittest.TestCase):
    def _load(self):
        spec = importlib.util.spec_from_file_location("quality_loop_modularity", QUALITY_LOOP)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_entrypoint_is_a_bounded_compatibility_facade(self) -> None:
        self.assertLessEqual(len(QUALITY_LOOP.read_text(encoding="utf-8").splitlines()), 1200)
        module = self._load()
        for name in (
            "run_loop", "main", "_parse_target", "_load_claim_manifest", "_quality_dimensions",
            "_environment_problem", "_advance_convergence_state", "_load_baseline", "_repair_plan",
            "PASS", "FAIL", "BLOCKED", "TRANSITION_TARGET_KINDS",
        ):
            self.assertTrue(hasattr(module, name), name)

    def test_focused_modules_have_single_responsibility_names(self) -> None:
        expected = {
            "common.py", "constants.py", "contracts.py", "environment.py", "dimensions.py",
            "claims.py", "convergence.py", "state.py",
        }
        self.assertEqual({path.name for path in MODULE_ROOT.glob("*.py")} - {"__init__.py"}, expected)

    def test_extracted_functions_are_not_duplicated_in_entrypoint(self) -> None:
        tree = ast.parse(QUALITY_LOOP.read_text(encoding="utf-8"))
        local_functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        forbidden = {
            "_parse_target", "_load_claim_manifest", "_run_shell", "_quality_dimensions",
            "_validate_policy", "_claim_results", "_advance_convergence_state", "_load_baseline",
        }
        self.assertFalse(local_functions.intersection(forbidden))
        # _environment_problem intentionally remains a tiny compatibility wrapper for monkeypatch users.
        wrapper = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_environment_problem")
        self.assertLessEqual(wrapper.end_lineno - wrapper.lineno + 1, 5)

    def test_repair_loop_import_contract_still_resolves(self) -> None:
        scripts = str(ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import repair_loop  # type: ignore
        self.assertEqual(repair_loop.quality_loop.MAX_REPAIR_ROUNDS, 8)
        self.assertTrue(callable(repair_loop.quality_loop._parse_target))


if __name__ == "__main__":
    unittest.main()
