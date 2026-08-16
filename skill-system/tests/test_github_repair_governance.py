from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import github_repair_baseline_acceptance as baseline_acceptance  # noqa: E402
import github_repair_exact_head as exact_head  # noqa: E402
import github_repair_governance as governance  # noqa: E402
import verify_product_source_baseline as baseline_verify  # noqa: E402


PRE_G6 = {
    "G0_SCOPE_AUTHORITY": {"status": "PASS"},
    "G1_CONTRACT_PROJECTION": {"status": "PASS"},
    "G2_SEMANTIC_INVARIANT": {"status": "PASS"},
    "G3_MUTATION": {"status": "PASS"},
    "G4_FINAL_AUTHORITY": {"status": "PASS"},
    "G5_INTEGRATION_CERTIFICATION": {"status": "PASS"},
    "G6_GOVERNANCE_EXACT_HEAD": {"status": "PENDING"},
}


class FakeTaskRunStore:
    instances: list["FakeTaskRunStore"] = []

    def __init__(self, path: Path, payload: dict[str, object]) -> None:
        self.path = path
        self.payload = payload
        self.calls: list[tuple[str, object]] = []
        FakeTaskRunStore.instances.append(self)

    def mark_condition(self, name: str, **kwargs: object) -> None:
        self.calls.append(("mark_condition", name))

    def checkpoint(self, **kwargs: object) -> None:
        self.calls.append(("checkpoint", kwargs.get("phase")))
        self.payload["status"] = kwargs.get("status")
        self.payload["phase"] = kwargs.get("phase")

    def complete(self, **kwargs: object) -> None:
        self.calls.append(("complete", kwargs))
        self.payload["status"] = "COMPLETED"


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


class GovernedRepairGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeTaskRunStore.instances.clear()

    def _publication(self) -> dict[str, object]:
        return {
            "schema": "github-governed-repair-draft-publication@1",
            "status": "DRAFT_REPAIR_PR_PUBLISHED_AWAITING_GOVERNANCE",
            "governed_repair_state": "GOVERNANCE_REQUIRED",
            "repository": "owner/repo",
            "source_run_id": "42",
            "draft_pr_url": "https://github.com/owner/repo/pull/99",
            "repair_branch": "governed-repair/quality-42",
            "repair_base_branch": "main",
            "published_source_sha": "a" * 40,
            "validated_tree_sha": "b" * 40,
            "rca_sha256": "c" * 64,
            "write_grant_sha256": "d" * 64,
            "violated_invariant": "INV-001",
            "authority_owner": "deterministic-owner",
            "required_permanent_guard": "machine gate",
            "changed_paths": ["services/agent-service/src/agent_core/example.py"],
            "draft_pr_published": True,
            "gates": dict(PRE_G6),
            "governance_closed": False,
            "baseline_accepted": False,
            "exact_head_certified": False,
            "ready_for_review": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }

    def test_governance_closes_before_baseline_and_never_enables_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            publication = root / "publication.json"
            task = root / "task.json"
            output = root / "governance.json"
            _write_json(publication, self._publication())
            _write_json(task, {"status": "WAITING_EXTERNAL_RESULT", "phase": "STAGE4_GOVERNANCE_REQUIRED"})
            with patch.object(governance, "TaskRunStore", FakeTaskRunStore):
                result = governance.close_governance(
                    publication_receipt_path=publication,
                    task_run_path=task,
                    actor="reviewer",
                    approval_ref="approval:123",
                    output_path=output,
                )
        self.assertTrue(result["governance_closed"])
        self.assertFalse(result["baseline_accepted"])
        self.assertFalse(result["exact_head_certified"])
        self.assertFalse(result["ready_for_review"])
        self.assertFalse(result["merge_allowed"])
        self.assertFalse(result["deploy_allowed"])
        self.assertFalse(result["production_closed"])
        self.assertEqual(
            result["gates"]["G6_GOVERNANCE_EXACT_HEAD"]["status"],
            "GOVERNANCE_CLOSED_BASELINE_PENDING",
        )

    def test_governance_rejects_missing_pre_g6_gate(self) -> None:
        publication = self._publication()
        publication["gates"] = dict(PRE_G6)
        publication["gates"].pop("G3_MUTATION")  # type: ignore[index]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            publication_path = root / "publication.json"
            task = root / "task.json"
            _write_json(publication_path, publication)
            _write_json(task, {"status": "WAITING_EXTERNAL_RESULT", "phase": "STAGE4_GOVERNANCE_REQUIRED"})
            with patch.object(governance, "TaskRunStore", FakeTaskRunStore):
                with self.assertRaises(governance.GovernanceError):
                    governance.close_governance(
                        publication_receipt_path=publication_path,
                        task_run_path=task,
                        actor="reviewer",
                        approval_ref="approval:123",
                        output_path=root / "out.json",
                    )

    def _repo_with_baseline(self, *, two_changed: bool = False) -> tuple[tempfile.TemporaryDirectory[str], Path, list[str], str]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        protected = root / "services/agent-service/src"
        protected.mkdir(parents=True)
        first = protected / "a.py"
        second = protected / "b.py"
        first.write_text("A = 1\n", encoding="utf-8")
        second.write_text("B = 1\n", encoding="utf-8")
        baseline_path = root / "skill-system/registry/product-source-baseline.json"
        baseline_path.parent.mkdir(parents=True)
        baseline = {
            "schema_version": 2,
            "generated_at": "2026-08-16T00:00:00+00:00",
            "generated_from": "git:" + "0" * 40,
            "protected_roots": ["services/agent-service/src"],
            "file_count": 2,
            "files": {
                "services/agent-service/src/a.py": _hash(first),
                "services/agent-service/src/b.py": _hash(second),
            },
        }
        _write_json(baseline_path, baseline)
        _git(root, "init", "-q")
        _git(root, "config", "user.name", "Test")
        _git(root, "config", "user.email", "test@example.com")
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "baseline")
        first.write_text("A = 2\n", encoding="utf-8")
        changed = ["services/agent-service/src/a.py"]
        if two_changed:
            second.write_text("B = 2\n", encoding="utf-8")
            changed.append("services/agent-service/src/b.py")
        _git(root, "add", "services/agent-service/src")
        _git(root, "commit", "-qm", "governed source")
        source_sha = _git(root, "rev-parse", "HEAD")
        return temp, root, changed, source_sha

    @staticmethod
    def _governance_receipt(source_sha: str, approved: list[str]) -> dict[str, object]:
        gates = dict(PRE_G6)
        gates["G6_GOVERNANCE_EXACT_HEAD"] = {
            "status": "GOVERNANCE_CLOSED_BASELINE_PENDING"
        }
        result: dict[str, object] = {
            "schema": "governed-repair-governance@1",
            "status": "GOVERNANCE_CLOSED",
            "governed_repair_state": "GOVERNANCE_CLOSED",
            "repository": "owner/repo",
            "source_run_id": "42",
            "draft_pr_url": "https://github.com/owner/repo/pull/99",
            "repair_branch": "governed-repair/quality-42",
            "repair_base_branch": "main",
            "published_source_sha": source_sha,
            "validated_tree_sha": "f" * 40,
            "rca_sha256": "c" * 64,
            "write_grant_sha256": "d" * 64,
            "governance_actor": "reviewer",
            "approval_ref": "approval:123",
            "approved_source_paths": approved,
            "gates": gates,
            "governance_closed": True,
            "baseline_accepted": False,
            "exact_head_certified": False,
            "ready_for_review": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
        result["governance_sha256"] = baseline_acceptance._fingerprint(result)
        return result

    def test_baseline_acceptance_allows_only_exact_governed_drift(self) -> None:
        temp, root, changed, source_sha = self._repo_with_baseline()
        self.addCleanup(temp.cleanup)
        evidence = root / ".git/governed-repair-test-evidence"
        governance_path = evidence / "governance.json"
        task = evidence / "task.json"
        output = evidence / "acceptance.json"
        _write_json(governance_path, self._governance_receipt(source_sha, changed))
        _write_json(task, {"status": "WAITING_EXTERNAL_RESULT", "phase": "STAGE5_BASELINE_ACCEPTANCE_REQUIRED"})
        with patch.object(baseline_acceptance, "TaskRunStore", FakeTaskRunStore):
            receipt = baseline_acceptance.accept_baseline(
                workspace=root,
                governance_path=governance_path,
                task_run_path=task,
                output_path=output,
            )
        self.assertTrue(receipt["baseline_accepted"])
        self.assertFalse(receipt["exact_head_certified"])
        self.assertEqual(_git(root, "rev-parse", "HEAD^"), source_sha)
        verified = baseline_verify.verify(root, require_parent_binding=True)
        self.assertEqual(verified["status"], "PASS")
        self.assertEqual(verified["drift_paths"], [])

    def test_baseline_acceptance_rejects_unapproved_extra_drift(self) -> None:
        temp, root, changed, source_sha = self._repo_with_baseline(two_changed=True)
        self.addCleanup(temp.cleanup)
        evidence = root / ".git/governed-repair-test-evidence"
        governance_path = evidence / "governance.json"
        task = evidence / "task.json"
        output = evidence / "acceptance.json"
        _write_json(governance_path, self._governance_receipt(source_sha, changed[:1]))
        _write_json(task, {"status": "WAITING_EXTERNAL_RESULT", "phase": "STAGE5_BASELINE_ACCEPTANCE_REQUIRED"})
        with patch.object(baseline_acceptance, "TaskRunStore", FakeTaskRunStore):
            with self.assertRaises(baseline_acceptance.BaselineAcceptanceError):
                baseline_acceptance.accept_baseline(
                    workspace=root,
                    governance_path=governance_path,
                    task_run_path=task,
                    output_path=output,
                )

    def _baseline_receipt_for_exact_head(self, exact_sha: str) -> dict[str, object]:
        gates = dict(PRE_G6)
        gates["G6_GOVERNANCE_EXACT_HEAD"] = {
            "status": "BASELINE_ACCEPTED_EXACT_HEAD_PENDING"
        }
        result: dict[str, object] = {
            "schema": "governed-baseline-acceptance@1",
            "status": "BASELINE_ACCEPTED",
            "governed_repair_state": "BASELINE_ACCEPTED",
            "repository": "owner/repo",
            "source_run_id": "42",
            "draft_pr_url": "https://github.com/owner/repo/pull/99",
            "repair_branch": "governed-repair/quality-42",
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
            "gates": gates,
            "governance_closed": True,
            "baseline_accepted": True,
            "exact_head_certified": False,
            "ready_for_review": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
        result["baseline_acceptance_sha256"] = exact_head._fingerprint(result)
        return result

    def test_exact_head_requires_both_mandatory_workflows_on_same_sha(self) -> None:
        exact_sha = "9" * 40
        baseline = self._baseline_receipt_for_exact_head(exact_sha)
        ci = {
            "schema": "governed-repair-exact-head-ci@1",
            "head_sha": exact_sha,
            "pr_url": baseline["draft_pr_url"],
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
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline_path = root / "baseline.json"
            ci_path = root / "ci.json"
            task = root / "task.json"
            _write_json(baseline_path, baseline)
            _write_json(ci_path, ci)
            _write_json(task, {"status": "WAITING_EXTERNAL_RESULT", "phase": "STAGE6_EXACT_HEAD_CERTIFICATION_REQUIRED"})
            with patch.object(exact_head, "TaskRunStore", FakeTaskRunStore):
                result = exact_head.finalize_exact_head(
                    baseline_receipt_path=baseline_path,
                    ci_evidence_path=ci_path,
                    task_run_path=task,
                    output_path=root / "final.json",
                )
        self.assertEqual(result["status"], "READY_FOR_REVIEW")
        self.assertTrue(result["exact_head_certified"])
        self.assertTrue(result["ready_for_review"])
        self.assertEqual(result["gates"]["G6_GOVERNANCE_EXACT_HEAD"]["status"], "PASS")
        self.assertFalse(result["merge_allowed"])
        self.assertFalse(result["deploy_allowed"])
        self.assertFalse(result["production_closed"])

    def test_exact_head_rejects_workflow_from_other_sha(self) -> None:
        exact_sha = "9" * 40
        baseline = self._baseline_receipt_for_exact_head(exact_sha)
        ci = {
            "schema": "governed-repair-exact-head-ci@1",
            "head_sha": exact_sha,
            "pr_url": baseline["draft_pr_url"],
            "pr_number": 99,
            "pr_is_draft": True,
            "pr_head_sha": exact_sha,
            "workflows": {
                "quality": {
                    "run_id": "100",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": "8" * 40,
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
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline_path = root / "baseline.json"
            ci_path = root / "ci.json"
            task = root / "task.json"
            _write_json(baseline_path, baseline)
            _write_json(ci_path, ci)
            _write_json(task, {"status": "WAITING_EXTERNAL_RESULT", "phase": "STAGE6_EXACT_HEAD_CERTIFICATION_REQUIRED"})
            with patch.object(exact_head, "TaskRunStore", FakeTaskRunStore):
                with self.assertRaisesRegex(
                    exact_head.ExactHeadError,
                    "workflow quality ran on the wrong SHA",
                ):
                    exact_head.finalize_exact_head(
                        baseline_receipt_path=baseline_path,
                        ci_evidence_path=ci_path,
                        task_run_path=task,
                        output_path=root / "final.json",
                    )


if __name__ == "__main__":
    unittest.main()
