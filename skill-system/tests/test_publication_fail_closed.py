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
from workflow_step_values import WorkflowStepValueError, resolve_request_from_steps  # type: ignore


PR_NUMBER = 2067
MERGE_SHA = "3" * 40
OTHER_SHA = "4" * 40
DIGEST = "a" * 64
POST_MERGE_RUN_ID = 88000


class FakePostMergeHost:
    def request_validation(self, *, source_pr_number, merge_sha, step, state):
        return PostMergeValidationHostResult(
            receipt={
                "status": "POST_MERGE_VALIDATION_REQUESTED",
                "source_pr_number": source_pr_number,
                "merge_sha": merge_sha,
                "correlation_ref": f"post-merge:{source_pr_number}:{merge_sha}",
            },
            evidence_refs=(f"post-merge-request:{source_pr_number}",),
        )


class PublicationFailClosedTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="publication-fail-closed-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    @staticmethod
    def wait_step() -> WorkflowStepSpec:
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
    def wait_binding() -> CapabilityBinding:
        return CapabilityBinding(
            capability_id="publication.post_merge.validation.wait",
            provider_id="github.governed_validation",
            provider_type="integration",
            activation_key="github.governed_validation",
            mutates=False,
            external_wait=True,
        )

    @staticmethod
    def target_ref() -> dict:
        return {
            "publication_requests": {
                "wait-post-merge": {
                    "capability_id": "publication.post_merge.validation.wait",
                    "source_pr_number": PR_NUMBER,
                    "merge_sha": MERGE_SHA,
                    "correlation_ref": f"post-merge:{PR_NUMBER}:{MERGE_SHA}",
                    "resume_event": "post_merge.validation.completed",
                }
            }
        }

    @staticmethod
    def running_wait_handle() -> dict:
        return {
            "provider": "github.governed_validation",
            "correlation_ref": f"post-merge:{PR_NUMBER}:{MERGE_SHA}",
            "resume_event": "post_merge.validation.completed",
            "source_pr_number": PR_NUMBER,
            "merge_sha": MERGE_SHA,
            "workflow_run_id": POST_MERGE_RUN_ID,
            "wait_status": "RUNNING",
        }

    @staticmethod
    def valid_receipt() -> dict:
        return {
            "schema": "governed-post-merge-validation@1",
            "status": "POST_MERGE_VALIDATED",
            "source_pr_number": PR_NUMBER,
            "merge_sha": MERGE_SHA,
            "merge_base_sha": "5" * 40,
            "merge_head_sha": "6" * 40,
            "quality_run_id": 88001,
            "quality_run_attempt": 1,
            "project_convergence_run_id": 88002,
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

    def test_step_dataflow_rejects_missing_prior_step_result(self) -> None:
        with self.assertRaisesRegex(WorkflowStepValueError, "has no recorded result"):
            resolve_request_from_steps(
                {"step_results": {}},
                {
                    "from_steps": {
                        "head_sha": {"step_id": "commit", "path": "commit_sha"}
                    }
                },
            )

    def test_step_dataflow_rejects_conflicting_concrete_value(self) -> None:
        with self.assertRaisesRegex(WorkflowStepValueError, "conflicts with value resolved"):
            resolve_request_from_steps(
                {
                    "step_results": {
                        "commit": [
                            {
                                "payload": {
                                    "commit_sha": "2" * 40,
                                }
                            }
                        ]
                    }
                },
                {
                    "head_sha": "7" * 40,
                    "from_steps": {
                        "head_sha": {"step_id": "commit", "path": "commit_sha"}
                    },
                },
            )

    def test_post_merge_wait_does_not_consume_stale_event_from_other_stage(self) -> None:
        result = self.adapter().invoke(
            binding=self.wait_binding(),
            step=self.wait_step(),
            state={
                "task_id": "task-publication",
                "resume_stage": "wait-ci",
                "target_ref": self.target_ref(),
                "external_event": {
                    "provider": "github.governed_validation",
                    "correlation_ref": f"post-merge:{PR_NUMBER}:{MERGE_SHA}",
                    "event": "post_merge.validation.completed",
                    "conclusion": "success",
                    "evidence_refs": ["stale:event"],
                    "receipt": self.valid_receipt(),
                },
            },
        )
        self.assertEqual(result.outcome, "pending")
        self.assertIsNotNone(result.external_wait)
        self.assertEqual(result.external_wait["merge_sha"], MERGE_SHA)
        self.assertEqual(result.payload["status"], "WAITING_FOR_EXPECTED_CHILD")

    def test_post_merge_success_without_durable_evidence_fails_closed(self) -> None:
        result = self.adapter().invoke(
            binding=self.wait_binding(),
            step=self.wait_step(),
            state={
                "task_id": "task-publication",
                "resume_stage": "wait-post-merge",
                "target_ref": self.target_ref(),
                "external_wait": self.running_wait_handle(),
                "external_event": {
                    "provider": "github.governed_validation",
                    "correlation_ref": f"post-merge:{PR_NUMBER}:{MERGE_SHA}",
                    "event": "post_merge.validation.completed",
                    "workflow_run_id": POST_MERGE_RUN_ID,
                    "conclusion": "success",
                    "evidence_refs": [],
                    "receipt": self.valid_receipt(),
                },
            },
        )
        self.assertEqual(result.outcome, "blocked")
        self.assertIn("requires durable evidence_refs", result.payload["error"])
        self.assertFalse(result.payload["authority_effect"])
        self.assertFalse(result.payload["production_closed"])

    def test_post_merge_receipt_with_wrong_merge_sha_fails_closed(self) -> None:
        receipt = self.valid_receipt()
        receipt["merge_sha"] = OTHER_SHA
        result = self.adapter().invoke(
            binding=self.wait_binding(),
            step=self.wait_step(),
            state={
                "task_id": "task-publication",
                "resume_stage": "wait-post-merge",
                "target_ref": self.target_ref(),
                "external_wait": self.running_wait_handle(),
                "external_event": {
                    "provider": "github.governed_validation",
                    "correlation_ref": f"post-merge:{PR_NUMBER}:{MERGE_SHA}",
                    "event": "post_merge.validation.completed",
                    "workflow_run_id": POST_MERGE_RUN_ID,
                    "conclusion": "success",
                    "evidence_refs": ["post-merge:artifact"],
                    "receipt": receipt,
                },
            },
        )
        self.assertEqual(result.outcome, "blocked")
        self.assertIn("merge_sha mismatch", result.payload["error"])

    def test_post_merge_receipt_cannot_claim_production_closed(self) -> None:
        receipt = self.valid_receipt()
        receipt["production_closed"] = True
        result = self.adapter().invoke(
            binding=self.wait_binding(),
            step=self.wait_step(),
            state={
                "task_id": "task-publication",
                "resume_stage": "wait-post-merge",
                "target_ref": self.target_ref(),
                "external_wait": self.running_wait_handle(),
                "external_event": {
                    "provider": "github.governed_validation",
                    "correlation_ref": f"post-merge:{PR_NUMBER}:{MERGE_SHA}",
                    "event": "post_merge.validation.completed",
                    "workflow_run_id": POST_MERGE_RUN_ID,
                    "conclusion": "success",
                    "evidence_refs": ["post-merge:artifact"],
                    "receipt": receipt,
                },
            },
        )
        self.assertEqual(result.outcome, "blocked")
        self.assertIn("production_closed=false", result.payload["error"])
        self.assertFalse(result.payload["authority_effect"])
        self.assertFalse(result.payload["completion_authority_changed"])


if __name__ == "__main__":
    unittest.main()
