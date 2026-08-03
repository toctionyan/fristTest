from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate_b30_entrypoints.py"
spec = importlib.util.spec_from_file_location("validate_b30_entrypoints", SCRIPT)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class B30RuntimeEntrypointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = ROOT / "governance" / "architecture" / "b30-runtime-entrypoints.json"
        self.documentation = ROOT / "docs" / "architecture" / "B30_RUNTIME_ENTRYPOINTS.md"

    def _validate(self, payload: dict) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "entrypoints.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            validator.validate(path, self.documentation)

    def test_repository_inventory_is_valid(self) -> None:
        validator.validate(self.inventory, self.documentation)

    def test_missing_external_write_entrypoint_is_rejected(self) -> None:
        payload = json.loads(self.inventory.read_text(encoding="utf-8"))
        payload["entrypoints"] = [
            row for row in payload["entrypoints"]
            if row["id"] != "transaction_authority_http"
        ]
        with self.assertRaisesRegex(validator.EntrypointContractError, "external_entrypoint_inventory_incomplete"):
            self._validate(payload)

    def test_effect_bearing_pass_without_transaction_and_outcome_authority_is_rejected(self) -> None:
        payload = json.loads(self.inventory.read_text(encoding="utf-8"))
        row = next(item for item in payload["entrypoints"] if item["id"] == "transaction_reconcile_http")
        row["conformance"] = "PASS"
        row["authority_sequence"] = ["LifecycleCommandRunner", "BusinessService"]
        with self.assertRaisesRegex(validator.EntrypointContractError, "effect_bearing_pass_missing_authority"):
            self._validate(payload)

    def test_gap_without_remediation_work_package_is_rejected(self) -> None:
        payload = json.loads(self.inventory.read_text(encoding="utf-8"))
        row = next(item for item in payload["entrypoints"] if item["id"] == "chat_turn_http")
        row["remediation_work_package"] = ""
        with self.assertRaisesRegex(validator.EntrypointContractError, "remediation_work_package"):
            self._validate(payload)


if __name__ == "__main__":
    unittest.main()
