from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate_b30_semantic_context.py"
spec = importlib.util.spec_from_file_location("validate_b30_semantic_context", SCRIPT)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class SemanticContextAuthorityContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = ROOT / "governance" / "architecture" / "b30-semantic-context-authority.json"
        self.documentation = ROOT / "docs" / "architecture" / "B30_SEMANTIC_CONTEXT_AUTHORITY.md"

    def _validate(self, payload: dict) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            validator.validate(path, self.documentation)

    def test_repository_contract_is_valid(self) -> None:
        validator.validate(self.contract, self.documentation)

    def test_context_evidence_must_precede_semantic_freeze(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        order = payload["semantic_compile_order"]
        context_index = order.index("build_context_evidence_projection")
        freeze_index = order.index("freeze_turn_semantic_contract")
        order[context_index], order[freeze_index] = order[freeze_index], order[context_index]
        with self.assertRaisesRegex(validator.SemanticContextContractError, "semantic_compile_order_invalid"):
            self._validate(payload)

    def test_context_projection_cannot_auto_select_target(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        payload["context_evidence_projection"]["hard_flags"]["runtime_auto_select_target"] = True
        with self.assertRaisesRegex(validator.SemanticContextContractError, "context_hard_flags_invalid"):
            self._validate(payload)

    def test_visible_referent_set_cannot_be_dispatchable(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        payload["visible_referent_sets"]["dispatchable"] = True
        with self.assertRaisesRegex(validator.SemanticContextContractError, "non_dispatchable"):
            self._validate(payload)

    def test_semantics_cannot_mutate_after_freeze(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        payload["frozen_semantic_contract"]["mutation_after_freeze"] = True
        with self.assertRaisesRegex(validator.SemanticContextContractError, "semantic_mutation_after_freeze_forbidden"):
            self._validate(payload)

    def test_latest_object_guess_cannot_be_removed_from_forbidden_set(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        payload["legacy_policy"]["forbidden"].remove("latest-object automatic target selection")
        with self.assertRaisesRegex(validator.SemanticContextContractError, "legacy_forbidden_set_invalid"):
            self._validate(payload)

    def test_source_effect_ambiguity_counterexample_is_required(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        payload["acceptance_tests"].remove("multiple_latest_refs_distinct_source_effects_are_ambiguous")
        with self.assertRaisesRegex(validator.SemanticContextContractError, "acceptance_test_set_invalid"):
            self._validate(payload)

    def test_wp02b_cannot_write_turn_request_store(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        payload["implementation_scope"]["forbidden_paths"].remove(
            "services/agent-service/src/agent_core/persistence/turn_request_store.py"
        )
        with self.assertRaisesRegex(validator.SemanticContextContractError, "wp02a_request_store_must_be_forbidden"):
            self._validate(payload)


if __name__ == "__main__":
    unittest.main()
