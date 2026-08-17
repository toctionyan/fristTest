from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


class G6ResumeWorkflowContractTests(unittest.TestCase):
    def _text(self, name: str) -> str:
        return (WORKFLOWS / name).read_text(encoding="utf-8")

    def test_both_g6_orchestrators_delegate_approval_wait_classification(self) -> None:
        for name in (
            "governed-ci-repair-governance.yml",
            "governed-ci-existing-candidate-solo-governance.yml",
        ):
            text = self._text(name)
            with self.subTest(workflow=name):
                self.assertIn("github_repair_exact_head_state.py", text)
                self.assertIn("EXACT_HEAD_CI_AWAITING_APPROVAL", text)
                self.assertIn("EXACT_HEAD_CI_PASSED", text)
                self.assertIn("Do not rerun governance or baseline acceptance", text)

    def test_resume_path_cannot_repeat_governance_or_baseline_mutation(self) -> None:
        text = self._text("governed-ci-repair-exact-head-resume.yml")
        self.assertIn("github_repair_exact_head_state.py", text)
        self.assertIn("github_repair_exact_head.py", text)
        self.assertIn("EXACT_HEAD_CI_AWAITING_APPROVAL", text)
        self.assertNotIn("github_repair_baseline_acceptance.py", text)
        self.assertNotIn("github_repair_governance.py", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("contents: write", text)

    def test_merge_workflow_consumes_machine_grant_not_comment_authority(self) -> None:
        text = self._text("governed-ci-repair-merge.yml")
        self.assertIn("github_repair_merge_grant.py", text)
        self.assertIn("AUTHORIZE_EXACT_HEAD_MERGE", text)
        self.assertIn(".head.sha == $head", text)
        self.assertIn(".base.sha == $base", text)
        self.assertIn(".parents[0].sha == $base", text)
        self.assertIn(".parents[1].sha == $head", text)
        self.assertIn("grant_consumed:true", text)
        self.assertIn("deploy_allowed:false", text)
        self.assertIn("production_closed:false", text)
        self.assertNotIn("production-certification-release", text)
        self.assertNotIn("release.yml", text)

    def test_single_script_owner_for_merge_grant_issuance(self) -> None:
        owners = []
        for path in (ROOT / "scripts").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if '"MERGE_GRANT_ISSUED"' in text:
                owners.append(path.name)
        self.assertEqual(owners, ["github_repair_merge_grant.py"])


if __name__ == "__main__":
    unittest.main()
