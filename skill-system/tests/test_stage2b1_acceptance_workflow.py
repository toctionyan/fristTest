"""Contract tests for the explicit Stage2B1 acceptance workflow entrypoint."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-stage2b1-acceptance.yml"


class Stage2B1AcceptanceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_manual_and_explicitly_bound(self) -> None:
        self.assertIn("workflow_dispatch:", self.source)
        for field in (
            "source_run_id:",
            "source_run_attempt:",
            "acceptance_artifact_id:",
            "change_contract_digest:",
        ):
            self.assertIn(field, self.source)
        self.assertIn('ref: main', self.source)
        self.assertIn('[[ "${GITHUB_REF}" == "refs/heads/main" ]]', self.source)
        self.assertIn('name == "stage2b1-acceptance-inputs"', self.source)
        self.assertIn('(.workflow_run.id|tostring) == $run_id', self.source)

    def test_workflow_never_discovers_latest_artifacts_or_dispatches_workflows(self) -> None:
        forbidden = (
            "sort_by(.created_at)",
            "| .[0]",
            "latest",
            "gh workflow run",
            "workflow_dispatch --",
        )
        for fragment in forbidden:
            self.assertNotIn(fragment, self.source)
        self.assertIn("actions/artifacts/${ACCEPTANCE_ARTIFACT_ID}/zip", self.source)

    def test_workflow_is_read_only_against_repository_and_uses_existing_command(self) -> None:
        self.assertIn("contents: read", self.source)
        self.assertIn("actions: read", self.source)
        self.assertNotIn("contents: write", self.source)
        self.assertNotIn("git -C control push", self.source)
        self.assertIn("control/scripts/stage2b1_acceptance.py", self.source)
        self.assertIn("control/governance/active-change.json", self.source)
        self.assertIn("cmp incoming/active-change.before.sha256 incoming/active-change.after.sha256", self.source)
        self.assertIn("task-run.json", self.source)
        self.assertIn('conditions["stage-accepted"].satisfied == true', self.source)

    def test_input_archive_is_fail_closed(self) -> None:
        self.assertIn("acceptance artifact contains an unsafe path", self.source)
        self.assertIn("acceptance artifact contains a symlink", self.source)
        self.assertIn("acceptance artifact must contain exactly the required files", self.source)
        self.assertIn('stage2b1-acceptance-inputs@1', self.source)
        self.assertIn('set(names) != required', self.source)


if __name__ == "__main__":
    unittest.main()
