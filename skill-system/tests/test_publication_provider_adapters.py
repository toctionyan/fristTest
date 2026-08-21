from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from capability_registry import CapabilityBinding  # type: ignore
from publication_provider_adapters import (  # type: ignore
    CodeReviewProviderAdapter,
    LocalGitProviderAdapter,
    PublicationHostResult,
)
from workflow_graph_contract import WorkflowStepSpec  # type: ignore


PARENT_SHA = "1" * 40
COMMIT_SHA = "2" * 40
HEAD_SHA = "3" * 40
MERGE_SHA = "4" * 40


class FakeLocalGitHost:
    def __init__(self) -> None:
        self.calls = []

    def create_commit(self, *, request, step, state):
        self.calls.append((dict(request), step.step_id, dict(state)))
        return PublicationHostResult(
            receipt={
                "commit_sha": COMMIT_SHA,
                "parent_sha": request["expected_parent_sha"],
                "changed_paths": list(request["changed_paths"]),
            },
            evidence_refs=("git-commit:2",),
        )


class FakeCodeReviewHost:
    def __init__(self) -> None:
        self.create_calls = []
        self.merge_calls = []

    def create_pull_request(self, *, request, step, state):
        self.create_calls.append((dict(request), step.step_id, dict(state)))
        return PublicationHostResult(
            receipt={
                "pull_request_number": 77,
                "pull_request_url": "https://example.invalid/pull/77",
                "base_branch": request["base_branch"],
                "head_branch": request["head_branch"],
                "head_sha": request["head_sha"],
                "draft": request["draft"],
            },
            evidence_refs=("pr:77",),
        )

    def merge_pull_request(self, *, request, step, state):
        self.merge_calls.append((dict(request), step.step_id, dict(state)))
        return PublicationHostResult(
            receipt={
                "pull_request_number": request["pull_request_number"],
                "head_sha": request["head_sha"],
                "merge_commit_sha": MERGE_SHA,
                "merge_grant_ref": request["merge_grant_ref"],
            },
            evidence_refs=("merge:77",),
        )


class AllowMergeGuard:
    def __init__(self) -> None:
        self.calls = []

    def assert_merge_allowed(self, *, binding, step, state, request):
        self.calls.append((binding.capability_id, step.step_id, dict(request)))


class PublicationProviderAdaptersTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="publication-provider-adapters-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    @staticmethod
    def step(step_id: str, capability: str) -> WorkflowStepSpec:
        return WorkflowStepSpec(
            step_id=step_id,
            step_type="executor",
            use=capability,
            routes={"green": "END", "blocked": "BLOCKED_UNRECOVERABLE"},
            max_attempts=4,
        )

    @staticmethod
    def binding(capability: str, *, provider_id: str, provider_type: str) -> CapabilityBinding:
        return CapabilityBinding(
            capability_id=capability,
            provider_id=provider_id,
            provider_type=provider_type,
            activation_key=provider_id,
            mutates=True,
            external_wait=False,
        )

    def test_local_git_commit_requires_exact_parent_scope_and_evidence(self) -> None:
        workspace = self.workspace()
        host = FakeLocalGitHost()
        adapter = LocalGitProviderAdapter(workspace=workspace, host=host)
        result = adapter.invoke(
            binding=self.binding(
                "vcs.commit.create",
                provider_id="local.git",
                provider_type="executor",
            ),
            step=self.step("commit", "vcs.commit.create"),
            state={
                "task_id": "task-publication",
                "target_ref": {
                    "publication_requests": {
                        "commit": {
                            "capability_id": "vcs.commit.create",
                            "expected_parent_sha": PARENT_SHA,
                            "message": "feat: governed publication",
                            "changed_paths": ["skill-system/controller/example.py"],
                        }
                    }
                },
            },
        )

        self.assertEqual(result.outcome, "green")
        self.assertEqual(result.payload["commit_sha"], COMMIT_SHA)
        self.assertEqual(result.payload["parent_sha"], PARENT_SHA)
        self.assertEqual(result.payload["changed_paths"], ["skill-system/controller/example.py"])
        self.assertIn("git-commit:2", result.evidence_refs)
        self.assertFalse(result.payload["completion_authority_changed"])
        self.assertFalse(result.payload["production_closed"])
        self.assertEqual(len(host.calls), 1)

    def test_code_review_create_pr_validates_exact_head_identity(self) -> None:
        workspace = self.workspace()
        host = FakeCodeReviewHost()
        adapter = CodeReviewProviderAdapter(
            workspace=workspace,
            provider_id="github.code_review",
            host=host,
        )
        result = adapter.invoke(
            binding=self.binding(
                "code_review.pull_request.create",
                provider_id="github.code_review",
                provider_type="integration",
            ),
            step=self.step("create-pr", "code_review.pull_request.create"),
            state={
                "task_id": "task-publication",
                "target_ref": {
                    "publication_requests": {
                        "create-pr": {
                            "base_branch": "main",
                            "head_branch": "feat/governed-publication",
                            "head_sha": HEAD_SHA,
                            "title": "feat: governed publication",
                            "body": "publication adapter validation",
                            "draft": True,
                        }
                    }
                },
            },
        )

        self.assertEqual(result.outcome, "green")
        self.assertEqual(result.payload["receipt"]["pull_request_number"], 77)
        self.assertEqual(result.payload["receipt"]["head_sha"], HEAD_SHA)
        self.assertIn("pr:77", result.evidence_refs)
        self.assertEqual(len(host.create_calls), 1)

    def test_merge_fails_closed_without_independent_merge_authority(self) -> None:
        workspace = self.workspace()
        host = FakeCodeReviewHost()
        adapter = CodeReviewProviderAdapter(
            workspace=workspace,
            provider_id="github.code_review",
            host=host,
        )
        result = adapter.invoke(
            binding=self.binding(
                "code_review.pull_request.merge",
                provider_id="github.code_review",
                provider_type="integration",
            ),
            step=self.step("merge", "code_review.pull_request.merge"),
            state={
                "task_id": "task-publication",
                "target_ref": {
                    "publication_requests": {
                        "merge": {
                            "pull_request_number": 77,
                            "head_sha": HEAD_SHA,
                            "merge_method": "squash",
                            "merge_grant_ref": "grant:77",
                        }
                    }
                },
            },
        )

        self.assertEqual(result.outcome, "blocked")
        self.assertIn("MergeAuthorityGuard", result.payload["error"])
        self.assertEqual(host.merge_calls, [])
        self.assertEqual(len(result.evidence_refs), 1)

    def test_merge_uses_independent_grant_and_returns_exact_merge_receipt(self) -> None:
        workspace = self.workspace()
        host = FakeCodeReviewHost()
        guard = AllowMergeGuard()
        adapter = CodeReviewProviderAdapter(
            workspace=workspace,
            provider_id="github.code_review",
            host=host,
            merge_authority_guard=guard,
        )
        result = adapter.invoke(
            binding=self.binding(
                "code_review.pull_request.merge",
                provider_id="github.code_review",
                provider_type="integration",
            ),
            step=self.step("merge", "code_review.pull_request.merge"),
            state={
                "task_id": "task-publication",
                "target_ref": {
                    "publication_requests": {
                        "merge": {
                            "pull_request_number": 77,
                            "head_sha": HEAD_SHA,
                            "merge_method": "squash",
                            "merge_grant_ref": "grant:77",
                        }
                    }
                },
            },
        )

        self.assertEqual(result.outcome, "green")
        self.assertEqual(result.payload["receipt"]["merge_commit_sha"], MERGE_SHA)
        self.assertEqual(result.payload["receipt"]["merge_grant_ref"], "grant:77")
        self.assertIn("merge:77", result.evidence_refs)
        self.assertEqual(len(guard.calls), 1)
        self.assertEqual(len(host.merge_calls), 1)
        self.assertFalse(result.payload["authority_effect"])
        self.assertFalse(result.payload["completion_authority_changed"])


if __name__ == "__main__":
    unittest.main()
