from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
QUALITY_LOOP = ROOT / "scripts" / "quality_loop.py"


def _module():
    spec = importlib.util.spec_from_file_location("quality_dimensions_controller", QUALITY_LOOP)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load quality loop")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QualityDimensionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = _module()

    def test_architecture_debt_is_visible_separately_from_gate_pass(self) -> None:
        results = [
            {
                "id": "architecture-convergence",
                "status": "PASS",
                "category": "architecture",
                "metadata": {
                    "structured_assessment": {
                        "status": "PASS",
                        "architecture_status": "PASS_WITH_DEBT",
                        "architecture_debt_status": "UNCHANGED",
                    }
                },
            },
            {
                "id": "python-test-suites",
                "status": "PASS",
                "category": "unit-contract",
                "metadata": {},
            },
        ]
        dimensions = self.controller._quality_dimensions(results)
        self.assertEqual(dimensions["functional"]["status"], "PASS")
        self.assertEqual(dimensions["architecture"]["status"], "PASS_WITH_DEBT")
        self.assertEqual(dimensions["architecture"]["gate_status"], "PASS")
        self.assertEqual(dimensions["architecture"]["debt_status"], "UNCHANGED")
        self.assertEqual(
            dimensions["real_model_certification"]["status"], "NOT_DECLARED"
        )

    def test_structured_stdout_requires_one_json_object(self) -> None:
        payload = self.controller._structured_stdout_payload(
            {"stdout": json.dumps({"architecture_status": "PASS_WITH_DEBT"})}
        )
        self.assertEqual(payload, {"architecture_status": "PASS_WITH_DEBT"})
        self.assertIsNone(
            self.controller._structured_stdout_payload(
                {"stdout": "log line\n" + json.dumps({"status": "PASS"})}
            )
        )

    def test_functional_failure_is_not_hidden_by_architecture_pass(self) -> None:
        dimensions = self.controller._quality_dimensions(
            [
                {
                    "id": "architecture-convergence",
                    "status": "PASS",
                    "category": "architecture",
                    "metadata": {},
                },
                {
                    "id": "python-test-suites",
                    "status": "FAIL",
                    "category": "unit-contract",
                    "metadata": {},
                },
            ]
        )
        self.assertEqual(dimensions["functional"]["status"], "FAIL")
        self.assertEqual(dimensions["architecture"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
