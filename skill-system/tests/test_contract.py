
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import sys

CONTROLLER=Path(__file__).resolve().parents[1]/"controller"
sys.path.insert(0,str(CONTROLLER))
from contract import SKILL_ONLY_ALLOWED, SKILL_ONLY_FORBIDDEN, REQUIRED_PROFILES, validate_contract_payload
import change_contract_cli


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

    def test_implementing_transition_requires_repair_governance(self):
        p=self.payload(); p["status"]="implementing"
        self.assertIn("transition_missing_repair_governance", validate_contract_payload(p))

    def test_approved_transition_may_be_planned_before_permit_binding(self):
        p=self.payload(); p["status"]="approved"
        self.assertNotIn("transition_missing_repair_governance", validate_contract_payload(p))

    def test_read_only_diagnosis_does_not_require_repair_governance(self):
        p=self.payload(); p["target_kind"]="diagnosis"; p["writer_role"]="none"; p["status"]="approved"
        self.assertNotIn("transition_missing_repair_governance", validate_contract_payload(p))

    def test_migration_begin_rejects_missing_architecture_decision_before_permit(self):
        payload = self.payload()
        payload.update({
            "target_kind": "migration",
            "status": "approved",
            "repair_governance": "governance/repair-cases/example",
        })
        contract = SimpleNamespace(
            payload=payload,
            status="approved",
            target_kind=SimpleNamespace(value="migration"),
            profile="skill-only",
            path=Path("/tmp/active-change.json"),
        )
        with (
            patch.object(change_contract_cli, "_workspace", return_value=Path("/tmp")),
            patch.object(change_contract_cli, "load_contract", return_value=contract),
            patch.object(
                change_contract_cli,
                "_validate_architecture_inputs",
                side_effect=SystemExit("architecture decision record is required"),
            ) as architecture_gate,
            patch.object(change_contract_cli, "validate_multi_agent_begin_ready") as permit_gate,
        ):
            with self.assertRaisesRegex(SystemExit, "architecture decision record is required"):
                change_contract_cli.cmd_begin(SimpleNamespace())
        architecture_gate.assert_called_once_with(Path("/tmp"), payload)
        permit_gate.assert_not_called()

    def test_skill_only_scope_includes_phase_candidate_metadata(self):
        from contract import SKILL_ONLY_ALLOWED
        self.assertIn("PHASE_CANDIDATE_NOTICE.md", SKILL_ONLY_ALLOWED)
        self.assertIn("PHASE_CANDIDATE_MANIFEST.json", SKILL_ONLY_ALLOWED)
        self.assertIn("B18_STAGE_SUMMARY.json", SKILL_ONLY_ALLOWED)

if __name__=="__main__": unittest.main()