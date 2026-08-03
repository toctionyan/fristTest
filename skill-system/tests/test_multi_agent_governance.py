from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

import candidate_freeze

from agent_attestation import (  # type: ignore
    import_attestation,
    load_manifest,
    payload_digest,
    register_implementer,
    sign_attestation,
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
        sign_key: str | None = None,
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
        if sign_key:
            payload = sign_attestation(payload, sign_key)
        else:
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

    def test_reused_reviewer_thread_is_rejected(self) -> None:
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
                thread_id="shared-thread",
                worktree_id="worktree-failure",
                decision="PROVEN",
            ),
        )
        plan_review = self.root / "inbox/plan-review.json"
        _write(plan_review, {"reviewer_role": "repair-plan-reviewer", "decision": "APPROVED"})
        with self.assertRaisesRegex(ValueError, "thread reused"):
            import_attestation(
                self.root,
                self.contract,
                role="repair-plan-reviewer",
                artifact_source=plan_review,
                attestation_source=self._attestation(
                    role="repair-plan-reviewer",
                    artifact=plan_review,
                    task_id="task-plan",
                    thread_id="shared-thread",
                    worktree_id="worktree-plan",
                    decision="APPROVED",
                ),
            )

    def test_strict_signature_mode_rejects_unsigned_attestation(self) -> None:
        plan_review = self.root / "inbox/plan-review.json"
        _write(plan_review, {"reviewer_role": "repair-plan-reviewer", "decision": "APPROVED"})
        attestation = self._attestation(
            role="repair-plan-reviewer",
            artifact=plan_review,
            task_id="task-plan",
            thread_id="thread-plan",
            worktree_id="worktree-plan",
            decision="APPROVED",
        )
        old_required = os.environ.get("MULTI_AGENT_REQUIRE_SIGNATURE")
        old_key = os.environ.get("MULTI_AGENT_ATTESTATION_KEY")
        try:
            os.environ["MULTI_AGENT_REQUIRE_SIGNATURE"] = "1"
            os.environ["MULTI_AGENT_ATTESTATION_KEY"] = "trusted-test-key"
            with self.assertRaisesRegex(ValueError, "requires a trusted signature"):
                import_attestation(
                    self.root, self.contract, role="repair-plan-reviewer",
                    artifact_source=plan_review, attestation_source=attestation,
                )
        finally:
            if old_required is None:
                os.environ.pop("MULTI_AGENT_REQUIRE_SIGNATURE", None)
            else:
                os.environ["MULTI_AGENT_REQUIRE_SIGNATURE"] = old_required
            if old_key is None:
                os.environ.pop("MULTI_AGENT_ATTESTATION_KEY", None)
            else:
                os.environ["MULTI_AGENT_ATTESTATION_KEY"] = old_key

    def test_signed_attestation_passes_only_with_the_matching_key(self) -> None:
        plan_review = self.root / "inbox/plan-review.json"
        _write(plan_review, {"reviewer_role": "repair-plan-reviewer", "decision": "APPROVED"})
        attestation = self._attestation(
            role="repair-plan-reviewer",
            artifact=plan_review,
            task_id="task-plan",
            thread_id="thread-plan",
            worktree_id="worktree-plan",
            decision="APPROVED",
            sign_key="trusted-test-key",
        )
        old_required = os.environ.get("MULTI_AGENT_REQUIRE_SIGNATURE")
        old_key = os.environ.get("MULTI_AGENT_ATTESTATION_KEY")
        try:
            os.environ["MULTI_AGENT_REQUIRE_SIGNATURE"] = "1"
            os.environ["MULTI_AGENT_ATTESTATION_KEY"] = "wrong-key"
            with self.assertRaisesRegex(ValueError, "signature is invalid"):
                import_attestation(
                    self.root, self.contract, role="repair-plan-reviewer",
                    artifact_source=plan_review, attestation_source=attestation,
                )
            os.environ["MULTI_AGENT_ATTESTATION_KEY"] = "trusted-test-key"
            result = import_attestation(
                self.root, self.contract, role="repair-plan-reviewer",
                artifact_source=plan_review, attestation_source=attestation,
            )
            self.assertEqual(result["status"], "PASS")
        finally:
            if old_required is None:
                os.environ.pop("MULTI_AGENT_REQUIRE_SIGNATURE", None)
            else:
                os.environ["MULTI_AGENT_REQUIRE_SIGNATURE"] = old_required
            if old_key is None:
                os.environ.pop("MULTI_AGENT_ATTESTATION_KEY", None)
            else:
                os.environ["MULTI_AGENT_ATTESTATION_KEY"] = old_key

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

    def _commit_source_value(self, value: int, message: str) -> str:
        _write(self.root / "src/module.py", f"def value():\n    return {value}\n")
        subprocess.run(["git", "-C", str(self.root), "add", "src/module.py"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", message], check=True)
        return subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True
        ).strip()

    def _candidate_freeze_patches(self) -> ExitStack:
        stages = {
            "failure-explorer": {
                "task_id": "task-failure", "thread_id": "thread-failure",
                "worktree_id": "worktree-failure",
            },
            "repair-plan-reviewer": {
                "task_id": "task-plan", "thread_id": "thread-plan",
                "worktree_id": "worktree-plan",
            },
            "product-implementer": {
                "task_id": "task-implementation", "thread_id": "thread-implementation",
                "worktree_id": "worktree-implementation",
            },
        }
        manifest = {
            "schema_version": 1,
            "record_type": "agent-task-manifest",
            "change_id": "change-001",
            "repository": "example/repository",
            "baseline_commit": self.baseline,
            "stages": stages,
        }
        implementer = {"manifest": manifest, "stage": stages["product-implementer"]}
        chain = SimpleNamespace(
            permit={
                "allowed_paths": ["src/module.py"],
                "baseline_source_fingerprint": "0" * 64,
            },
            permit_digest="1" * 64,
        )
        stack = ExitStack()
        stack.enter_context(patch.object(candidate_freeze, "load_chain", return_value=chain))
        stack.enter_context(patch.object(candidate_freeze, "validate_stage", return_value=implementer))
        stack.enter_context(patch.object(candidate_freeze, "validate_role_separation"))
        stack.enter_context(patch.object(candidate_freeze, "load_manifest", return_value=manifest))
        stack.enter_context(
            patch.object(candidate_freeze, "manifest_path", return_value=self.case_dir / "agent-task-manifest.json")
        )
        stack.enter_context(patch.object(candidate_freeze, "case_dir_from_contract", return_value=self.case_dir))
        return stack

    def test_candidate_freeze_rejects_an_old_commit(self) -> None:
        candidate = self._commit_source_value(2, "candidate")
        _write(self.root / "governance/note.txt", "later governance change\n")
        subprocess.run(["git", "-C", str(self.root), "add", "governance/note.txt"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "later governance"], check=True)
        with self._candidate_freeze_patches():
            with self.assertRaisesRegex(ValueError, "equal the current HEAD"):
                candidate_freeze.freeze_candidate(self.root, self.contract, candidate_commit=candidate)

    def test_candidate_freeze_rejects_uncommitted_permitted_source(self) -> None:
        _write(self.root / "src/module.py", "def value():\n    return 2\n")
        with self._candidate_freeze_patches():
            with self.assertRaisesRegex(ValueError, "permitted source change to be committed"):
                candidate_freeze.freeze_candidate(
                    self.root, self.contract, candidate_commit=self.baseline
                )

    def test_candidate_freeze_rejects_source_change_after_freeze(self) -> None:
        candidate = self._commit_source_value(2, "candidate")
        with self._candidate_freeze_patches():
            candidate_freeze.freeze_candidate(self.root, self.contract, candidate_commit=candidate)
            _write(self.root / "src/module.py", "def value():\n    return 3\n")
            with self.assertRaisesRegex(ValueError, "governed source changed"):
                candidate_freeze.validate_candidate_freeze(self.root, self.contract)

    def test_candidate_freeze_allows_later_governance_commit_only(self) -> None:
        candidate = self._commit_source_value(2, "candidate")
        with self._candidate_freeze_patches():
            candidate_freeze.freeze_candidate(self.root, self.contract, candidate_commit=candidate)
            subprocess.run(["git", "-C", str(self.root), "add", "governance"], check=True)
            subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "record governance"], check=True)
            result = candidate_freeze.validate_candidate_freeze(self.root, self.contract)
            self.assertEqual(result["candidate_commit"], candidate)

    def test_role_string_without_attestation_is_not_identity(self) -> None:
        _write(
            self.case_dir / "plan-review.json",
            {"reviewer_role": "repair-plan-reviewer", "decision": "APPROVED"},
        )
        with self.assertRaisesRegex(ValueError, "manifest|stage"):
            validate_stage(self.root, self.contract, "repair-plan-reviewer")


if __name__ == "__main__":
    unittest.main()
