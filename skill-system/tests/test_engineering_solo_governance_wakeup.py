from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class EngineeringSoloGovernanceWakeupTests(unittest.TestCase):
    def test_manual_solo_acknowledgement_remains_backward_compatible(self) -> None:
        source = (ROOT / ".github/workflows/governed-ci-repair-solo-governance.yml").read_text(encoding="utf-8")
        self.assertIn("CLOSE_SOLO_GOVERNANCE_AND_ACCEPT_BASELINE", source)
        self.assertIn('if [[ "${GITHUB_ACTOR}" != "${GITHUB_REPOSITORY_OWNER}" ]]; then', source)
        self.assertIn("solo-owner-explicit-acknowledgement", source)

    def test_task_preauthorization_is_verified_before_governance_close(self) -> None:
        source = (ROOT / ".github/workflows/governed-ci-repair-solo-governance.yml").read_text(encoding="utf-8")
        self.assertIn("preauthorized_merge_grant_run_id", source)
        self.assertIn("github_engineering_solo_governance_preauth.py", source)
        verifier = source.index("github_engineering_solo_governance_preauth.py")
        close = source.index("github_repair_governance.py")
        self.assertLess(verifier, close)
        self.assertIn("task-bound-preauthorization", source)
        self.assertIn("independent_human_review:false", source)
        self.assertIn("merge_allowed:false", source)
        self.assertIn("deploy_allowed:false", source)
        self.assertIn("production_closed:false", source)

    def test_wakeup_never_bypasses_real_independent_reviewer_environment(self) -> None:
        source = (ROOT / ".github/workflows/engineering-solo-governance-wakeup.yml").read_text(encoding="utf-8")
        self.assertIn("governed-repair-governance", source)
        self.assertIn('select(.type == "required_reviewers")', source)
        self.assertIn(".prevent_self_review", source)
        self.assertIn("independent_human_gate=true", source)
        self.assertIn("solo preauthorization cannot bypass it", source)

    def test_wakeup_requires_exact_same_task_owner_grant(self) -> None:
        source = (ROOT / ".github/workflows/engineering-solo-governance-wakeup.yml").read_text(encoding="utf-8")
        self.assertIn("engineering-merge-grant-${TASK_FP}", source)
        self.assertIn("github_engineering_solo_governance_preauth.py", source)
        self.assertIn("preauthorized_merge_grant_run_id=${AUTHORIZE_RUN_ID}", source)
        self.assertIn("preauthorized_merge_grant_run_attempt=${AUTHORIZE_RUN_ATTEMPT}", source)

    def test_dispatch_reservation_is_bound_to_stage3_identity_not_wakeup_run(self) -> None:
        source = (ROOT / ".github/workflows/engineering-solo-governance-wakeup.yml").read_text(encoding="utf-8")
        self.assertIn('context="engineering-solo-g6/${STAGE3_RUN_ID}/${STAGE3_RUN_ATTEMPT}/${TASK_FP:0:12}"', source)
        self.assertIn("reservation_state=UNCERTAIN", source)
        self.assertIn("reservation_state=DISPATCHED", source)

    def test_multi_user_governance_still_owns_independent_review(self) -> None:
        source = (ROOT / ".github/workflows/governed-ci-repair-governance.yml").read_text(encoding="utf-8")
        self.assertIn("environment:\n      name: governed-repair-governance", source)
        self.assertIn("required_reviewers", source)
        self.assertIn("prevent_self_review", source)
        self.assertNotIn("preauthorized_merge_grant_run_id", source)

    def test_wakeup_has_no_merge_or_deploy_authority(self) -> None:
        source = (ROOT / ".github/workflows/engineering-solo-governance-wakeup.yml").read_text(encoding="utf-8")
        self.assertNotIn("gh pr merge", source)
        self.assertNotIn("/pulls/", source)
        self.assertNotIn("deploy", source.lower())


if __name__ == "__main__":
    unittest.main()
