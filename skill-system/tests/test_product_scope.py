from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from contract import PRODUCT_CONTROL_FORBIDDEN, validate_contract_payload  # type: ignore
from models import ChangeContract  # type: ignore
from product_scope import required_profiles_for_product, target_scope_matches_contract, validate_product_scope  # type: ignore
from scope_guard import path_decision  # type: ignore


class ProductScopeTest(unittest.TestCase):
    def payload(self, *, status: str = "approved") -> dict[str, object]:
        return {
            "schema_version": 1,
            "change_id": "product-context-001",
            "target_kind": "repair",
            "goal": "repair one context owner without widening the product scope",
            "profile": "product-repair",
            "allowed_paths": [
                "services/agent-service/src/customer_agent/context/**",
                "services/agent-service/tests/context/**",
            ],
            "forbidden_paths": list(PRODUCT_CONTROL_FORBIDDEN),
            "invariants": ["single product owner remains authoritative"],
            "required_profiles": list(required_profiles_for_product("repair", "quick")),
            "writer_role": "product-implementer",
            "review_roles": ["scope-planner", "adversarial-reviewer", "release-judge"],
            "review_attestations": [],
            "affected_modules": ["agent-context"],
            "minimum_quality_mode": "quick",
            "quality_target": "governance/targets/product-context-001.md",
            "baseline_evidence": None,
            "initial_source_fingerprint": "0" * 64,
            "initial_source_file_count": 0,
            "product_validation": None,
            "status": status,
            "result": "PENDING",
        }

    def test_specific_product_scope_is_valid(self) -> None:
        self.assertEqual(validate_contract_payload(self.payload()), [])

    def test_broad_product_write_scope_is_rejected(self) -> None:
        payload = self.payload()
        payload["allowed_paths"] = ["services/**"]
        errors = validate_contract_payload(payload)
        self.assertTrue(any("product_write_scope_too_broad" in error for error in errors))

    def test_implementing_transition_requires_baseline(self) -> None:
        errors = validate_contract_payload(self.payload(status="implementing"))
        self.assertIn("product_transition_missing_baseline_evidence", errors)

    def test_read_only_target_cannot_write(self) -> None:
        contract = ChangeContract(Path("contract.json"), {
            "change_id": "diagnose-001",
            "target_kind": "diagnosis",
            "profile": "product-diagnosis",
            "allowed_paths": ["services/agent-service/src/customer_agent/context/**"],
            "forbidden_paths": list(PRODUCT_CONTROL_FORBIDDEN),
            "status": "approved",
        })
        self.assertFalse(path_decision(contract, "services/agent-service/src/customer_agent/context/x.py")[0])

    def test_quality_target_scope_must_equal_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "target.md"
            target.write_text("- 允许变更路径：`services/agent-service/src/a/**`, `services/agent-service/tests/a/**`\n", encoding="utf-8")
            ok, _details = target_scope_matches_contract(target, [
                "services/agent-service/src/a/**",
                "services/agent-service/tests/a/**",
            ])
            self.assertTrue(ok)
            mismatch, _details = target_scope_matches_contract(target, ["services/agent-service/src/a/**"])
            self.assertFalse(mismatch)

    def test_product_scope_validator_rejects_control_plane(self) -> None:
        errors = validate_product_scope(
            profile="product-repair",
            target_kind="repair",
            allowed_paths=["skill-system/**"],
            forbidden_paths=PRODUCT_CONTROL_FORBIDDEN,
            minimum_mode="quick",
        )
        self.assertTrue(any("non_product_allowed_path" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
