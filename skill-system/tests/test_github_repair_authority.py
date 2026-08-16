from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from github_repair_authority import (  # noqa: E402
    RCA_SCHEMA,
    RepairAuthorityError,
    compile_write_grant,
    failure_binding,
    failure_case_fingerprint,
    rca_fingerprint,
    revoke_write_grant,
    validate_write_grant,
)


class GovernedRepairAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = "services/agent-service/src/agent_core/example.py"
        self.failure = {
            "schema": "github-failure-ingest@1",
            "repository": "toctionyan/fristTest",
            "workflow_run_id": "123",
            "workflow_run_attempt": "1",
            "head_sha": "a" * 40,
            "failure_signature": "b" * 64,
            "candidate_paths": [self.path],
        }
        self.rca = {
            "schema": RCA_SCHEMA,
            "state": "RCA_READ_ONLY",
            "binding": failure_binding(self.failure),
            "failure_case_sha256": failure_case_fingerprint(self.failure),
            "candidate_paths": [self.path],
            "repair_round": 1,
            "read_only": True,
            "workspace_mutated": False,
            "workspace_fingerprint_before": "c" * 64,
            "workspace_fingerprint_after": "c" * 64,
            "failure_class": "code_or_contract",
            "violated_invariant": "INV-001",
            "authority_owner": "deterministic-owner",
            "drifted_projection": "projection-a",
            "root_cause": "one semantic copy drifted",
            "existing_gate_gap": "no machine drift gate",
            "required_permanent_guard": "add canonical provenance gate",
            "repair_plan": ["repair product source", "add permanent invariant evidence"],
            "write_scope_recommendation": {
                "decision": "GRANT",
                "paths": [self.path],
            },
            "production_closed": False,
        }
        self.rca["rca_sha256"] = rca_fingerprint(self.rca)

    def test_compile_and_validate_exact_grant(self) -> None:
        grant = compile_write_grant(
            failure_case=self.failure,
            rca=self.rca,
            candidate_paths=self.failure["candidate_paths"],
        )
        self.assertEqual(
            validate_write_grant(
                grant,
                failure_case=self.failure,
                rca=self.rca,
                candidate_paths=self.failure["candidate_paths"],
            ),
            (self.path,),
        )
        self.assertEqual(grant["state"], "WRITE_GRANTED")
        self.assertFalse(grant["authority"]["scope_expansion_allowed"])
        self.assertFalse(grant["authority"]["merge_allowed"])
        self.assertFalse(grant["authority"]["deploy_allowed"])

    def test_tampered_grant_scope_is_rejected(self) -> None:
        grant = compile_write_grant(
            failure_case=self.failure,
            rca=self.rca,
            candidate_paths=self.failure["candidate_paths"],
        )
        grant["allowed_paths"] = [self.path, "services/agent-service/src/agent_core/other.py"]
        with self.assertRaises(RepairAuthorityError):
            validate_write_grant(
                grant,
                failure_case=self.failure,
                rca=self.rca,
                candidate_paths=self.failure["candidate_paths"],
            )

    def test_tampered_rca_binding_is_rejected(self) -> None:
        self.rca["binding"] = dict(self.rca["binding"])
        self.rca["binding"]["head_sha"] = "d" * 40
        self.rca["rca_sha256"] = rca_fingerprint(self.rca)
        with self.assertRaises(RepairAuthorityError):
            compile_write_grant(
                failure_case=self.failure,
                rca=self.rca,
                candidate_paths=self.failure["candidate_paths"],
            )

    def test_rca_cannot_expand_scope(self) -> None:
        self.rca["write_scope_recommendation"] = {
            "decision": "GRANT",
            "paths": [self.path, "services/agent-service/src/agent_core/other.py"],
        }
        self.rca["rca_sha256"] = rca_fingerprint(self.rca)
        with self.assertRaises(RepairAuthorityError):
            compile_write_grant(
                failure_case=self.failure,
                rca=self.rca,
                candidate_paths=self.failure["candidate_paths"],
            )

    def test_deny_never_compiles_write_authority(self) -> None:
        self.rca["write_scope_recommendation"] = {"decision": "DENY", "paths": []}
        self.rca["rca_sha256"] = rca_fingerprint(self.rca)
        with self.assertRaises(RepairAuthorityError):
            compile_write_grant(
                failure_case=self.failure,
                rca=self.rca,
                candidate_paths=self.failure["candidate_paths"],
            )

    def test_repeated_failure_revokes_write(self) -> None:
        grant = compile_write_grant(
            failure_case=self.failure,
            rca=self.rca,
            candidate_paths=self.failure["candidate_paths"],
        )
        receipt = revoke_write_grant(
            grant,
            reason="same_failure_signature_twice",
            failure_signature=self.failure["failure_signature"],
        )
        self.assertFalse(receipt["write_authority"])
        self.assertEqual(receipt["state"], "RCA_READ_ONLY")
        self.assertEqual(receipt["next_action"], "ARCHITECTURE_REPLAN_AND_NEW_RCA")


if __name__ == "__main__":
    unittest.main()
