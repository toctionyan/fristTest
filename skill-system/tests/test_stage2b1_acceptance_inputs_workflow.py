"""Contract tests for the explicit Stage2B1 acceptance input producer."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-stage2b1-acceptance-inputs.yml"


class Stage2B1AcceptanceInputsWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_requires_explicit_source_and_all_input_artifacts(self) -> None:
        self.assertIn("workflow_dispatch:", self.source)
        for field in (
            "source_run_id:",
            "source_run_attempt:",
            "source_workflow_id:",
            "source_workflow_path:",
            "source_event:",
            "source_ref:",
            "source_head_sha:",
            "task_run_artifact_id:",
            "decision_artifact_id:",
            "expected_binding_artifact_id:",
            "change_contract_artifact_id:",
            "human_gate_artifact_id:",
            "human_decision_artifact_id:",
        ):
            self.assertIn(field, self.source)
        self.assertIn('[[ "${GITHUB_REF}" == "refs/heads/main" ]]', self.source)
        self.assertIn('(.workflow_run.id|tostring) == $run_id', self.source)
        self.assertIn('(.workflow_id|tostring) == $workflow_id', self.source)
        self.assertIn('refs/tags/*)', self.source)
        self.assertIn('expected_source_ref', self.source)
        self.assertIn('archive_digest', self.source)
        self.assertIn('content_digest', self.source)

    def test_workflow_does_not_discover_latest_or_run_acceptance(self) -> None:
        for forbidden in (
            "sort_by(.created_at)",
            "| .[0]",
            "latest",
            "gh workflow run",
            "stage2b1_acceptance.py",
            "active-change.json",
        ):
            self.assertNotIn(forbidden, self.source)
        self.assertIn("actions/artifacts/${artifact_id}/zip", self.source)
        self.assertIn("stage2b1_acceptance_inputs.py", self.source)

    def test_workflow_is_read_only_and_uploads_only_the_packaged_contract(self) -> None:
        self.assertIn("contents: read", self.source)
        self.assertIn("actions: read", self.source)
        self.assertNotIn("contents: write", self.source)
        self.assertNotIn("actions: write", self.source)
        self.assertIn("name: stage2b1-acceptance-inputs", self.source)
        self.assertIn("path: incoming/package", self.source)
        self.assertNotIn("TaskRunStore", self.source)

    def test_extracted_inputs_are_fail_closed(self) -> None:
        self.assertIn("input artifact must contain exactly one file", self.source)
        self.assertIn("input artifact contains an unsafe or unexpected path", self.source)
        self.assertIn("input artifact contains a symlink", self.source)
        self.assertIn("input artifact contains a non-regular file", self.source)
        self.assertIn("input artifact JSON must be an object", self.source)
        self.assertIn(".expired == false", self.source)
        self.assertIn(".digest", self.source)


if __name__ == "__main__":
    unittest.main()
