from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate_b30_architecture.py"
spec = importlib.util.spec_from_file_location("validate_b30_architecture", SCRIPT)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class B30ArchitectureContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.authority_path = ROOT / "governance" / "architecture" / "b30-authority-map.json"
        self.retirement_path = ROOT / "governance" / "architecture" / "b30-legacy-retirement.json"
        self.doc_path = ROOT / "docs" / "architecture" / "B30_AUTHORITATIVE_RUNTIME.md"

    def test_repository_contract_is_valid(self) -> None:
        validator.validate(self.authority_path, self.retirement_path, self.doc_path)

    def _validate_mutation(self, *, authority=None, retirement=None) -> None:
        authority_payload = authority or json.loads(self.authority_path.read_text(encoding="utf-8"))
        retirement_payload = retirement or json.loads(self.retirement_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            authority_file = temp / "authority.json"
            retirement_file = temp / "retirement.json"
            authority_file.write_text(json.dumps(authority_payload), encoding="utf-8")
            retirement_file.write_text(json.dumps(retirement_payload), encoding="utf-8")
            validator.validate(authority_file, retirement_file, self.doc_path)

    def test_duplicate_chain_stage_is_rejected(self) -> None:
        payload = json.loads(self.authority_path.read_text(encoding="utf-8"))
        payload["authoritative_chain"][1] = copy.deepcopy(payload["authoritative_chain"][0])
        with self.assertRaisesRegex(validator.ContractError, "authoritative_chain_order_invalid|duplicate_chain_stage"):
            self._validate_mutation(authority=payload)

    def test_context_evidence_cannot_follow_semantic_contract(self) -> None:
        payload = json.loads(self.authority_path.read_text(encoding="utf-8"))
        chain = payload["authoritative_chain"]
        chain[1], chain[2] = chain[2], chain[1]
        with self.assertRaisesRegex(validator.ContractError, "authoritative_chain_order_invalid"):
            self._validate_mutation(authority=payload)

    def test_missing_unsupported_outcome_is_rejected(self) -> None:
        payload = json.loads(self.authority_path.read_text(encoding="utf-8"))
        payload["allowed_terminal_outcomes"].remove("UNSUPPORTED")
        with self.assertRaisesRegex(validator.ContractError, "required_terminal_outcome_missing"):
            self._validate_mutation(authority=payload)

    def test_semantic_boundary_cannot_claim_request_owner(self) -> None:
        payload = json.loads(self.authority_path.read_text(encoding="utf-8"))
        payload["authority_boundaries"]["semantic_meaning"]["owner"] = "TurnRequestLedger"
        with self.assertRaisesRegex(validator.ContractError, "authority_boundary_owner_invalid:semantic_meaning"):
            self._validate_mutation(authority=payload)

    def test_context_evidence_cannot_become_typed_target_owner(self) -> None:
        payload = json.loads(self.authority_path.read_text(encoding="utf-8"))
        payload["authority_boundaries"]["dialogue_reference"]["owner"] = "ContextEvidenceProjection"
        with self.assertRaisesRegex(validator.ContractError, "authority_boundary_owner_invalid:dialogue_reference"):
            self._validate_mutation(authority=payload)

    def test_retirement_without_deletion_condition_is_rejected(self) -> None:
        payload = json.loads(self.retirement_path.read_text(encoding="utf-8"))
        payload["retirement_targets"][0]["deletion_condition"] = ""
        with self.assertRaisesRegex(validator.ContractError, "deletion_condition"):
            self._validate_mutation(retirement=payload)

    def test_missing_work_package_is_rejected(self) -> None:
        payload = json.loads(self.retirement_path.read_text(encoding="utf-8"))
        payload["work_packages"] = payload["work_packages"][:-1]
        with self.assertRaisesRegex(validator.ContractError, "work_package_set_invalid"):
            self._validate_mutation(retirement=payload)

    def test_missing_wp02a_request_identity_subpackage_is_rejected(self) -> None:
        payload = json.loads(self.retirement_path.read_text(encoding="utf-8"))
        wp02 = next(item for item in payload["work_packages"] if item["id"] == "WP-02")
        wp02["sub_work_packages"] = [
            item for item in wp02["sub_work_packages"] if item["id"] != "WP-02A"
        ]
        with self.assertRaisesRegex(validator.ContractError, "wp02_sub_work_package_set_invalid"):
            self._validate_mutation(retirement=payload)

    def test_wp02b_cannot_claim_request_identity_owner(self) -> None:
        payload = json.loads(self.retirement_path.read_text(encoding="utf-8"))
        wp02 = next(item for item in payload["work_packages"] if item["id"] == "WP-02")
        wp02b = next(item for item in wp02["sub_work_packages"] if item["id"] == "WP-02B")
        wp02b["authority_owner"] = "TurnRequestLedger"
        with self.assertRaisesRegex(validator.ContractError, "wp02_sub_work_package_owner_invalid:WP-02B"):
            self._validate_mutation(retirement=payload)

    def test_wp02_subpackage_cannot_have_wrong_parent(self) -> None:
        payload = json.loads(self.retirement_path.read_text(encoding="utf-8"))
        wp02 = next(item for item in payload["work_packages"] if item["id"] == "WP-02")
        wp02a = next(item for item in wp02["sub_work_packages"] if item["id"] == "WP-02A")
        wp02a["parent"] = "WP-03"
        with self.assertRaisesRegex(validator.ContractError, "wp02_sub_work_package_parent_invalid:WP-02A"):
            self._validate_mutation(retirement=payload)


if __name__ == "__main__":
    unittest.main()
