from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
CONTROL = ROOT / "skill-system" / "controller"
for entry in (SCRIPTS, CONTROL):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import github_repair_exact_head as exact_head  # noqa: E402
from task_run import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    InvalidTaskTransitionError,
    TaskRunStore,
)


PRE_G6 = {
    "G0_SCOPE_AUTHORITY": {"status": "PASS"},
    "G1_CONTRACT_PROJECTION": {"status": "PASS"},
    "G2_SEMANTIC_INVARIANT": {"status": "PASS"},
    "G3_MUTATION": {"status": "PASS"},
    "G4_FINAL_AUTHORITY": {"status": "PASS"},
    "G5_INTEGRATION_CERTIFICATION": {"status": "PASS"},
    "G6_GOVERNANCE_EXACT_HEAD": {"status": "BASELINE_ACCEPTED_EXACT_HEAD_PENDING"},
}


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _baseline(exact_sha: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "governed-baseline-acceptance@1",
        "status": "BASELINE_ACCEPTED",
        "governed_repair_state": "BASELINE_ACCEPTED",
        "repository": "owner/repo",
        "source_run_id": "42",
        "draft_pr_url": "https://github.com/owner/repo/pull/99",
        "repair_branch": "fix/example",
        "repair_base_branch": "main",
        "published_source_sha": "a" * 40,
        "baseline_commit_sha": exact_sha,
        "validated_tree_sha": "b" * 40,
        "rca_sha256": "c" * 64,
        "write_grant_sha256": "d" * 64,
        "governance_sha256": "e" * 64,
        "approved_source_paths": ["services/agent-service/src/a.py"],
        "approved_baseline_paths": ["services/agent-service/src/a.py"],
        "baseline_path": "skill-system/registry/product-source-baseline.json",
        "gates": dict(PRE_G6),
        "governance_closed": True,
        "baseline_accepted": True,
        "exact_head_certified": False,
        "ready_for_review": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    payload["baseline_acceptance_sha256"] = exact_head._fingerprint(payload)
    return payload


def _ci(exact_sha: str) -> dict[str, object]:
    return {
        "schema": "governed-repair-exact-head-ci@1",
        "head_sha": exact_sha,
        "pr_url": "https://github.com/owner/repo/pull/99",
        "pr_number": 99,
        "pr_is_draft": True,
        "pr_head_sha": exact_sha,
        "workflows": {
            "quality": {
                "run_id": "100",
                "status": "completed",
                "conclusion": "success",
                "head_sha": exact_sha,
                "event": "pull_request",
                "pr_number": 99,
            },
            "skill-self-validation": {
                "run_id": "101",
                "status": "completed",
                "conclusion": "success",
                "head_sha": exact_sha,
                "event": "pull_request",
                "pr_number": 99,
            },
        },
    }


def _task(exact_sha: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": "g6-resume-transition-test",
        "task_kind": "governed-repair",
        "status": "WAITING_EXTERNAL_RESULT",
        "phase": "STAGE6_EXACT_HEAD_CERTIFICATION_REQUIRED",
        "revision": 0,
        "binding": {"draft_pr_url": "https://github.com/owner/repo/pull/99", "head_sha": exact_sha},
        "required_conditions": ["exact_head_certified", "ready_for_review"],
        "conditions": {
            "exact_head_certified": {"satisfied": False, "evidence_refs": [], "updated_at": None},
            "ready_for_review": {"satisfied": False, "evidence_refs": [], "updated_at": None},
        },
        "metadata": {},
        "checkpoints": [],
        "action_attempts": [],
        "blockers": [],
        "created_at": "2026-08-18T00:00:00+00:00",
        "updated_at": "2026-08-18T00:00:00+00:00",
    }


class ExactHeadTaskTransitionTests(unittest.TestCase):
    def test_waiting_external_result_cannot_complete_directly(self) -> None:
        self.assertIn("VALIDATING", ALLOWED_TRANSITIONS["WAITING_EXTERNAL_RESULT"])
        self.assertNotIn("COMPLETED", ALLOWED_TRANSITIONS["WAITING_EXTERNAL_RESULT"])
        exact_sha = "9" * 40
        with tempfile.TemporaryDirectory() as temp:
            task_path = Path(temp) / "task.json"
            _write(task_path, _task(exact_sha))
            store = TaskRunStore(task_path, json.loads(task_path.read_text(encoding="utf-8")))
            store.mark_condition("exact_head_certified", evidence_refs=["ci:100"])
            store.mark_condition("ready_for_review", evidence_refs=["G6:PASS"])
            with self.assertRaises(InvalidTaskTransitionError):
                store.complete(workspace_fingerprint=exact_sha, evidence_refs=["final"])

    def test_finalize_advances_waiting_task_through_validating_then_completed(self) -> None:
        exact_sha = "9" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline_path = root / "baseline.json"
            ci_path = root / "ci.json"
            task_path = root / "task.json"
            output_path = root / "final.json"
            _write(baseline_path, _baseline(exact_sha))
            _write(ci_path, _ci(exact_sha))
            _write(task_path, _task(exact_sha))
            baseline_before = baseline_path.read_bytes()

            result = exact_head.finalize_exact_head(
                baseline_receipt_path=baseline_path,
                ci_evidence_path=ci_path,
                task_run_path=task_path,
                output_path=output_path,
            )

            persisted = json.loads(task_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], "COMPLETED")
            self.assertEqual(persisted["phase"], "COMPLETED")
            self.assertEqual(
                [row["status"] for row in persisted["checkpoints"]],
                ["VALIDATING", "COMPLETED"],
            )
            self.assertEqual(
                persisted["checkpoints"][0]["phase"],
                "STAGE6_EXACT_HEAD_VALIDATING",
            )
            self.assertTrue(persisted["conditions"]["exact_head_certified"]["satisfied"])
            self.assertTrue(persisted["conditions"]["ready_for_review"]["satisfied"])
            self.assertEqual(baseline_path.read_bytes(), baseline_before)
            self.assertEqual(result["status"], "READY_FOR_REVIEW")
            self.assertEqual(result["gates"]["G6_GOVERNANCE_EXACT_HEAD"]["status"], "PASS")
            self.assertFalse(result["merge_allowed"])
            self.assertFalse(result["deploy_allowed"])
            self.assertFalse(result["production_closed"])


if __name__ == "__main__":
    unittest.main()
