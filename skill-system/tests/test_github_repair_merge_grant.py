from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import github_repair_merge_grant as merge_grant  # noqa: E402


class MergeGrantTests(unittest.TestCase):
    HEAD = "9" * 40
    BASE = "8" * 40
    HISTORICAL_PR_BASE = "7" * 40
    PR_URL = "https://github.com/owner/repo/pull/99"

    def _exact_head(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema": "governed-repair-exact-head@1",
            "status": "READY_FOR_REVIEW",
            "governed_repair_state": "READY_FOR_REVIEW",
            "repository": "owner/repo",
            "source_run_id": "42",
            "draft_pr_url": self.PR_URL,
            "pull_request_number": 99,
            "repair_branch": "governed-repair/quality-42",
            "repair_base_branch": "main",
            "published_source_sha": "a" * 40,
            "baseline_commit_sha": self.HEAD,
            "rca_sha256": "b" * 64,
            "write_grant_sha256": "c" * 64,
            "governance_sha256": "d" * 64,
            "baseline_acceptance_sha256": "e" * 64,
            "exact_head_ci_sha256": "f" * 64,
            "gates": {
                "G0_SCOPE_AUTHORITY": {"status": "PASS"},
                "G1_CONTRACT_PROJECTION": {"status": "PASS"},
                "G2_SEMANTIC_INVARIANT": {"status": "PASS"},
                "G3_MUTATION": {"status": "PASS"},
                "G4_FINAL_AUTHORITY": {"status": "PASS"},
                "G5_INTEGRATION_CERTIFICATION": {"status": "PASS"},
                "G6_GOVERNANCE_EXACT_HEAD": {"status": "PASS"},
            },
            "governance_closed": True,
            "baseline_accepted": True,
            "exact_head_certified": True,
            "ready_for_review": True,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
        result["exact_head_receipt_sha256"] = merge_grant._fingerprint(result)
        return result

    def _pr_state(self) -> dict[str, object]:
        return {
            "number": 99,
            "url": self.PR_URL,
            "state": "OPEN",
            "is_draft": False,
            "head_sha": self.HEAD,
            "base_branch": "main",
            # GitHub PR metadata may retain an older base SHA. It must not be
            # authoritative for a new MergeGrant.
            "base_sha": self.HISTORICAL_PR_BASE,
        }

    def _base_state(self) -> dict[str, object]:
        return {"branch": "main", "sha": self.BASE}

    def _issue(self, **overrides: object) -> dict[str, object]:
        args: dict[str, object] = {
            "exact_head": self._exact_head(),
            "pr_state": self._pr_state(),
            "base_state": self._base_state(),
            "actor": "owner",
            "repository_owner": "owner",
            "approval_ref": "github-actions:123/1:owner-merge",
        }
        args.update(overrides)
        return merge_grant.issue_merge_grant(**args)  # type: ignore[arg-type]

    def test_owner_can_issue_exact_head_live_base_bound_merge_grant(self) -> None:
        grant = self._issue()
        self.assertEqual(grant["status"], "MERGE_GRANT_ISSUED")
        self.assertEqual(grant["head_sha"], self.HEAD)
        self.assertEqual(grant["base_sha"], self.BASE)
        self.assertEqual(grant["base_sha_authority"], "live_branch_tip")
        self.assertNotEqual(grant["base_sha"], self.HISTORICAL_PR_BASE)
        self.assertTrue(grant["merge_allowed"])
        self.assertFalse(grant["grant_consumed"])
        self.assertFalse(grant["deploy_allowed"])
        self.assertFalse(grant["production_closed"])

    def test_non_owner_cannot_issue_merge_grant(self) -> None:
        with self.assertRaisesRegex(merge_grant.MergeGrantError, "repository owner"):
            self._issue(actor="reviewer")

    def test_stale_head_invalidates_merge_grant(self) -> None:
        pr = self._pr_state()
        pr["head_sha"] = "6" * 40
        with self.assertRaisesRegex(merge_grant.MergeGrantError, "head drifted"):
            self._issue(pr_state=pr)

    def test_draft_pr_cannot_receive_merge_grant(self) -> None:
        pr = self._pr_state()
        pr["is_draft"] = True
        with self.assertRaisesRegex(merge_grant.MergeGrantError, "Ready for review"):
            self._issue(pr_state=pr)

    def test_tampered_exact_head_receipt_is_rejected(self) -> None:
        exact = self._exact_head()
        exact["ready_for_review"] = False
        with self.assertRaises(merge_grant.MergeGrantError):
            self._issue(exact_head=exact)

    def test_live_base_branch_must_match_certified_base_branch(self) -> None:
        with self.assertRaisesRegex(merge_grant.MergeGrantError, "live base branch"):
            self._issue(base_state={"branch": "release", "sha": self.BASE})

    def test_missing_or_invalid_live_base_sha_fails_closed(self) -> None:
        for sha in ("", "not-a-sha", "a" * 39):
            with self.subTest(sha=sha):
                with self.assertRaisesRegex(merge_grant.MergeGrantError, "live base SHA"):
                    self._issue(base_state={"branch": "main", "sha": sha})

    def test_grant_never_carries_deploy_or_production_authority(self) -> None:
        grant = self._issue()
        self.assertTrue(grant["merge_allowed"])
        self.assertFalse(grant["deploy_allowed"])
        self.assertFalse(grant["production_closed"])

    def test_workflow_uses_live_branch_tip_and_rechecks_it_before_merge(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "governed-ci-repair-merge.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("git/ref/heads/${BASE_BRANCH}", workflow)
        self.assertIn("--base-state merge-evidence/base-state.json", workflow)
        self.assertIn("base_sha_authority == \"live_branch_tip\"", workflow)
        self.assertIn("live base drifted after MergeGrant issuance", workflow)
        self.assertIn("head_sha=${HEAD_SHA}&event=pull_request", workflow)
        self.assertIn(".name == \"quality\"", workflow)
        self.assertIn(".name == \"skill-self-validation\"", workflow)
        self.assertNotIn("base_sha:.base.sha", workflow)


if __name__ == "__main__":
    unittest.main()
