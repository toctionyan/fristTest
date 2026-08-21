from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from composition_bootstrap import CompositionBootstrap  # type: ignore
from langgraph_workflow_runtime import (  # type: ignore
    RUNTIME_STATUS_END,
    RUNTIME_STATUS_WAITING_EXTERNAL,
    build_langgraph_workflow,
    initial_workflow_state,
    resume_workflow_state,
)
from post_merge_validation_provider_adapter import (  # type: ignore
    GovernedPostMergeValidationProviderAdapter,
    PostMergeValidationHostResult,
)
from provider_adapters import EventDrivenCIProviderAdapter  # type: ignore
from publication_provider_adapters import (  # type: ignore
    CodeReviewProviderAdapter,
    LocalGitProviderAdapter,
    PublicationHostResult,
)
from request_dataflow_provider_adapter import RequestDataflowProviderAdapter  # type: ignore
from workflow_dispatcher import ProviderAdapterRegistry, WorkflowAdapterDispatcher  # type: ignore


BASE_SHA = "1" * 40
COMMIT_SHA = "2" * 40
MERGE_SHA = "3" * 40
PR_NUMBER = 2067


class AllowWriteGuard:
    def __init__(self) -> None:
        self.calls = []

    def assert_allowed(self, *, binding, step, state):
        self.calls.append((binding.capability_id, step.step_id))


class AllowMergeGuard:
    def __init__(self) -> None:
        self.calls = []

    def assert_merge_allowed(self, *, binding, step, state, request):
        self.calls.append(
            (
                binding.capability_id,
                step.step_id,
                request["pull_request_number"],
                request["head_sha"],
                request["merge_grant_ref"],
            )
        )


class FakeGitHost:
    def __init__(self) -> None:
        self.calls = []

    def create_commit(self, *, request, step, state):
        self.calls.append(dict(request))
        return PublicationHostResult(
            receipt={
                "commit_sha": COMMIT_SHA,
                "parent_sha": request["expected_parent_sha"],
                "changed_paths": list(request["changed_paths"]),
            },
            evidence_refs=("git-commit:publication",),
        )


class FakeCodeReviewHost:
    def __init__(self) -> None:
        self.create_calls = []
        self.merge_calls = []

    def create_pull_request(self, *, request, step, state):
        self.create_calls.append(dict(request))
        return PublicationHostResult(
            receipt={
                "pull_request_number": PR_NUMBER,
                "pull_request_url": f"https://example.invalid/pr/{PR_NUMBER}",
                "base_branch": request["base_branch"],
                "head_branch": request["head_branch"],
                "head_sha": request["head_sha"],
                "draft": request["draft"],
            },
            evidence_refs=(f"pull-request:{PR_NUMBER}",),
        )

    def merge_pull_request(self, *, request, step, state):
        self.merge_calls.append(dict(request))
        return PublicationHostResult(
            receipt={
                "pull_request_number": request["pull_request_number"],
                "head_sha": request["head_sha"],
                "merge_commit_sha": MERGE_SHA,
                "merge_grant_ref": request["merge_grant_ref"],
            },
            evidence_refs=(f"merge:{PR_NUMBER}",),
        )


class FakePostMergeHost:
    def __init__(self) -> None:
        self.calls = []

    def request_validation(self, *, source_pr_number, merge_sha, step, state):
        self.calls.append((source_pr_number, merge_sha))
        return PostMergeValidationHostResult(
            receipt={
                "status": "POST_MERGE_VALIDATION_REQUESTED",
                "source_pr_number": source_pr_number,
                "merge_sha": merge_sha,
                "correlation_ref": f"post-merge:{source_pr_number}:{merge_sha}",
            },
            evidence_refs=(f"post-merge-request:{source_pr_number}",),
        )


