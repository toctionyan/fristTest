from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPTS = ROOT / "scripts"
for path in (CONTROL, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from engineering_merge_grant import create_merge_grant  # noqa: E402
from github_engineering_solo_governance_preauth import (  # noqa: E402
    SoloGovernancePreauthorizationError,
    verify_preauthorization,
)

REPO = "toctionyan/fristTest"
OWNER = "toctionyan"
RUN_ID = 12345
ATTEMPT = 1


def task() -> dict:
    return {
        "task_id": "preauth-task",
        "binding": {
            "base_sha": "a" * 40,
            "branch": "repair/preauth-task",
            "allowed_paths": ["services/agent-service/app/runtime.py"],
            "target_fingerprint": "target-preauth-v1",
        },
    }


def run(*, actor: str = OWNER, event: str = "workflow_dispatch", conclusion: str = "success") -> dict:
    return {
        "id": RUN_ID,
        "run_attempt": ATTEMPT,
        "name": "engineering-autonomy-authorize",
        "event": event,
        "status": "completed",
        "conclusion": conclusion,
        "actor": {"login": actor},
    }


class SoloGovernancePreauthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = task()
        self.grant = create_merge_grant(
            task=self.task,
            repository=REPO,
            source_pr_number=1900,
            issued_by=OWNER,
            owner_authorization_ref=f"engineering-autonomy-authorize:{RUN_ID}/{ATTEMPT}",
        )

    def verify(self, **overrides):
        values = {
            "task": self.task,
            "grant": self.grant,
            "authorize_run": run(),
            "repository": REPO,
            "repository_owner": OWNER,
            "expected_run_id": RUN_ID,
            "expected_run_attempt": ATTEMPT,
        }
        values.update(overrides)
        return verify_preauthorization(**values)

    def test_owner_dispatched_same_task_grant_can_pre_authorize_solo_g6(self) -> None:
        result = self.verify()
        self.assertEqual(result["status"], "AUTHORIZED")
        self.assertTrue(result["governance_close_allowed"])
        self.assertFalse(result["independent_human_review"])
        self.assertFalse(result["merge_allowed"])
        self.assertFalse(result["deploy_allowed"])

    def test_different_task_cannot_reuse_preauthorization(self) -> None:
        other = copy.deepcopy(self.task)
        other["task_id"] = "other-task"
        with self.assertRaises(SoloGovernancePreauthorizationError):
            self.verify(task=other)

    def test_non_owner_authorize_actor_is_rejected(self) -> None:
        with self.assertRaisesRegex(SoloGovernancePreauthorizationError, "actor"):
            self.verify(authorize_run=run(actor="github-actions[bot]"))

    def test_non_dispatch_or_failed_authorization_is_rejected(self) -> None:
        with self.assertRaises(SoloGovernancePreauthorizationError):
            self.verify(authorize_run=run(event="push"))
        with self.assertRaises(SoloGovernancePreauthorizationError):
            self.verify(authorize_run=run(conclusion="failure"))

    def test_run_attempt_substitution_is_rejected(self) -> None:
        with self.assertRaisesRegex(SoloGovernancePreauthorizationError, "identity"):
            self.verify(expected_run_attempt=2)

    def test_grant_authorization_reference_substitution_is_rejected(self) -> None:
        forged = copy.deepcopy(self.grant)
        forged["owner_authorization_ref"] = "engineering-autonomy-authorize:999/1"
        with self.assertRaises(SoloGovernancePreauthorizationError):
            self.verify(grant=forged)


if __name__ == "__main__":
    unittest.main()
