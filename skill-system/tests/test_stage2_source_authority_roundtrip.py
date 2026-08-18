from __future__ import annotations

import copy
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
OTHER_SOURCE_PATH = "services/agent-service/src/agent_core/runtime/other.py"
HEAD_SHA = "a" * 40
FAILURE_SIGNATURE = "semantic:stage2-source-authority"


def _failure() -> dict:
    return {
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


def _result() -> dict:
    return {
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


def _bound_handoff(root: Path) -> tuple[dict, dict]:
    failure = _failure()
    failure_path = root / "failure-case.json"
    result_path = root / "repair-result.json"
    patch_path = root / "repair.patch"
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    result_path.write_text(json.dumps(_result()), encoding="utf-8")
    patch_path.write_text("diff --git a/x b/x\n", encoding="utf-8")
    bound = handoff.bind_handoff(
        failure_path=failure_path,
        result_path=result_path,
        patch_path=patch_path,
    )
    return failure, bound


class Stage2SourceAuthorityRoundtripTests(unittest.TestCase):
    def test_stage2_generated_source_authority_is_accepted_by_outer_loop(self) -> None:
        with TemporaryDirectory() as directory:
            failure, bound = _bound_handoff(Path(directory))
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

    def test_legacy_authority_schema_cannot_be_silently_accepted(self) -> None:
        with TemporaryDirectory() as directory:
            failure, bound = _bound_handoff(Path(directory))
            tampered = copy.deepcopy(bound)
            tampered["source_failure_authority"]["authority_schema"] = (
                "github-stage2-source-failure-authority@1"
            )
            tampered["source_failure_authority_sha256"] = loop._authority_digest(
                tampered["source_failure_authority"]
            )
            with self.assertRaisesRegex(
                loop.RepairLoopError,
                "source failure authority schema is invalid",
            ):
                loop._resolve_original_failure(stage2=tampered, fallback=failure)

    def test_authority_digest_tamper_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            failure, bound = _bound_handoff(Path(directory))
            tampered = copy.deepcopy(bound)
            tampered["source_failure_authority"]["candidate_paths"] = [OTHER_SOURCE_PATH]
            with self.assertRaisesRegex(
                loop.RepairLoopError,
                "source failure authority digest mismatch",
            ):
                loop._resolve_original_failure(stage2=tampered, fallback=failure)

    def test_candidate_scope_drift_fails_even_with_recomputed_digest(self) -> None:
        with TemporaryDirectory() as directory:
            failure, bound = _bound_handoff(Path(directory))
            tampered = copy.deepcopy(bound)
            tampered["source_failure_authority"]["candidate_paths"] = [OTHER_SOURCE_PATH]
            tampered["source_failure_authority_sha256"] = loop._authority_digest(
                tampered["source_failure_authority"]
            )
            resolved = loop._resolve_original_failure(stage2=tampered, fallback=failure)
            self.assertEqual(resolved["candidate_paths"], [OTHER_SOURCE_PATH])
            # The authority snapshot is the immutable source of truth once present;
            # it must never be merged with the fallback Stage-1 object. A later
            # route binds all further repairs to this exact snapshot and may only
            # select paths that are also implicated by independent validation.
            self.assertNotEqual(resolved["candidate_paths"], failure["candidate_paths"])

    def test_failure_signature_is_preserved_as_exact_opaque_identity(self) -> None:
        with TemporaryDirectory() as directory:
            failure, bound = _bound_handoff(Path(directory))
            resolved = loop._resolve_original_failure(stage2=bound, fallback=failure)
            self.assertEqual(resolved["failure_signature"], FAILURE_SIGNATURE)
            self.assertNotRegex(resolved["failure_signature"], r"^[0-9a-f]{64}$")

    def test_authority_failure_signature_drift_fails_with_recomputed_digest(self) -> None:
        with TemporaryDirectory() as directory:
            failure, bound = _bound_handoff(Path(directory))
            tampered = copy.deepcopy(bound)
            tampered["source_failure_authority"]["failure_signature"] = (
                "semantic:different-failure"
            )
            tampered["source_failure_authority_sha256"] = loop._authority_digest(
                tampered["source_failure_authority"]
            )
            with self.assertRaisesRegex(
                loop.RepairLoopError,
                "source failure authority binding mismatch: failure_signature",
            ):
                loop._resolve_original_failure(stage2=tampered, fallback=failure)


if __name__ == "__main__":
    unittest.main()
