from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from agent_attestation import (  # type: ignore
    import_attestation,
    load_manifest,
    payload_digest,
    register_implementer,
    validate_role_separation,
    validate_stage,
)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class MultiAgentGovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._github_repository = os.environ.pop("GITHUB_REPOSITORY", None)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "remote", "add", "origin", "https://github.com/example/repository.git"],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        _write(self.root / "src/module.py", "def value():\n    return 1\n")
        _write(self.root / "governance/repair-cases/change-001/repair-plan.json", {"plan": "approved candidate"})
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "baseline"], check=True)
        self.baseline = subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        self.case_dir = self.root / "governance/repair-cases/change-001"
        self.contract = {
            "change_id": "change-001",
            "target_kind": "repair",
            "repair_governance": "governance/repair-cases/change-001",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()
        if self._github_repository is None:
            os.environ.pop("GITHUB_REPOSITORY", None)
        else:
            os.environ["GITHUB_REPOSITORY"] = self._github_repository

    def _attestation(
        self,
        *,
        role: str,
        artifact: Path,
        task_id: str,
        thread_id: str,
        worktree_id: str,
        decision: str,
        input_sha256: str | None = None,
        candidate_commit: str = "",
    ) -> Path:
        payload = {
            "schema_version": 1,
            "record_type": "agent-attestation",
            "change_id": "change-001",
            "role": role,
            "provider": "codex-cloud",
            "repository": "example/repository",
            "task_id": task_id,
            "thread_id": thread_id,
            "worktree_id": worktree_id,
            "baseline_commit": self.baseline,
            "candidate_commit": candidate_commit,
            "input_sha256": input_sha256 or ("a" * 64),
            "output_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "decision": decision,
            "issued_at": "2026-08-03T09:00:00Z",
        }
        payload["attestation_digest"] = payload_digest(payload)
        path = self.root / "inbox" / f"{role}.json"
        _write(path, payload)
        return path

    def test_imported_reviews_and_implementer_use_distinct_tasks_and_worktrees(self) -> None:
        failure = self.root / "inbox/root-cause.json"
        _write(failure, {"record_type": "root-cause-proof", "decision": "PROVEN"})
        import_attestation(
            self.root,
            self.contract,
            role="failure-explorer",
            artifact_source=failure,
            attestation_source=self._attestation(
                role="failure-explorer",
                artifact=failure,
                task_id="task-failure",
                thread_id="thread-failure",
                worktree_id="worktree-failure",
                decision="PROVEN",
            ),
        )
        plan_review = self.root / "inbox/plan-review.json"
        _write(plan_review, {"reviewer_role": "repair-plan-reviewer", "decision": "APPROVED"})
        import_attestation(
            self.root,
            self.contract,
            role="repair-plan-reviewer",
            artifact_source=plan_review,
            attestation_source=self._attestation(
                role="repair-plan-reviewer",
                artifact=plan_review,
                task_id="task-plan",
                thread_id="thread-plan",
                worktree_id="worktree-plan",
                decision="APPROVED",
            ),
        )
        register_implementer(
            self.root,
            self.contract,
            provider="codex-cloud",
            task_id="task-implementation",
            thread_id="thread-implementation",
            worktree_id="worktree-implementation",
            baseline_commit=self.baseline,
        )
        manifest = load_manifest(self.case_dir)
        validate_role_separation(
            manifest,
            {"failure-explorer", "repair-plan-reviewer", "product-implementer"},
        )
        self.assertEqual(set(manifest["stages"]), {
            "failure-explorer", "repair-plan-reviewer", "product-implementer"
        })

    def test_reused_reviewer_task_is_rejected(self) -> None:
        failure = self.root / "inbox/root-cause.json"
        _write(failure, {"record_type": "root-cause-proof", "decision": "PROVEN"})
        import_attestation(
            self.root,
            self.contract,
            role="failure-explorer",
            artifact_source=failure,
            attestation_source=self._attestation(
                role="failure-explorer",
                artifact=failure,
                task_id="shared-task",
                thread_id="thread-failure",
                worktree_id="worktree-failure",
                decision="PROVEN",
            ),
        )
        plan_review = self.root / "inbox/plan-review.json"
        _write(plan_review, {"reviewer_role": "repair-plan-reviewer", "decision": "APPROVED"})
        with self.assertRaisesRegex(ValueError, "task reused"):
            import_attestation(
                self.root,
                self.contract,
                role="repair-plan-reviewer",
                artifact_source=plan_review,
                attestation_source=self._attestation(
                    role="repair-plan-reviewer",
                    artifact=plan_review,
                    task_id="shared-task",
                    thread_id="thread-plan",
                    worktree_id="worktree-plan",
                    decision="APPROVED",
                ),
            )

    def test_reused_reviewer_worktree_is_rejected(self) -> None:
        failure = self.root / "inbox/root-cause.json"
        _write(failure, {"record_type": "root-cause-proof", "decision": "PROVEN"})
        import_attestation(
            self.root,
            self.contract,
            role="failure-explorer",
            artifact_source=failure,
            attestation_source=self._attestation(
                role="failure-explorer",
                artifact=failure,
                task_id="task-failure",
                thread_id="thread-failure",
                worktree_id="shared-worktree",
                decision="PROVEN",
            ),
        )
        plan_review = self.root / "inbox/plan-review.json"
        _write(plan_review, {"reviewer_role": "repair-plan-reviewer", "decision": "APPROVED"})
        with self.assertRaisesRegex(ValueError, "worktree reused"):
            import_attestation(
                self.root,
                self.contract,
                role="repair-plan-reviewer",
                artifact_source=plan_review,
                attestation_source=self._attestation(
                    role="repair-plan-reviewer",
                    artifact=plan_review,
                    task_id="task-plan",
                    thread_id="thread-plan",
                    worktree_id="shared-worktree",
                    decision="APPROVED",
                ),
            )

    def test_changed_artifact_invalidates_attestation(self) -> None:
        plan_review = self.root / "inbox/plan-review.json"
        _write(plan_review, {"reviewer_role": "repair-plan-reviewer", "decision": "APPROVED"})
        import_attestation(
            self.root,
            self.contract,
            role="repair-plan-reviewer",
            artifact_source=plan_review,
            attestation_source=self._attestation(
                role="repair-plan-reviewer",
                artifact=plan_review,
                task_id="task-plan",
                thread_id="thread-plan",
                worktree_id="worktree-plan",
                decision="APPROVED",
            ),
        )
        _write(self.case_dir / "plan-review.json", {"reviewer_role": "repair-plan-reviewer", "decision": "REJECTED"})
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            validate_stage(self.root, self.contract, "repair-plan-reviewer")

    def test_role_string_without_attestation_is_not_identity(self) -> None:
        _write(
            self.case_dir / "plan-review.json",
            {"reviewer_role": "repair-plan-reviewer", "decision": "APPROVED"},
        )
        with self.assertRaisesRegex(ValueError, "manifest|stage"):
            validate_stage(self.root, self.contract, "repair-plan-reviewer")


if __name__ == "__main__":
    unittest.main()
