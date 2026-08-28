"""Contract tests for event-driven pull-request CI convergence."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "engineering-ci-convergence.yml"


class EngineeringCIConvergenceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_listens_to_required_workflows_and_publishes_one_status(self) -> None:
        self.assertIn("workflow_run:", self.source)
        self.assertIn("- quality", self.source)
        self.assertIn("- skill-self-validation", self.source)
        self.assertIn("types: [requested, in_progress, completed]", self.source)
        self.assertIn('EVENT_ACTION: ${{ github.event.action }}', self.source)
        self.assertIn('context "ci-convergence"', self.source)
        self.assertIn('"repos/${GITHUB_REPOSITORY}/statuses/${HEAD_SHA}"', self.source)
        self.assertIn("statuses: write", self.source)
        self.assertIn("pull-requests: read", self.source)

    def test_is_exact_head_and_fail_closed(self) -> None:
        self.assertIn("head_sha=${HEAD_SHA}&event=pull_request", self.source)
        self.assertIn("--trigger-run-id", self.source)
        self.assertIn("--trigger-run-attempt", self.source)
        self.assertIn("--trigger-workflow", self.source)
        self.assertIn('gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${WORKFLOW_RUN_ID}"', self.source)
        self.assertIn("ref: ${{ github.workflow_sha }}", self.source)
        self.assertIn("gh api --paginate", self.source)
        self.assertIn("jq -s 'map(.workflow_runs // []) | add'", self.source)
        self.assertIn("for retry in 1 2 3 4 5", self.source)
        self.assertIn("run_snapshot_found=false", self.source)
        self.assertIn("no status update published", self.source)
        self.assertIn("Ignoring stale pre-terminal workflow_run event", self.source)
        self.assertIn("Ignoring workflow_run completed event while the exact trigger run is not terminal", self.source)
        self.assertIn("multiple_distinct_runs_for_exact_head", (ROOT / "scripts" / "ci_convergence.py").read_text(encoding="utf-8"))
        self.assertNotIn("sort_by(.created_at)", self.source)
        self.assertNotIn("latest", self.source)
        self.assertNotIn(".[0]", self.source)

    def test_does_not_merge_or_touch_governance(self) -> None:
        self.assertNotIn("git push", self.source)
        self.assertNotIn("gh pr merge", self.source)
        self.assertNotIn("active-change.json", self.source)
        self.assertNotIn("workflow_dispatch", self.source)
        self.assertNotIn("contents: write", self.source)


if __name__ == "__main__":
    unittest.main()
