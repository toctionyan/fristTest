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
from post_merge_validation_provider_adapter import (  # type: ignore
    GovernedPostMergeValidationProviderAdapter,
    PostMergeValidationHostResult,
)
from workflow_graph_contract import WorkflowStepSpec  # type: ignore


PR_NUMBER = 2073
MERGE_SHA = "d" * 40
RUN_ID = 99001
OTHER_RUN_ID = 99002
DIGEST = "a" * 64
CORRELATION = f"post-merge:{PR_NUMBER}:{MERGE_SHA}"


class FakePostMergeHost:
    def request_validation(self, *, source_pr_number, merge_sha, step, state):
        return PostMergeValidationHostResult(
            receipt={
                "status": "POST_MERGE_VALIDATION_REQUESTED",
                "source_pr_number": source_pr_number,
                "merge_sha": merge_sha,
                "correlation_ref": CORRELATION,
            },
            evidence_refs=(f"post-merge-request:{source_pr_number}",),
        )


class PostMergeChildIdentityTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="post-merge-child-identity-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    @staticmethod
    def binding() -> CapabilityBinding:
        return CapabilityBinding(
            capability_id="publication.post_merge.validation.wait",
            provider_id="github.governed_validation",
            provider_type="integration",
            activation_key="github.governed_validation",
            mutates=False,
            external_wait=True,
        )

    @staticmethod
    def step() -> WorkflowStepSpec:
        return WorkflowStepSpec(
            step_id="wait-post-merge",
            step_type="external_wait",
            use="publication.post_merge.validation.wait",
            routes={
                "pending": "WAITING_EXTERNAL",
                "green": "END",
                "red": "BLOCKED_UNRECOVERABLE",
                "blocked": "BLOCKED_UNRECOVERABLE",
            },
            max_attempts=4,
        )

    @staticmethod
    def target_ref() -> dict:
        return {
            "publication_requests": {
                "wait-post-merge": {
                    "capability_id": "publication.post_merge.validation.wait",
                    "source_pr_number": PR_NUMBER,
                    "merge_sha": MERGE_SHA,
                    "correlation_ref": CORRELATION,
                    "resume_event": "post_merge.validation.completed",
                }
            }
        }

    @staticmethod
    def final_receipt() -> dict:
        return {
            "schema": "governed-post-merge-validation@1",
            "status": "POST_MERGE_VALIDATED",
            "source_pr_number": PR_NUMBER,
            "merge_sha": MERGE_SHA,
            "merge_base_sha": "b" * 40,
            "merge_head_sha": "c" * 40,
            "quality_run_id": 99101,
            "quality_run_attempt": 1,
            "project_convergence_run_id": 99102,
            "project_convergence_run_attempt": 1,
            "authority_effect": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
            "post_merge_receipt_sha256": DIGEST,
        }

    def adapter(self) -> GovernedPostMergeValidationProviderAdapter:
        return GovernedPostMergeValidationProviderAdapter(
            workspace=self.workspace(),
            host=FakePostMergeHost(),
        )

    def test_missing_child_is_explicit_waiting_for_expected_child(self) -> None:
        result = self.adapter().invoke(
            binding=self.binding(),
            step=self.step(),
            state={
                "task_id": "task-post-merge-child",
                "target_ref": self.target_ref(),
            },
        )

        self.assertEqual(result.outcome, "pending")
        self.assertEqual(result.payload["status"], "WAITING_FOR_EXPECTED_CHILD")
        self.assertEqual(result.external_wait["wait_status"], "WAITING_FOR_EXPECTED_CHILD")
        self.assertEqual(
            result.external_wait["resume_event"],
            "post_merge.validation.child_discovered",
        )
        self.assertNotIn("workflow_run_id", result.external_wait)

    def test_child_discovery_transitions_to_running_with_exact_run_id(self) -> None:
        waiting = self.adapter().invoke(
            binding=self.binding(),
            step=self.step(),
            state={
                "task_id": "task-post-merge-child",
                "target_ref": self.target_ref(),
            },
        )

        result = self.adapter().invoke(
            binding=self.binding(),
            step=self.step(),
            state={
                "task_id": "task-post-merge-child",
                "resume_stage": "wait-post-merge",
                "target_ref": self.target_ref(),
                "external_wait": dict(waiting.external_wait),
                "external_event": {
                    "provider": "github.governed_validation",
                    "correlation_ref": CORRELATION,
                    "event": "post_merge.validation.child_discovered",
                    "workflow_run_id": RUN_ID,
                    "conclusion": "success",
                    "evidence_refs": [f"workflow-run:{RUN_ID}"],
                },
            },
        )

        self.assertEqual(result.outcome, "pending")
        self.assertEqual(result.payload["status"], "RUNNING")
        self.assertEqual(result.payload["workflow_run_id"], RUN_ID)
        self.assertEqual(result.external_wait["wait_status"], "RUNNING")
        self.assertEqual(result.external_wait["workflow_run_id"], RUN_ID)
        self.assertEqual(
            result.external_wait["resume_event"],
            "post_merge.validation.completed",
        )

    def test_completion_event_must_match_exact_child_run_id(self) -> None:
        running_handle = {
            "provider": "github.governed_validation",
            "correlation_ref": CORRELATION,
            "resume_event": "post_merge.validation.completed",
            "source_pr_number": PR_NUMBER,
            "merge_sha": MERGE_SHA,
            "workflow_run_id": RUN_ID,
            "wait_status": "RUNNING",
        }
        result = self.adapter().invoke(
            binding=self.binding(),
            step=self.step(),
            state={
                "task_id": "task-post-merge-child",
                "resume_stage": "wait-post-merge",
                "target_ref": self.target_ref(),
                "external_wait": running_handle,
                "external_event": {
                    "provider": "github.governed_validation",
                    "correlation_ref": CORRELATION,
                    "event": "post_merge.validation.completed",
                    "workflow_run_id": OTHER_RUN_ID,
                    "conclusion": "success",
                    "evidence_refs": [f"workflow-run:{OTHER_RUN_ID}"],
                    "receipt": self.final_receipt(),
                },
            },
        )

        self.assertEqual(result.outcome, "blocked")
        self.assertIn("workflow_run_id mismatch", result.payload["error"])
        self.assertFalse(result.payload["authority_effect"])
        self.assertFalse(result.payload["production_closed"])

    def test_unknown_completion_status_is_not_reported_as_running_or_success(self) -> None:
        running_handle = {
            "provider": "github.governed_validation",
            "correlation_ref": CORRELATION,
            "resume_event": "post_merge.validation.completed",
            "source_pr_number": PR_NUMBER,
            "merge_sha": MERGE_SHA,
            "workflow_run_id": RUN_ID,
            "wait_status": "RUNNING",
        }
        result = self.adapter().invoke(
            binding=self.binding(),
            step=self.step(),
            state={
                "task_id": "task-post-merge-child",
                "resume_stage": "wait-post-merge",
                "target_ref": self.target_ref(),
                "external_wait": running_handle,
                "external_event": {
                    "provider": "github.governed_validation",
                    "correlation_ref": CORRELATION,
                    "event": "post_merge.validation.completed",
                    "workflow_run_id": RUN_ID,
                    "conclusion": "",
                    "evidence_refs": [f"workflow-run:{RUN_ID}"],
                },
            },
        )

        self.assertEqual(result.outcome, "blocked")
        self.assertEqual(result.payload["status"], "STATUS_UNKNOWN")
        self.assertFalse(result.payload["authority_effect"])
        self.assertFalse(result.payload["production_closed"])


if __name__ == "__main__":
    unittest.main()
