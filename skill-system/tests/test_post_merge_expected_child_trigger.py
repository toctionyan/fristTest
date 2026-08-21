from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-post-merge-validation.yml"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

import execution_progress  # noqa: E402


class PostMergeExpectedChildTriggerTests(unittest.TestCase):
    def test_owner_direct_merge_to_main_auto_starts_post_merge_validator(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("pull_request_target:", text)
        self.assertIn("types: [closed]", text)
        self.assertIn("github.event.pull_request.merged == true", text)
        self.assertIn("github.event.pull_request.base.ref == 'main'", text)
        self.assertIn('EVENT_NAME: ${{ github.event_name }}', text)
        self.assertIn('MERGED_PR_NUMBER: ${{ github.event.pull_request.number }}', text)
        self.assertIn('MERGED_PR_SHA: ${{ github.event.pull_request.merge_commit_sha }}', text)
        self.assertIn('MERGED_PR_BASE: ${{ github.event.pull_request.base.ref }}', text)
        self.assertIn('MERGED_PR_MERGED: ${{ github.event.pull_request.merged }}', text)
        self.assertIn('MERGED_PR_MERGED_BY: ${{ github.event.pull_request.merged_by.login }}', text)
        self.assertIn('elif [[ "${EVENT_NAME}" == "pull_request_target" ]]; then', text)
        self.assertIn('[[ "${MERGED_PR_MERGED}" == "true" ]]', text)
        self.assertIn('[[ "${MERGED_PR_BASE}" == "main" ]]', text)
        self.assertIn('[[ "${MERGED_PR_MERGED_BY}" == "${OWNER}" ]]', text)
        self.assertIn('merge_sha="${MERGED_PR_SHA}"', text)
        self.assertIn('pr_number="${MERGED_PR_NUMBER}"', text)
        self.assertIn('authorizing_actor="${MERGED_PR_MERGED_BY}"', text)

        # The trigger consumes only trusted event metadata. The control plane itself
        # must still come from main, and the untrusted PR head must never be checked
        # out before the existing independent merge-lineage verifier runs.
        self.assertIn("Checkout trusted post-merge control plane", text)
        self.assertIn("          ref: main", text)
        self.assertIn("          persist-credentials: false", text)
        self.assertNotIn("ref: ${{ github.event.pull_request.head", text)
        self.assertIn("github.event.pull_request.merge_commit_sha || inputs.merge_sha", text)

        # Keep the workflow_run path because merges produced by GITHUB_TOKEN-backed
        # governed workflows may not emit a second downstream PR event.
        self.assertIn("workflow_run:", text)
        self.assertIn("- governed-ci-repair-merge", text)

    def test_validator_announces_exact_child_run_identity_before_long_child_work(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("Record post-merge validation start", text)
        self.assertIn("Post-merge validation started for exact merge", text)
        self.assertIn('in workflow run \\`${GITHUB_RUN_ID}\\`', text)
        self.assertLess(
            text.index("Record post-merge validation start"),
            text.index("Dispatch Quality on the exact merge ref"),
        )

        # This comment is discovery transport only. Existing governance keeps the
        # immutable validation receipt as the authority and tolerates comment write
        # failure without converting it into validation success.
        self.assertIn("COMMENT_WRITE_FAILED_NON_AUTHORITATIVE", text)
        self.assertIn("non-authoritative start comment could not be written; validation continues", text)

    def test_missing_expected_child_evidence_is_pending_not_running(self) -> None:
        progress = execution_progress.build_execution_progress(
            planned_stages=[
                {
                    "id": "canonical-merge",
                    "label": "canonical merge",
                    "status": "PASS",
                    "evidence_ref": "commit:d82cd617",
                },
                {
                    "id": "post-merge-validation",
                    "label": "post-merge validation",
                    "status": "PENDING",
                    "detail": "expected child has not been observed yet",
                },
            ]
        )

        self.assertEqual(progress["overall"], "PENDING")
        self.assertEqual(progress["current_stage"], "post-merge-validation")
        self.assertFalse(progress["recovery"]["active"])
        self.assertFalse(progress["human"]["required"])
        self.assertEqual(progress["summary"]["completed_steps"], 1)
        self.assertEqual(progress["summary"]["total_steps"], 2)


if __name__ == "__main__":
    unittest.main()
