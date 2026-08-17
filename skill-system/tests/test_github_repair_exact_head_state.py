from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import github_repair_exact_head_state as state  # noqa: E402


class ExactHeadStateTests(unittest.TestCase):
    SHA = "9" * 40
    PR_URL = "https://github.com/owner/repo/pull/99"

    def _row(
        self,
        run_id: str,
        *,
        status: str = "completed",
        conclusion: str = "success",
        sha: str | None = None,
        event: str = "pull_request",
        pr_number: int = 99,
    ) -> dict[str, object]:
        return {
            "run_id": run_id,
            "status": status,
            "conclusion": conclusion,
            "head_sha": sha or self.SHA,
            "event": event,
            "pr_number": pr_number,
        }

    def _ci(
        self,
        quality: dict[str, object],
        skill: dict[str, object],
    ) -> dict[str, object]:
        return {
            "schema": "governed-repair-exact-head-ci@1",
            "head_sha": self.SHA,
            "pr_url": self.PR_URL,
            "pr_number": 99,
            "pr_is_draft": True,
            "pr_head_sha": self.SHA,
            "workflows": {
                "quality": quality,
                "skill-self-validation": skill,
            },
        }

    def test_both_success_allows_only_g6_finalization(self) -> None:
        result = state.classify_exact_head_ci(
            self._ci(self._row("100"), self._row("101")),
            exact_sha=self.SHA,
            draft_pr_url=self.PR_URL,
        )
        self.assertEqual(result["status"], state.STATE_PASSED)
        self.assertTrue(result["finalize_allowed"])
        self.assertFalse(result["resume_required"])
        self.assertFalse(result["baseline_mutation_allowed"])
        self.assertFalse(result["merge_allowed"])
        self.assertFalse(result["deploy_allowed"])
        self.assertFalse(result["production_closed"])

    def test_action_required_is_resumable_not_terminal_failure(self) -> None:
        result = state.classify_exact_head_ci(
            self._ci(
                self._row("100", conclusion="action_required"),
                self._row("101", conclusion="action_required"),
            ),
            exact_sha=self.SHA,
            draft_pr_url=self.PR_URL,
        )
        self.assertEqual(result["status"], state.STATE_AWAITING_APPROVAL)
        self.assertTrue(result["resume_required"])
        self.assertFalse(result["finalize_allowed"])
        self.assertEqual(
            result["approval_required_workflows"],
            ["quality", "skill-self-validation"],
        )
        self.assertFalse(result["baseline_mutation_allowed"])

    def test_one_action_required_dominates_other_unfinished_run(self) -> None:
        result = state.classify_exact_head_ci(
            self._ci(
                self._row("100", conclusion="action_required"),
                self._row("101", status="in_progress", conclusion=""),
            ),
            exact_sha=self.SHA,
            draft_pr_url=self.PR_URL,
        )
        self.assertEqual(result["status"], state.STATE_AWAITING_APPROVAL)
        self.assertTrue(result["resume_required"])
        self.assertIn("skill-self-validation", result["unfinished_workflows"])

    def test_real_terminal_failure_is_not_reclassified_as_approval_wait(self) -> None:
        result = state.classify_exact_head_ci(
            self._ci(
                self._row("100", conclusion="failure"),
                self._row("101", conclusion="action_required"),
            ),
            exact_sha=self.SHA,
            draft_pr_url=self.PR_URL,
        )
        self.assertEqual(result["status"], state.STATE_FAILED)
        self.assertFalse(result["resume_required"])
        self.assertFalse(result["finalize_allowed"])
        self.assertEqual(result["failed_workflows"], ["quality:failure"])

    def test_same_sha_push_run_cannot_satisfy_or_wait_for_g6(self) -> None:
        with self.assertRaisesRegex(
            state.ExactHeadStateError,
            "workflow quality is not a pull_request run",
        ):
            state.classify_exact_head_ci(
                self._ci(
                    self._row("100", event="push"),
                    self._row("101"),
                ),
                exact_sha=self.SHA,
                draft_pr_url=self.PR_URL,
            )

    def test_stale_pr_head_fails_closed(self) -> None:
        ci = self._ci(self._row("100"), self._row("101"))
        ci["pr_head_sha"] = "8" * 40
        with self.assertRaisesRegex(
            state.ExactHeadStateError,
            "Draft PR head is not the accepted baseline commit",
        ):
            state.classify_exact_head_ci(
                ci,
                exact_sha=self.SHA,
                draft_pr_url=self.PR_URL,
            )


if __name__ == "__main__":
    unittest.main()
