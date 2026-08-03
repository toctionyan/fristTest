from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate_b30_turn_request_ledger.py"
spec = importlib.util.spec_from_file_location("validate_b30_turn_request_ledger", SCRIPT)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class TurnRequestLedgerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = ROOT / "governance" / "architecture" / "b30-turn-request-ledger.json"
        self.documentation = ROOT / "docs" / "architecture" / "B30_TURN_REQUEST_LEDGER.md"

    def _validate(self, payload: dict) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            validator.validate(path, self.documentation)

    def test_repository_contract_is_valid(self) -> None:
        validator.validate(self.contract, self.documentation)

    def test_scope_key_cannot_drop_tenant_or_client_request_id(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        payload["authority"]["scope_key"] = ["user_id", "thread_id"]
        with self.assertRaisesRegex(validator.LedgerContractError, "scope_key_invalid"):
            self._validate(payload)

    def test_graph_cannot_run_before_ledger_claim(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        order = payload["operation_order"]
        claim = order.index("claim_turn_request_ledger")
        invoke = order.index("invoke_lifecycle_graph_with_turn_identity")
        order[claim], order[invoke] = order[invoke], order[claim]
        with self.assertRaisesRegex(validator.LedgerContractError, "operation_order_invalid"):
            self._validate(payload)

    def test_public_api_cannot_generate_request_id_fallback(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        payload["api_contract"]["server_generated_fallback_allowed"] = True
        with self.assertRaisesRegex(validator.LedgerContractError, "without_fallback"):
            self._validate(payload)

    def test_expired_running_record_cannot_be_automatically_reexecuted(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        payload["transitions"].append({
            "from": "RUNNING",
            "to": "RUNNING",
            "precondition": "lease expired",
        })
        with self.assertRaisesRegex(validator.LedgerContractError, "automatic_running_reexecution_forbidden"):
            self._validate(payload)

    def test_payload_conflict_decision_is_required(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        payload["claim_decisions"] = [
            row for row in payload["claim_decisions"] if row["decision"] != "PAYLOAD_CONFLICT"
        ]
        with self.assertRaisesRegex(validator.LedgerContractError, "claim_decision_set_invalid"):
            self._validate(payload)


if __name__ == "__main__":
    unittest.main()
