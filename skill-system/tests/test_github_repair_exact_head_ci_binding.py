from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from github_repair_exact_head import (  # noqa: E402
    ExactHeadError,
    validate_exact_head_ci_evidence,
)


class ExactHeadCiBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sha = "a" * 40
        self.pr_number = 1469
        self.pr_url = f"https://github.com/toctionyan/fristTest/pull/{self.pr_number}"
        self.ci = {
            "schema": "governed-repair-exact-head-ci@1",
            "head_sha": self.sha,
            "pr_url": self.pr_url,
            "pr_number": self.pr_number,
            "pr_is_draft": True,
            "pr_head_sha": self.sha,
            "workflows": {
                "quality": {
                    "run_id": "1001",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": self.sha,
                    "event": "pull_request",
                    "pr_number": self.pr_number,
                },
                "skill-self-validation": {
                    "run_id": "1002",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": self.sha,
                    "event": "pull_request",
                    "pr_number": self.pr_number,
                },
            },
        }

    def _validate(self, payload: dict[str, object]) -> None:
        validate_exact_head_ci_evidence(
            payload,
            exact_sha=self.sha,
            draft_pr_url=self.pr_url,
        )

    def test_exact_pr_pull_request_evidence_passes(self) -> None:
        number, workflows = validate_exact_head_ci_evidence(
            self.ci,
            exact_sha=self.sha,
            draft_pr_url=self.pr_url,
        )
        self.assertEqual(number, self.pr_number)
        self.assertEqual(set(workflows), {"quality", "skill-self-validation"})

    def test_same_sha_push_run_cannot_satisfy_g6(self) -> None:
        payload = copy.deepcopy(self.ci)
        payload["workflows"]["quality"]["event"] = "push"
        with self.assertRaisesRegex(ExactHeadError, "not a pull_request run"):
            self._validate(payload)

    def test_same_sha_workflow_dispatch_run_cannot_satisfy_g6(self) -> None:
        payload = copy.deepcopy(self.ci)
        payload["workflows"]["skill-self-validation"]["event"] = "workflow_dispatch"
        with self.assertRaisesRegex(ExactHeadError, "not a pull_request run"):
            self._validate(payload)

    def test_other_pr_same_sha_cannot_satisfy_g6(self) -> None:
        payload = copy.deepcopy(self.ci)
        payload["workflows"]["quality"]["pr_number"] = self.pr_number + 1
        with self.assertRaisesRegex(ExactHeadError, "wrong pull request"):
            self._validate(payload)

    def test_top_level_pr_binding_cannot_be_relabelled(self) -> None:
        payload = copy.deepcopy(self.ci)
        payload["pr_number"] = self.pr_number + 1
        with self.assertRaisesRegex(ExactHeadError, "pull request number mismatch"):
            self._validate(payload)

    def test_stale_pr_head_cannot_satisfy_g6(self) -> None:
        payload = copy.deepcopy(self.ci)
        payload["pr_head_sha"] = "b" * 40
        with self.assertRaisesRegex(ExactHeadError, "Draft PR head"):
            self._validate(payload)

    def test_mandatory_workflow_wrong_sha_cannot_satisfy_g6(self) -> None:
        payload = copy.deepcopy(self.ci)
        payload["workflows"]["quality"]["head_sha"] = "b" * 40
        with self.assertRaisesRegex(ExactHeadError, "wrong SHA"):
            self._validate(payload)

    def test_failed_mandatory_workflow_cannot_satisfy_g6(self) -> None:
        payload = copy.deepcopy(self.ci)
        payload["workflows"]["quality"]["conclusion"] = "failure"
        with self.assertRaisesRegex(ExactHeadError, "did not succeed"):
            self._validate(payload)

    def test_missing_mandatory_workflow_cannot_satisfy_g6(self) -> None:
        payload = copy.deepcopy(self.ci)
        del payload["workflows"]["quality"]
        with self.assertRaisesRegex(ExactHeadError, "workflow evidence is missing"):
            self._validate(payload)


if __name__ == "__main__":
    unittest.main()
