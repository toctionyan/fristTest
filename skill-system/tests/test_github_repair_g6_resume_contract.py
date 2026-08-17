from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / "scripts"


class G6ResumeWorkflowContractTests(unittest.TestCase):
    def _text(self, name: str) -> str:
        return (WORKFLOWS / name).read_text(encoding="utf-8")

    def _script_owners(self, marker: str) -> list[str]:
        owners: list[str] = []
        for path in SCRIPTS.glob("*.py"):
            if marker in path.read_text(encoding="utf-8"):
                owners.append(path.name)
        return sorted(owners)

    def test_all_g6_orchestrators_delegate_approval_wait_classification(self) -> None:
        for name in (
            "governed-ci-repair-governance.yml",
            "governed-ci-repair-solo-governance.yml",
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
        self.assertIn('governed-ci-repair-governance"', text)
        self.assertIn('governed-ci-repair-solo-governance"', text)
        self.assertIn('governed-ci-existing-candidate-solo-governance"', text)
        self.assertNotIn("github_repair_baseline_acceptance.py", text)
        self.assertNotIn("github_repair_governance.py", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("contents: write", text)

    def test_merge_workflow_consumes_machine_grant_not_comment_authority(self) -> None:
        text = self._text("governed-ci-repair-merge.yml")
        self.assertIn("github_repair_merge_grant.py", text)
        self.assertIn("AUTHORIZE_EXACT_HEAD_MERGE", text)
        self.assertIn('governed-ci-repair-governance"', text)
        self.assertIn('governed-ci-repair-solo-governance"', text)
        self.assertIn('governed-ci-existing-candidate-solo-governance"', text)
        self.assertIn(".head.sha == $head", text)
        self.assertIn("git/ref/heads/${BASE_BRANCH}", text)
        self.assertIn(".object.sha == $base", text)
        self.assertNotIn(".base.sha == $base", text)
        self.assertIn(".parents[0].sha == $base", text)
        self.assertIn(".parents[1].sha == $head", text)
        self.assertIn("grant_consumed:true", text)
        self.assertIn("deploy_allowed:false", text)
        self.assertIn("production_closed:false", text)
        self.assertNotIn("production-certification-release", text)
        self.assertNotIn("release.yml", text)

    def test_single_authority_owner_per_lifecycle_transition(self) -> None:
        self.assertEqual(
            self._script_owners("def accept_baseline("),
            ["github_repair_baseline_acceptance.py"],
        )
        self.assertEqual(
            self._script_owners("def classify_exact_head_ci("),
            ["github_repair_exact_head_state.py"],
        )
        self.assertEqual(
            self._script_owners("def finalize_exact_head("),
            ["github_repair_exact_head.py"],
        )
        self.assertEqual(
            self._script_owners("def issue_merge_grant("),
            ["github_repair_merge_grant.py"],
        )

    def test_single_script_owner_for_merge_grant_issuance(self) -> None:
        owners = []
        for path in SCRIPTS.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if '"MERGE_GRANT_ISSUED"' in text:
                owners.append(path.name)
        self.assertEqual(owners, ["github_repair_merge_grant.py"])


if __name__ == "__main__":
    unittest.main()
