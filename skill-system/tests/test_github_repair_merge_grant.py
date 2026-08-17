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
            "base_sha": self.BASE,
        }

    def test_owner_can_issue_exact_head_base_bound_merge_grant(self) -> None:
        grant = merge_grant.issue_merge_grant(
            self._exact_head(),
            self._pr_state(),
            actor="owner",
            repository_owner="owner",
            approval_ref="github-actions:123/1:owner-merge",
        )
        self.assertEqual(grant["status"], "MERGE_GRANT_ISSUED")
        self.assertEqual(grant["head_sha"], self.HEAD)
        self.assertEqual(grant["base_sha"], self.BASE)
        self.assertTrue(grant["merge_allowed"])
        self.assertFalse(grant["grant_consumed"])
        self.assertFalse(grant["deploy_allowed"])
        self.assertFalse(grant["production_closed"])

    def test_non_owner_cannot_issue_merge_grant(self) -> None:
        with self.assertRaisesRegex(
            merge_grant.MergeGrantError,
            "repository owner",
        ):
            merge_grant.issue_merge_grant(
                self._exact_head(),
                self._pr_state(),
                actor="reviewer",
                repository_owner="owner",
                approval_ref="approval:1",
            )

    def test_stale_head_invalidates_merge_grant(self) -> None:
        pr = self._pr_state()
        pr["head_sha"] = "7" * 40
        with self.assertRaisesRegex(merge_grant.MergeGrantError, "head drifted"):
            merge_grant.issue_merge_grant(
                self._exact_head(),
                pr,
                actor="owner",
                repository_owner="owner",
                approval_ref="approval:1",
            )

    def test_draft_pr_cannot_receive_merge_grant(self) -> None:
        pr = self._pr_state()
        pr["is_draft"] = True
        with self.assertRaisesRegex(merge_grant.MergeGrantError, "Ready for review"):
            merge_grant.issue_merge_grant(
                self._exact_head(),
                pr,
                actor="owner",
                repository_owner="owner",
                approval_ref="approval:1",
            )

    def test_tampered_exact_head_receipt_is_rejected(self) -> None:
        exact = self._exact_head()
        exact["ready_for_review"] = False
        with self.assertRaises(merge_grant.MergeGrantError):
            merge_grant.issue_merge_grant(
                exact,
                self._pr_state(),
                actor="owner",
                repository_owner="owner",
                approval_ref="approval:1",
            )

    def test_grant_never_carries_deploy_or_production_authority(self) -> None:
        grant = merge_grant.issue_merge_grant(
            self._exact_head(),
            self._pr_state(),
            actor="owner",
            repository_owner="owner",
            approval_ref="approval:1",
        )
        self.assertTrue(grant["merge_allowed"])
        self.assertFalse(grant["deploy_allowed"])
        self.assertFalse(grant["production_closed"])


if __name__ == "__main__":
    unittest.main()
