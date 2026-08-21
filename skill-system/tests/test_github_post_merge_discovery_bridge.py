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
from github_post_merge_discovery_bridge import (  # type: ignore
    GithubPostMergeDiscoveryError,
    build_child_discovered_event,
    select_expected_child_run_id,
)
from post_merge_validation_provider_adapter import GovernedPostMergeValidationProviderAdapter  # type: ignore
from workflow_graph_contract import WorkflowStepSpec  # type: ignore


REPOSITORY = "toctionyan/fristTest"
PR_NUMBER = 2075
MERGE_SHA = "e" * 40
RUN_ID = 32599900001
OTHER_RUN_ID = 32599900002
CORRELATION = f"post-merge:{PR_NUMBER}:{MERGE_SHA}"


def _comment(run_id: int, *, merge_sha: str = MERGE_SHA) -> dict:
    return {
        "body": (
            f"Post-merge validation started for exact merge `{merge_sha}` "
            f"in workflow run `{run_id}`. This stage is validation-only: "
            "authority_effect=false, merge_allowed=false, deploy_allowed=false, "
            "production_closed=false."
        )
    }


def _branch(run_id: int, *, merge_sha: str = MERGE_SHA) -> str:
    return f"governed-post-merge-validation/{merge_sha[:12]}-{run_id}"


def _run(run_id: int = RUN_ID) -> dict:
    return {
        "id": run_id,
        "run_attempt": 1,
        "name": "governed-ci-post-merge-validation",
        "status": "in_progress",
        "conclusion": None,
        "html_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
        "repository": {"full_name": REPOSITORY},
    }


class FakePostMergeHost:
    def request_validation(self, *, source_pr_number, merge_sha, step, state):  # pragma: no cover - wait only
        raise AssertionError("request_validation is not used by discovery bridge tests")


class GithubPostMergeDiscoveryBridgeTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="github-post-merge-discovery-"))
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

    def adapter(self) -> GovernedPostMergeValidationProviderAdapter:
        return GovernedPostMergeValidationProviderAdapter(
            workspace=self.workspace(),
            host=FakePostMergeHost(),
        )

    def test_no_locator_for_exact_merge_remains_undiscovered(self) -> None:
        self.assertIsNone(
            select_expected_child_run_id(
                comments=[{"body": "unrelated"}, _comment(RUN_ID, merge_sha="a" * 40)],
                branch_names=[_branch(RUN_ID, merge_sha="b" * 40)],
                merge_sha=MERGE_SHA,
            )
        )

    def test_exact_start_comment_locates_one_child_run_id(self) -> None:
        self.assertEqual(
            select_expected_child_run_id(
                comments=[{"body": "unrelated"}, _comment(RUN_ID)],
                merge_sha=MERGE_SHA,
            ),
            RUN_ID,
        )

    def test_live_exact_merge_branch_locates_child_when_comment_transport_is_missing(self) -> None:
        self.assertEqual(
            select_expected_child_run_id(
                comments=[],
                branch_names=[_branch(RUN_ID)],
                merge_sha=MERGE_SHA,
            ),
            RUN_ID,
        )

    def test_comment_and_live_ref_may_agree_on_same_child(self) -> None:
        self.assertEqual(
            select_expected_child_run_id(
                comments=[_comment(RUN_ID)],
                branch_names=[_branch(RUN_ID)],
                merge_sha=MERGE_SHA,
            ),
            RUN_ID,
        )

    def test_multiple_located_child_runs_fail_closed(self) -> None:
        with self.assertRaisesRegex(GithubPostMergeDiscoveryError, "multiple post-merge child"):
            select_expected_child_run_id(
                comments=[_comment(RUN_ID)],
                branch_names=[_branch(OTHER_RUN_ID)],
                merge_sha=MERGE_SHA,
            )

    def test_actual_fetched_run_builds_provider_neutral_discovery_event(self) -> None:
        event = build_child_discovered_event(
            run=_run(),
            expected_run_id=RUN_ID,
            repository=REPOSITORY,
            source_pr_number=PR_NUMBER,
            merge_sha=MERGE_SHA,
            correlation_ref=CORRELATION,
            evidence_refs=["github-ref:post-merge-live-child"],
        )

        self.assertEqual(event["event"], "post_merge.validation.child_discovered")
        self.assertEqual(event["provider"], "github.governed_validation")
        self.assertEqual(event["workflow_run_id"], RUN_ID)
        self.assertEqual(event["workflow_run_attempt"], 1)
        self.assertEqual(event["child_status"], "IN_PROGRESS")
        self.assertIsNone(event["child_conclusion"])
        self.assertEqual(event["conclusion"], "success")
        self.assertIn(
            f"github-workflow-run:{REPOSITORY}:{RUN_ID}:attempt:1",
            event["evidence_refs"],
        )
        self.assertFalse(event["authority_effect"])
        self.assertFalse(event["completion_authority_changed"])
        self.assertFalse(event["production_closed"])

    def test_fetched_run_must_match_located_run_and_repository(self) -> None:
        with self.assertRaisesRegex(GithubPostMergeDiscoveryError, "located expected child"):
            build_child_discovered_event(
                run=_run(OTHER_RUN_ID),
                expected_run_id=RUN_ID,
                repository=REPOSITORY,
                source_pr_number=PR_NUMBER,
                merge_sha=MERGE_SHA,
                correlation_ref=CORRELATION,
            )

        wrong_repo = _run()
        wrong_repo["repository"] = {"full_name": "someone/else"}
        with self.assertRaisesRegex(GithubPostMergeDiscoveryError, "repository does not match"):
            build_child_discovered_event(
                run=wrong_repo,
                expected_run_id=RUN_ID,
                repository=REPOSITORY,
                source_pr_number=PR_NUMBER,
                merge_sha=MERGE_SHA,
                correlation_ref=CORRELATION,
            )

    def test_live_ref_bridge_event_moves_adapter_to_exact_running_child(self) -> None:
        adapter = self.adapter()
        waiting = adapter.invoke(
            binding=self.binding(),
            step=self.step(),
            state={
                "task_id": "task-post-merge-discovery",
                "target_ref": self.target_ref(),
            },
        )
        self.assertEqual(waiting.payload["status"], "WAITING_FOR_EXPECTED_CHILD")

        expected_run_id = select_expected_child_run_id(
            comments=[],
            branch_names=[_branch(RUN_ID)],
            merge_sha=MERGE_SHA,
        )
        self.assertEqual(expected_run_id, RUN_ID)
        event = build_child_discovered_event(
            run=_run(),
            expected_run_id=expected_run_id,
            repository=REPOSITORY,
            source_pr_number=PR_NUMBER,
            merge_sha=MERGE_SHA,
            correlation_ref=CORRELATION,
        )
        running = adapter.invoke(
            binding=self.binding(),
            step=self.step(),
            state={
                "task_id": "task-post-merge-discovery",
                "resume_stage": "wait-post-merge",
                "target_ref": self.target_ref(),
                "external_wait": dict(waiting.external_wait),
                "external_event": event,
            },
        )

        self.assertEqual(running.outcome, "pending")
        self.assertEqual(running.payload["status"], "RUNNING")
        self.assertEqual(running.payload["workflow_run_id"], RUN_ID)
        self.assertEqual(running.external_wait["workflow_run_id"], RUN_ID)
        self.assertEqual(
            running.external_wait["resume_event"],
            "post_merge.validation.completed",
        )


if __name__ == "__main__":
    unittest.main()
