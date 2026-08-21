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
    RUNTIME_STATUS_BLOCKED,
    RUNTIME_STATUS_END,
    RUNTIME_STATUS_WAITING_EXTERNAL,
    build_langgraph_workflow,
    initial_workflow_state,
    resume_workflow_state,
)
from provider_adapters import EventDrivenCIProviderAdapter  # type: ignore
from publication_provider_adapters import (  # type: ignore
    CodeReviewProviderAdapter,
    PublicationHostResult,
)
from workflow_dispatcher import ProviderAdapterRegistry, WorkflowAdapterDispatcher  # type: ignore


HEAD_SHA = "a" * 40
MERGE_SHA = "b" * 40


class AllowWriteGuard:
    def __init__(self) -> None:
        self.calls = []

    def assert_allowed(self, *, binding, step, state):
        self.calls.append((binding.capability_id, step.step_id))


class AllowMergeGuard:
    def __init__(self) -> None:
        self.calls = []

    def assert_merge_allowed(self, *, binding, step, state, request):
        self.calls.append((binding.capability_id, step.step_id, request["merge_grant_ref"]))


class FakeCodeReviewHost:
    def __init__(self) -> None:
        self.merge_calls = []

    def create_pull_request(self, *, request, step, state):
        raise AssertionError("governed-merge workflow must not create a pull request")

    def merge_pull_request(self, *, request, step, state):
        self.merge_calls.append(dict(request))
        return PublicationHostResult(
            receipt={
                "pull_request_number": request["pull_request_number"],
                "head_sha": request["head_sha"],
                "merge_commit_sha": MERGE_SHA,
                "merge_grant_ref": request["merge_grant_ref"],
            },
            evidence_refs=("github-merge:77",),
        )


class GovernedMergeWorkflowTest(unittest.TestCase):
    @property
    def repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="governed-merge-workflow-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def build_runtime(self):
        workspace = self.workspace()
        assembly = CompositionBootstrap(self.repo_root).assemble("governed-merge-github")
        write_guard = AllowWriteGuard()
        merge_guard = AllowMergeGuard()
        host = FakeCodeReviewHost()
        registry = ProviderAdapterRegistry(
            [
                EventDrivenCIProviderAdapter(
                    workspace=workspace,
                    provider_id="github.actions",
                ),
                CodeReviewProviderAdapter(
                    workspace=workspace,
                    provider_id="github.code_review",
                    host=host,
                    merge_authority_guard=merge_guard,
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
        return graph, assembly, host, write_guard, merge_guard

    @staticmethod
    def target_ref():
        return {
            "external_handles": {
                "ci.run.wait": {
                    "correlation_ref": "github-actions-run-9001",
                    "resume_event": "ci.completed",
                }
            },
            "publication_requests": {
                "merge": {
                    "capability_id": "code_review.pull_request.merge",
                    "pull_request_number": 77,
                    "head_sha": HEAD_SHA,
                    "merge_method": "squash",
                    "merge_grant_ref": "merge-grant:77",
                }
            },
        }

    def test_composition_binds_provider_neutral_capabilities_to_github(self) -> None:
        _, assembly, _, _, _ = self.build_runtime()
        self.assertTrue(assembly.ready)
        self.assertEqual(assembly.composition.workflow_id, "governed-merge")
        bindings = {
            row.capability_id: row.provider_id
            for row in assembly.activation.capability_preflight.required_bindings
        }
        self.assertEqual(bindings["ci.run.wait"], "github.actions")
        self.assertEqual(bindings["code_review.pull_request.merge"], "github.code_review")
        self.assertTrue(assembly.composition.write_authority_required)
        self.assertEqual(assembly.composition.completion_authority, "TaskRun")

    def test_green_ci_resumes_then_requires_both_write_and_merge_authority(self) -> None:
        graph, assembly, host, write_guard, merge_guard = self.build_runtime()
        first = graph.invoke(
            initial_workflow_state(
                workflow_id=assembly.activation.workflow.workflow_id,
                task_id="task-governed-merge",
                target_ref=self.target_ref(),
            )
        )

        self.assertEqual(first["runtime_status"], RUNTIME_STATUS_WAITING_EXTERNAL)
        self.assertEqual(first["current_stage"], "wait-ci")
        self.assertEqual(first["next_action"], "RESUME_ON_EXTERNAL_EVENT")
        self.assertEqual(first["external_wait"]["provider"], "github.actions")
        self.assertEqual(first["external_wait"]["correlation_ref"], "github-actions-run-9001")
        self.assertEqual(host.merge_calls, [])

        resumed = resume_workflow_state(
            first,
            external_event={
                "provider": "github.actions",
                "correlation_ref": "github-actions-run-9001",
                "event": "ci.completed",
                "conclusion": "success",
                "evidence_refs": ["github-actions-run:9001"],
            },
        )
        final = graph.invoke(resumed)

        self.assertEqual(final["runtime_status"], RUNTIME_STATUS_END)
        self.assertEqual(final["current_stage"], "merge")
        self.assertEqual(final["next_action"], "EVALUATE_COMPLETION_POLICY")
        self.assertIn("github-actions-run:9001", final["evidence_refs"])
        self.assertIn("github-merge:77", final["evidence_refs"])
        self.assertEqual(len(host.merge_calls), 1)
        self.assertEqual(write_guard.calls, [("code_review.pull_request.merge", "merge")])
        self.assertEqual(
            merge_guard.calls,
            [("code_review.pull_request.merge", "merge", "merge-grant:77")],
        )
        self.assertEqual(
            final["step_results"]["merge"][-1]["payload"]["receipt"]["merge_commit_sha"],
            MERGE_SHA,
        )

    def test_red_ci_blocks_before_merge(self) -> None:
        graph, assembly, host, write_guard, merge_guard = self.build_runtime()
        first = graph.invoke(
            initial_workflow_state(
                workflow_id=assembly.activation.workflow.workflow_id,
                task_id="task-governed-merge-red",
                target_ref=self.target_ref(),
            )
        )
        resumed = resume_workflow_state(
            first,
            external_event={
                "provider": "github.actions",
                "correlation_ref": "github-actions-run-9001",
                "event": "ci.completed",
                "conclusion": "failure",
                "evidence_refs": ["github-actions-run:9001:red"],
            },
        )
        final = graph.invoke(resumed)

        self.assertEqual(final["runtime_status"], RUNTIME_STATUS_BLOCKED)
        self.assertEqual(final["current_stage"], "wait-ci")
        self.assertEqual(host.merge_calls, [])
        self.assertEqual(write_guard.calls, [])
        self.assertEqual(merge_guard.calls, [])
        self.assertIn("github-actions-run:9001:red", final["evidence_refs"])


if __name__ == "__main__":
    unittest.main()
