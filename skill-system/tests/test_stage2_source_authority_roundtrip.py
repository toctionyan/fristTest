from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
CONTROL = ROOT / "skill-system" / "controller"
for entry in (str(SCRIPTS), str(CONTROL)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import github_repair_loop_controller as loop  # noqa: E402
import github_stage2_handoff as handoff  # noqa: E402


SOURCE_PATH = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
HEAD_SHA = "a" * 40
FAILURE_SIGNATURE = "semantic:stage2-source-authority"


class Stage2SourceAuthorityRoundtripTests(unittest.TestCase):
    def test_stage2_generated_source_authority_is_accepted_by_outer_loop(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            failure = {
                "schema": "github-failure-ingest@1",
                "status": "INGESTED",
                "repository": "acme/repo",
                "workflow_name": "quality",
                "workflow_run_id": "123",
                "workflow_run_attempt": "1",
                "head_sha": HEAD_SHA,
                "head_branch": "feature/source",
                "source_pr_number": 42,
                "failure_signature": FAILURE_SIGNATURE,
                "classification": "code_or_contract",
                "repair_allowed": True,
                "same_repository": True,
                "candidate_paths": [SOURCE_PATH],
                "repair_branch": "governed-repair/quality-123",
                "repair_base_branch": "feature/source",
            }
            result = {
                "schema": "github-governed-repair-stage2@1",
                "status": "REPAIR_CANDIDATE_READY",
                "repository": "acme/repo",
                "workflow_run_id": "123",
                "workflow_run_attempt": "1",
                "head_sha": HEAD_SHA,
                "failure_signature": FAILURE_SIGNATURE,
                "repair_round": 1,
                "governed_repair_state": "INDEPENDENT_REVIEW",
                "production_closed": False,
                "rca_sha256": "b" * 64,
                "write_grant_sha256": "c" * 64,
                "violated_invariant": "single-owner-source-authority",
                "authority_owner": "product-implementer",
                "required_permanent_guard": "stage2-source-authority-roundtrip",
                "required_guard_ids": ["G0_SCOPE_AUTHORITY"],
                "write_scope": [SOURCE_PATH],
                "changed_paths": [SOURCE_PATH],
                "gates": {"G0_SCOPE_AUTHORITY": {"status": "PASS"}},
            }
            failure_path = root / "failure-case.json"
            result_path = root / "repair-result.json"
            patch_path = root / "repair.patch"
            failure_path.write_text(json.dumps(failure), encoding="utf-8")
            result_path.write_text(json.dumps(result), encoding="utf-8")
            patch_path.write_text("diff --git a/x b/x\n", encoding="utf-8")

            bound = handoff.bind_handoff(
                failure_path=failure_path,
                result_path=result_path,
                patch_path=patch_path,
            )
            self.assertEqual(
                bound["source_failure_authority"]["authority_schema"],
                handoff.SOURCE_AUTHORITY_SCHEMA,
            )

            resolved = loop._resolve_original_failure(
                stage2=bound,
                fallback=failure,
            )
            self.assertEqual(resolved["candidate_paths"], [SOURCE_PATH])
            self.assertEqual(resolved["failure_signature"], FAILURE_SIGNATURE)
            self.assertEqual(
                resolved["authority_schema"],
                handoff.SOURCE_AUTHORITY_SCHEMA,
            )


if __name__ == "__main__":
    unittest.main()
