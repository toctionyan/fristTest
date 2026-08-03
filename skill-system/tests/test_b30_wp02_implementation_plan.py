from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate_b30_wp02_plan.py"
spec = importlib.util.spec_from_file_location("validate_b30_wp02_plan", SCRIPT)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class WP02ImplementationPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = ROOT / "governance" / "architecture" / "b30-wp02-implementation-plan.json"
        self.documentation = ROOT / "docs" / "architecture" / "B30_WP02_IMPLEMENTATION_PLAN.md"

    def _validate(self, payload: dict) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            validator.validate(path, self.documentation)

    def test_repository_plan_is_valid(self) -> None:
        validator.validate(self.plan, self.documentation)

    def test_contract_blob_movement_invalidates_plan(self) -> None:
        payload = json.loads(self.plan.read_text(encoding="utf-8"))
        payload["contract_binding"]["git_blob_sha"] = "0" * 40
        with self.assertRaisesRegex(validator.PlanError, "contract_binding_invalid"):
            self._validate(payload)

    def test_message_store_scope_amendment_cannot_be_omitted(self) -> None:
        payload = json.loads(self.plan.read_text(encoding="utf-8"))
        payload["scope_amendment"]["added_paths_requiring_review"].remove(
            "services/agent-service/src/agent_core/persistence/message_store.py"
        )
        with self.assertRaisesRegex(validator.PlanError, "scope_amendment_paths_invalid"):
            self._validate(payload)

    def test_product_implementer_cannot_write_protected_source_baseline(self) -> None:
        payload = json.loads(self.plan.read_text(encoding="utf-8"))
        payload["implementation_paths"].append("skill-system/registry/product-source-baseline.json")
        payload["phases"][0]["writes"].append("skill-system/registry/product-source-baseline.json")
        with self.assertRaisesRegex(validator.PlanError, "forbidden_implementation_path|baseline_refresh"):
            self._validate(payload)

    def test_phase_cannot_write_outside_approved_scope(self) -> None:
        payload = json.loads(self.plan.read_text(encoding="utf-8"))
        payload["phases"][5]["writes"].append("services/business-service/app/main.py")
        with self.assertRaisesRegex(validator.PlanError, "phase_write_outside_scope"):
            self._validate(payload)

    def test_crash_recovery_dimension_is_mandatory(self) -> None:
        payload = json.loads(self.plan.read_text(encoding="utf-8"))
        payload["mandatory_test_dimensions"].remove("crash_recovery")
        with self.assertRaisesRegex(validator.PlanError, "mandatory_test_dimensions_invalid"):
            self._validate(payload)


if __name__ == "__main__":
    unittest.main()
