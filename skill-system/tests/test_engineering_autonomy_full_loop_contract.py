from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
CONTROL = ROOT / "skill-system" / "controller"
for entry in (str(SCRIPTS), str(CONTROL)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import github_repair_loop_controller as loop  # noqa: E402
import github_repair_orchestrator_control_plane as orchestrator  # noqa: E402
from engineering_autonomy_continuation import build_autonomy_continuation  # noqa: E402


SOURCE_A = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
SOURCE_B = "services/agent-service/src/agent_core/lifecycle/context_store.py"
SOURCE_RUN_ID = "123"
SOURCE_RUN_ATTEMPT = "1"
SOURCE_HEAD = "a" * 40
FAILURE_SIGNATURE = "semantic:full-loop-autonomy"


def _continuation(*, grant_id: str = "grant-a", max_repair_rounds: int = 2) -> dict:
    return build_autonomy_continuation(
        grant_id=grant_id,
        grant_sha256="d" * 64,
        authorization_id="owner-autonomy:full-loop",
        authorization_sha256="e" * 64,
        source_run_id=SOURCE_RUN_ID,
        source_run_attempt=SOURCE_RUN_ATTEMPT,
        source_head_sha=SOURCE_HEAD,
        failure_signature=FAILURE_SIGNATURE,
        max_repair_rounds=max_repair_rounds,
        max_validation_retries=1,
    )


def _failure() -> dict:
    return {
        "schema": "github-failure-ingest@1",
        "status": "INGESTED",
        "repository": "acme/repo",
        "workflow_name": "quality",
        "workflow_run_id": SOURCE_RUN_ID,
        "workflow_run_attempt": SOURCE_RUN_ATTEMPT,
        "head_sha": SOURCE_HEAD,
        "failure_signature": FAILURE_SIGNATURE,
        "repair_allowed": True,
        "same_repository": True,
        "classification": "code_or_contract",
        "candidate_paths": [SOURCE_A, SOURCE_B],
        "source_changed_files": [SOURCE_A, SOURCE_B],
        "head_branch": "feature/full-loop",
        "repair_branch": "governed-repair/quality-123",
        "repair_base_branch": "feature/full-loop",
    }


def _candidate_paths_digest(paths: list[str]) -> str:
    canonical = json.dumps(paths, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EngineeringAutonomyFullLoopContractTests(unittest.TestCase):
    def test_next_round_feedback_binds_exact_narrowed_candidate_scope(self) -> None:
        continuation = _continuation()
        feedback = loop._safe_feedback_failure(
            _failure(),
            repair_paths=[SOURCE_A],
            repair_round=1,
            verification_attempt=1,
            failure_fingerprint="f" * 64,
            autonomy_continuation=continuation,
        )

        self.assertEqual(feedback["candidate_paths"], [SOURCE_A])
        self.assertFalse(feedback["loop_feedback"]["scope_expanded"])
        self.assertEqual(
            feedback["loop_feedback"]["candidate_paths_sha256"],
            _candidate_paths_digest([SOURCE_A]),
        )
        normalized = orchestrator.normalize_failure_case(feedback)
        self.assertEqual(normalized["candidate_paths"], [SOURCE_A])

    def test_next_round_feedback_cannot_reexpand_scope_inside_original_stage1_scope(self) -> None:
        feedback = loop._safe_feedback_failure(
            _failure(),
            repair_paths=[SOURCE_A],
            repair_round=1,
            verification_attempt=1,
            failure_fingerprint="f" * 64,
            autonomy_continuation=_continuation(),
        )
        feedback["candidate_paths"] = [SOURCE_A, SOURCE_B]

        with self.assertRaisesRegex(
            orchestrator.ScopeNormalizationError,
            "outer-loop candidate scope drifted",
        ):
            orchestrator.normalize_failure_case(feedback)

    def test_grant_identity_and_budget_cannot_change_between_rounds(self) -> None:
        binding = {
            "workflow_run_id": SOURCE_RUN_ID,
            "workflow_run_attempt": SOURCE_RUN_ATTEMPT,
            "head_sha": SOURCE_HEAD,
            "failure_signature": FAILURE_SIGNATURE,
        }
        original = _continuation(grant_id="grant-a", max_repair_rounds=2)
        previous = {
            "autonomy_continuation": original,
            "max_repair_rounds": 2,
            "max_validation_retries_per_candidate": 1,
        }
        same = loop._resolve_autonomy_continuation(
            stage2={"autonomy_continuation": original},
            previous=previous,
            binding=binding,
        )
        self.assertEqual(same["continuation_sha256"], original["continuation_sha256"])

        with self.assertRaisesRegex(loop.RepairLoopError, "changed between repair rounds"):
            loop._resolve_autonomy_continuation(
                stage2={"autonomy_continuation": _continuation(grant_id="grant-b", max_repair_rounds=2)},
                previous=previous,
                binding=binding,
            )

        with self.assertRaisesRegex(loop.RepairLoopError, "changed between repair rounds"):
            loop._resolve_autonomy_continuation(
                stage2={"autonomy_continuation": _continuation(grant_id="grant-a", max_repair_rounds=8)},
                previous=previous,
                binding=binding,
            )

    def test_continuation_cannot_disappear_after_autonomous_loop_started(self) -> None:
        binding = {
            "workflow_run_id": SOURCE_RUN_ID,
            "workflow_run_attempt": SOURCE_RUN_ATTEMPT,
            "head_sha": SOURCE_HEAD,
            "failure_signature": FAILURE_SIGNATURE,
        }
        previous = {
            "autonomy_continuation": _continuation(),
            "max_repair_rounds": 2,
            "max_validation_retries_per_candidate": 1,
        }
        with self.assertRaisesRegex(loop.RepairLoopError, "disappeared between repair rounds"):
            loop._resolve_autonomy_continuation(
                stage2={},
                previous=previous,
                binding=binding,
            )


if __name__ == "__main__":
    unittest.main()
