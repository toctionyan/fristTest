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
    compile_write_grant,
    failure_binding,
    failure_case_fingerprint,
    rca_fingerprint,
)
import github_repair_stage3 as stage3  # noqa: E402


class Stage3AuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = "services/agent-service/src/agent_core/example.py"
        self.failure = {
            "schema": "github-failure-ingest@1",
            "status": "INGESTED",
            "repository": "owner/repo",
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
            "failure_class": "semantic_contract_drift",
            "violated_invariant": "INV-001",
            "authority_owner": "deterministic-owner",
            "drifted_projection": "projection-a",
            "root_cause": "projection drifted",
            "existing_gate_gap": "missing provenance gate",
            "required_permanent_guard": "canonical projection + mutation proof",
            "repair_plan": ["repair the authorized product source"],
            "write_scope_recommendation": {
                "decision": "GRANT",
                "paths": [self.path],
            },
            "production_closed": False,
        }
        self.rca["rca_sha256"] = rca_fingerprint(self.rca)
        self.grant = compile_write_grant(
            failure_case=self.failure,
            rca=self.rca,
            candidate_paths=self.failure["candidate_paths"],
        )
        self.result = {
            "schema": "github-governed-repair-stage2@1",
            "status": "REPAIR_CANDIDATE_READY",
            "repository": "owner/repo",
            "workflow_run_id": "123",
            "head_sha": "a" * 40,
            "failure_signature": "b" * 64,
            "repair_branch": "governed-repair/quality-123",
            "repair_base_branch": "main",
            "write_scope": [self.path],
            "changed_paths": [self.path],
            "rca_sha256": self.rca["rca_sha256"],
            "write_grant_sha256": self.grant["write_grant_sha256"],
            "violated_invariant": self.rca["violated_invariant"],
            "authority_owner": self.rca["authority_owner"],
            "drifted_projection": self.rca["drifted_projection"],
            "required_permanent_guard": self.rca["required_permanent_guard"],
            "governed_repair_state": "INDEPENDENT_REVIEW",
            "deterministic_file_verification_passed": True,
            "full_validation_passed": False,
            "draft_pr_published": False,
            "production_closed": False,
        }

    def test_stage3_accepts_exact_rca_grant_bundle(self) -> None:
        granted = stage3._validate_authority_bundle(
            result=self.result,
            failure_case=self.failure,
            rca=self.rca,
            grant=self.grant,
            changed_paths=(self.path,),
        )
        self.assertEqual(granted, (self.path,))

    def test_stage3_rejects_patch_path_outside_grant(self) -> None:
        with self.assertRaises(stage3.Stage3Error):
            stage3._validate_authority_bundle(
                result=self.result,
                failure_case=self.failure,
                rca=self.rca,
                grant=self.grant,
                changed_paths=(self.path, "services/agent-service/src/agent_core/other.py"),
            )

    def test_stage3_rejects_tampered_grant(self) -> None:
        tampered = dict(self.grant)
        tampered["allowed_paths"] = [self.path, "services/agent-service/src/agent_core/other.py"]
        with self.assertRaises(stage3.Stage3Error):
            stage3._validate_authority_bundle(
                result=self.result,
                failure_case=self.failure,
                rca=self.rca,
                grant=tampered,
                changed_paths=(self.path,),
            )

    def test_legacy_stage3_complete_path_is_fail_closed(self) -> None:
        with self.assertRaises(stage3.Stage3Error):
            stage3.complete_publication(
                workspace=Path("."),
                validation_result_path=Path("missing-validation.json"),
                task_run_path=Path("missing-task.json"),
                pr_url="https://github.com/owner/repo/pull/99",
                output_path=Path("missing-output.json"),
            )


if __name__ == "__main__":
    unittest.main()
