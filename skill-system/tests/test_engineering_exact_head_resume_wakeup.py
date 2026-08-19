from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class EngineeringExactHeadResumeWakeupTests(unittest.TestCase):
    def test_manual_resume_remains_backward_compatible(self) -> None:
        source = (ROOT / ".github/workflows/governed-ci-repair-exact-head-resume.yml").read_text(encoding="utf-8")
        self.assertIn("RESUME_EXACT_HEAD_CERTIFICATION", source)
        self.assertIn('[[ "${ACTOR}" == "${OWNER}" ]]', source)
        self.assertIn("explicit_owner_resume", source)

    def test_same_task_owner_preauthorization_is_verified_before_resume(self) -> None:
        source = (ROOT / ".github/workflows/governed-ci-repair-exact-head-resume.yml").read_text(encoding="utf-8")
        self.assertIn("preauthorized_merge_grant_run_id", source)
        self.assertIn("engineering-merge-grant-${task_fp}", source)
        self.assertIn("github_engineering_solo_governance_preauth.py", source)
        verify = source.index("github_engineering_solo_governance_preauth.py")
        reobserve = source.index("Re-observe exact-head workflows after approval")
        finalize = source.index("Finalize G6 without repeating baseline acceptance")
        self.assertLess(verify, reobserve)
        self.assertLess(reobserve, finalize)
        self.assertNotIn("github_repair_baseline_acceptance.py", source)

    def test_wakeup_only_runs_after_real_pr_workflow_success(self) -> None:
        source = (ROOT / ".github/workflows/engineering-exact-head-resume-wakeup.yml").read_text(encoding="utf-8")
        self.assertIn("- quality", source)
        self.assertIn("- skill-self-validation", source)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", source)
        self.assertIn("github.event.workflow_run.event == 'pull_request'", source)
        self.assertIn('$quality.status == "completed" and $quality.conclusion == "success"', source)
        self.assertIn('$skill.status == "completed" and $skill.conclusion == "success"', source)
        self.assertNotIn("action_required", source)
        self.assertNotIn("approve", source.lower())

    def test_wakeup_requires_exact_prior_approval_wait_and_suppresses_completed_resume(self) -> None:
        source = (ROOT / ".github/workflows/engineering-exact-head-resume-wakeup.yml").read_text(encoding="utf-8")
        self.assertIn('status == "EXACT_HEAD_CI_AWAITING_APPROVAL"', source)
        self.assertIn("resume_required == true", source)
        self.assertIn("baseline_mutation_allowed == false", source)
        self.assertIn("governed-ci-repair-exact-head-resume-${G6_RUN_ID}-${G6_RUN_ATTEMPT}", source)
        self.assertIn("replay suppressed", source)

    def test_resume_dispatch_is_same_task_preauthorized_and_replay_stable(self) -> None:
        source = (ROOT / ".github/workflows/engineering-exact-head-resume-wakeup.yml").read_text(encoding="utf-8")
        self.assertIn("engineering-merge-grant-${task_fp}", source)
        self.assertIn("github_engineering_solo_governance_preauth.py", source)
        self.assertIn('context="engineering-exact-head-resume/${G6_RUN_ID}/${G6_RUN_ATTEMPT}/${TASK_FP:0:12}"', source)
        self.assertIn("reservation_state=UNCERTAIN", source)
        self.assertIn("preauthorized_merge_grant_run_id=${{ steps.grant.outputs.authorize_run_id }}", source)
        self.assertIn("preauthorized_merge_grant_run_attempt=${{ steps.grant.outputs.authorize_run_attempt }}", source)

    def test_resume_automation_has_no_merge_deploy_or_production_authority(self) -> None:
        wakeup = (ROOT / ".github/workflows/engineering-exact-head-resume-wakeup.yml").read_text(encoding="utf-8")
        resume = (ROOT / ".github/workflows/governed-ci-repair-exact-head-resume.yml").read_text(encoding="utf-8")
        self.assertNotIn("gh pr merge", wakeup)
        self.assertNotIn("/pulls/", wakeup)
        self.assertNotIn("/deployments", wakeup)
        self.assertIn("merge_allowed == false", wakeup)
        self.assertIn("deploy_allowed == false", wakeup)
        self.assertIn("production_closed == false", wakeup)
        self.assertIn("merge_allowed == false", resume)
        self.assertIn("deploy_allowed == false", resume)
        self.assertIn("production_closed == false", resume)


if __name__ == "__main__":
    unittest.main()
