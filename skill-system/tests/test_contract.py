
from __future__ import annotations

import unittest
from pathlib import Path
import sys

CONTROLLER=Path(__file__).resolve().parents[1]/"controller"
sys.path.insert(0,str(CONTROLLER))
from contract import SKILL_ONLY_ALLOWED, SKILL_ONLY_FORBIDDEN, REQUIRED_PROFILES, validate_contract_payload


class ContractTest(unittest.TestCase):
    def payload(self):
        return {"schema_version":1,"change_id":"skill-v6-test","target_kind":"repair","goal":"repair the Skill control plane safely","profile":"skill-only","allowed_paths":list(SKILL_ONLY_ALLOWED),"forbidden_paths":list(SKILL_ONLY_FORBIDDEN),"invariants":["product code unchanged"],"required_profiles":list(REQUIRED_PROFILES["skill-only"]),"writer_role":"skill-implementer","review_roles":["adversarial-reviewer"],"review_attestations":[],"status":"approved","decision_record":None,"variance_records":[],"result":"PENDING"}
    def test_valid_skill_contract(self): self.assertEqual(validate_contract_payload(self.payload()),[])
    def test_rejects_product_path(self):
        p=self.payload(); p["allowed_paths"].append("services/**")
        self.assertTrue(any("skill_only_allows_product_path" in e for e in validate_contract_payload(p)))
    def test_rejects_root_glob(self):
        p=self.payload(); p["allowed_paths"]=["**"]
        self.assertIn("unsafe_or_empty_allowed_paths",validate_contract_payload(p))

    def test_architecture_delta_requires_migration_decision_and_baseline(self):
        p=self.payload(); p["architecture_policy_delta"]="governance/deltas/x.json"
        errors=validate_contract_payload(p)
        self.assertIn("architecture_policy_delta_requires_migration_or_revert",errors)
        self.assertIn("architecture_policy_delta_requires_decision_record",errors)
        self.assertIn("architecture_policy_delta_requires_baseline_policy_id",errors)

if __name__=="__main__": unittest.main()
