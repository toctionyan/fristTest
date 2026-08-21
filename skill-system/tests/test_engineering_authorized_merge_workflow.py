from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class EngineeringAuthorizedMergeWorkflowTests(unittest.TestCase):
    def test_merge_policy_defaults_to_bounded_auto_merge_and_autonomy_authorize_does_not_merge(self) -> None:
        source = (ROOT / ".github/workflows/engineering-autonomy-authorize.yml").read_text(encoding="utf-8")
        self.assertIn("merge_policy:", source)
        self.assertIn("default: bounded-auto-merge", source)
        self.assertIn("- disabled", source)
        self.assertIn("- bounded-auto-merge", source)
        self.assertIn("if: ${{ inputs.merge_policy == 'bounded-auto-merge' }}", source)
        self.assertIn("engineering-merge-grant-", source)
        self.assertIn("AutonomyGrant merge_allowed: \\`false\\`", source)
        self.assertNotIn("gh pr merge", source)
        self.assertNotIn("/pulls/${SOURCE_PR_NUMBER}/merge", source)

    def test_final_merge_only_wakes_after_existing_g6_workflows(self) -> None:
        source = (ROOT / ".github/workflows/engineering-authorized-merge.yml").read_text(encoding="utf-8")
        for workflow in (
            "governed-ci-repair-governance",
            "governed-ci-repair-solo-governance",
            "governed-ci-repair-exact-head-resume",
        ):
            self.assertIn(f"- {workflow}", source)
        self.assertIn("exact-head-result.json", source)
        self.assertIn("EXACT_HEAD_CI_PASSED", source)
        self.assertIn("engineering-merge-grant-${TASK_FP}", source)
        self.assertIn("github_engineering_authorized_merge.py gate", source)
        self.assertIn("github_engineering_authorized_merge.py request", source)

    def test_final_landings_are_serialized_repository_wide(self) -> None:
        source = (ROOT / ".github/workflows/engineering-authorized-merge.yml").read_text(encoding="utf-8")
        self.assertIn("group: engineering-authorized-merge-${{ github.repository }}", source)
        self.assertNotIn("group: engineering-authorized-merge-${{ github.repository }}-${{ github.event.workflow_run.id }}", source)
        self.assertIn("statuses: write", source)
        self.assertIn("cancel-in-progress: false", source)

    def test_transient_mergeability_and_running_push_evidence_are_waited_not_immediately_failed(self) -> None:
        source = (ROOT / ".github/workflows/engineering-authorized-merge.yml").read_text(encoding="utf-8")
        self.assertIn("deadline=$((SECONDS + 1800))", source)
        self.assertIn('mergeable=$(jq -r \'.mergeable // "unknown"\'', source)
        self.assertIn("waiting_push_runs=$(jq -r", source)
        self.assertIn('select(.event == "push")', source)
        self.assertIn('select(.status != "completed")', source)
        self.assertIn('if [[ "${mergeable}" == "true" && "${waiting_push_runs}" == "0" ]]', source)
        self.assertIn("timed out waiting for final mergeability/current-head push evidence", source)

    def test_single_use_grant_is_reserved_after_cas_request_and_before_merge(self) -> None:
        source = (ROOT / ".github/workflows/engineering-authorized-merge.yml").read_text(encoding="utf-8")
        request = source.index("Re-read exact PR and compile CAS merge request")
        reserve = source.index("Reserve single-use MergeGrant consumption")
        merge = source.index("Execute exact-head merge-commit request")
        finalize = source.index("Finalize single-use MergeGrant consumption")
        self.assertLess(request, reserve)
        self.assertLess(reserve, merge)
        self.assertLess(merge, finalize)
        self.assertIn("github_engineering_merge_consumption.py", source)
        self.assertIn('[[ "${state}" == "RESERVABLE" ]]', source)
        self.assertIn("single-use autonomous merge grant reserved", source)
        self.assertIn("single-use merge grant consumed", source)
        self.assertIn("new authority required", source)

    def test_final_network_mutation_is_exact_merge_commit_only(self) -> None:
        source = (ROOT / ".github/workflows/engineering-authorized-merge.yml").read_text(encoding="utf-8")
        self.assertIn('method=$(jq -r \'.body.merge_method\'', source)
        self.assertIn('[[ "${expected_head}" == "${{ steps.g6.outputs.exact_head }}" && "${method}" == "merge" ]]', source)
        self.assertIn('gh api -X PUT "${path}" --input -', source)
        self.assertNotIn("--admin", source)
        self.assertNotIn("squash", source.lower())
        self.assertNotIn("rebase", source.lower())
        self.assertNotIn("deployment", source.lower())

    def test_core_autonomy_grant_remains_merge_forbidden(self) -> None:
        source = (ROOT / "skill-system/controller/autonomy_grant.py").read_text(encoding="utf-8")
        self.assertIn('"merge",', source)
        self.assertIn('"merge_allowed": False', source)


if __name__ == "__main__":
    unittest.main()