class PublicationE2EWorkflowTest(unittest.TestCase):
    @property
    def repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="publication-e2e-workflow-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    @staticmethod
    def target_ref():
        return {
            "external_handles": {
                "ci.run.wait": {
                    "correlation_ref": "ci:publication-e2e",
                    "resume_event": "ci.completed",
                }
            },
            "publication_requests": {
                "commit": {
                    "capability_id": "vcs.commit.create",
                    "expected_parent_sha": BASE_SHA,
                    "message": "feat: publication e2e candidate",
                    "changed_paths": ["skill-system/example.txt"],
                },
                "create-pr": {
                    "capability_id": "code_review.pull_request.create",
                    "base_branch": "main",
                    "head_branch": "feat/publication-e2e-candidate",
                    "title": "feat: publication e2e candidate",
                    "body": "Publication E2E candidate",
                    "draft": False,
                    "from_steps": {
                        "head_sha": {"step_id": "commit", "path": "commit_sha"}
                    },
                },
                "merge": {
                    "capability_id": "code_review.pull_request.merge",
                    "merge_method": "squash",
                    "merge_grant_ref": "merge-grant:publication-e2e",
                    "from_steps": {
                        "pull_request_number": {
                            "step_id": "create-pr",
                            "path": "receipt.pull_request_number",
                        },
                        "head_sha": {"step_id": "commit", "path": "commit_sha"},
                    },
                },
                "request-post-merge": {
                    "capability_id": "publication.post_merge.validation.request",
                    "from_steps": {
                        "source_pr_number": {
                            "step_id": "create-pr",
                            "path": "receipt.pull_request_number",
                        },
                        "merge_sha": {
                            "step_id": "merge",
                            "path": "receipt.merge_commit_sha",
                        },
                    },
                },
                "wait-post-merge": {
                    "capability_id": "publication.post_merge.validation.wait",
                    "resume_event": "post_merge.validation.completed",
                    "from_steps": {
                        "source_pr_number": {
                            "step_id": "create-pr",
                            "path": "receipt.pull_request_number",
                        },
                        "merge_sha": {
                            "step_id": "merge",
                            "path": "receipt.merge_commit_sha",
                        },
                        "correlation_ref": {
                            "step_id": "request-post-merge",
                            "path": "correlation_ref",
                        },
                    },
                },
            },
        }

    def build_runtime(self):
        workspace = self.workspace()
        assembly = CompositionBootstrap(self.repo_root).assemble("publication-e2e-github")
        write_guard = AllowWriteGuard()
        merge_guard = AllowMergeGuard()
        git_host = FakeGitHost()
        code_review_host = FakeCodeReviewHost()
        post_merge_host = FakePostMergeHost()
        code_review = CodeReviewProviderAdapter(
            workspace=workspace,
            provider_id="github.code_review",
            host=code_review_host,
            merge_authority_guard=merge_guard,
        )
        registry = ProviderAdapterRegistry(
            [
                LocalGitProviderAdapter(workspace=workspace, host=git_host),
                RequestDataflowProviderAdapter(code_review),
                EventDrivenCIProviderAdapter(
                    workspace=workspace,
                    provider_id="github.actions",
                ),
                GovernedPostMergeValidationProviderAdapter(
                    workspace=workspace,
                    host=post_merge_host,
                ),
            ]
        )
        dispatcher = WorkflowAdapterDispatcher(
            provider_adapters=registry,
            write_authority_guard=write_guard,
        )
        graph = build_langgraph_workflow(
            workflow=assembly.activation.workflow,
            activation=assembly.activation,
            dispatcher=dispatcher,
        )
        return (
            graph,
            assembly,
            git_host,
            code_review_host,
            post_merge_host,
            write_guard,
            merge_guard,
        )

    def test_composition_binds_complete_provider_neutral_publication_chain(self) -> None:
        _, assembly, *_ = self.build_runtime()
        self.assertTrue(assembly.ready)
        self.assertEqual(assembly.composition.workflow_id, "publication-e2e")
        bindings = {
            row.capability_id: row.provider_id
            for row in assembly.activation.capability_preflight.required_bindings
        }
        self.assertEqual(bindings["vcs.commit.create"], "local.git")
        self.assertEqual(bindings["code_review.pull_request.create"], "github.code_review")
        self.assertEqual(bindings["ci.run.wait"], "github.actions")
        self.assertEqual(bindings["code_review.pull_request.merge"], "github.code_review")
        self.assertEqual(
            bindings["publication.post_merge.validation.request"],
            "github.governed_validation",
        )
        self.assertEqual(
            bindings["publication.post_merge.validation.wait"],
            "github.governed_validation",
        )
        self.assertTrue(assembly.composition.write_authority_required)
        self.assertEqual(assembly.composition.completion_authority, "TaskRun")

    def test_commit_pr_ci_merge_post_merge_validation_reaches_workflow_end_only(self) -> None:
        (
            graph,
            assembly,
            git_host,
            code_review_host,
            post_merge_host,
            write_guard,
            merge_guard,
        ) = self.build_runtime()
        first = graph.invoke(
            initial_workflow_state(
                workflow_id=assembly.activation.workflow.workflow_id,
                task_id="task-publication-e2e",
                target_ref=self.target_ref(),
            )
        )

        self.assertEqual(first["runtime_status"], RUNTIME_STATUS_WAITING_EXTERNAL)
        self.assertEqual(first["current_stage"], "wait-ci")
        self.assertEqual(first["external_wait"]["provider"], "github.actions")
        self.assertEqual(first["external_wait"]["correlation_ref"], "ci:publication-e2e")
        self.assertEqual(git_host.calls[0]["expected_parent_sha"], BASE_SHA)
        self.assertEqual(code_review_host.create_calls[0]["head_sha"], COMMIT_SHA)
        self.assertEqual(code_review_host.merge_calls, [])

        after_ci = graph.invoke(
            resume_workflow_state(
                first,
                external_event={
                    "provider": "github.actions",
                    "correlation_ref": "ci:publication-e2e",
                    "event": "ci.completed",
                    "conclusion": "success",
                    "evidence_refs": ["ci-run:publication-e2e"],
                },
            )
        )

        self.assertEqual(after_ci["runtime_status"], RUNTIME_STATUS_WAITING_EXTERNAL)
        self.assertEqual(after_ci["current_stage"], "wait-post-merge")
        self.assertEqual(after_ci["external_wait"]["provider"], "github.governed_validation")
        self.assertEqual(code_review_host.merge_calls[0]["pull_request_number"], PR_NUMBER)
        self.assertEqual(code_review_host.merge_calls[0]["head_sha"], COMMIT_SHA)
        self.assertEqual(post_merge_host.calls, [(PR_NUMBER, MERGE_SHA)])
        correlation = after_ci["external_wait"]["correlation_ref"]
        self.assertEqual(correlation, f"post-merge:{PR_NUMBER}:{MERGE_SHA}")

        final_receipt = {
            "schema": "governed-repair-post-merge-validation@1",
            "status": "POST_MERGE_VALIDATED",
            "source_pr_number": PR_NUMBER,
            "merge_sha": MERGE_SHA,
            "quality_run_id": 88001,
            "convergence_run_id": 88002,
            "evidence": [
                "quality-run:88001",
                "project-convergence-run:88002",
                f"post-merge-receipt:{MERGE_SHA}",
            ],
            "authority_effect": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
        final = graph.invoke(
            resume_workflow_state(
                after_ci,
                external_event={
                    "provider": "github.governed_validation",
                    "correlation_ref": correlation,
                    "event": "post_merge.validation.completed",
                    "conclusion": "success",
                    "evidence_refs": [f"post-merge-artifact:{MERGE_SHA}"],
                    "receipt": final_receipt,
                },
            )
        )

        self.assertEqual(final["runtime_status"], RUNTIME_STATUS_END)
        self.assertEqual(final["current_stage"], "wait-post-merge")
        self.assertEqual(final["next_action"], "EVALUATE_COMPLETION_POLICY")
        self.assertIn("git-commit:publication", final["evidence_refs"])
        self.assertIn(f"pull-request:{PR_NUMBER}", final["evidence_refs"])
        self.assertIn("ci-run:publication-e2e", final["evidence_refs"])
        self.assertIn(f"merge:{PR_NUMBER}", final["evidence_refs"])
        self.assertIn(f"post-merge-artifact:{MERGE_SHA}", final["evidence_refs"])
        self.assertIn(f"post-merge-receipt:{MERGE_SHA}", final["evidence_refs"])
        self.assertEqual(
            write_guard.calls,
            [
                ("vcs.commit.create", "commit"),
                ("code_review.pull_request.create", "create-pr"),
                ("code_review.pull_request.merge", "merge"),
                ("publication.post_merge.validation.request", "request-post-merge"),
            ],
        )
        self.assertEqual(
            merge_guard.calls,
            [
                (
                    "code_review.pull_request.merge",
                    "merge",
                    PR_NUMBER,
                    COMMIT_SHA,
                    "merge-grant:publication-e2e",
                )
            ],
        )
        final_payload = final["step_results"]["wait-post-merge"][-1]["payload"]
        self.assertEqual(final_payload["status"], "POST_MERGE_VALIDATED")
        self.assertFalse(final_payload["authority_effect"])
        self.assertFalse(final_payload["completion_authority_changed"])
        self.assertFalse(final_payload["production_closed"])


if __name__ == "__main__":
    unittest.main()
