from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from engineering_merge_consumption import classify_consumption, consumption_context  # noqa: E402
from engineering_merge_grant import create_merge_grant  # noqa: E402

REPO = "toctionyan/fristTest"
BASE = "a" * 40


def task() -> dict:
    return {
        "task_id": "single-use-task",
        "binding": {
            "base_sha": BASE,
            "branch": "repair/single-use-task",
            "allowed_paths": ["services/agent-service/app/runtime.py"],
            "target_fingerprint": "single-use-target-v1",
        },
    }


def grant() -> dict:
    return create_merge_grant(
        task=task(),
        repository=REPO,
        source_pr_number=2001,
        issued_by="toctionyan",
        owner_authorization_ref="engineering-autonomy-authorize:123/1",
    )


def status_row(context: str, state: str, *, row_id: int, updated: str) -> dict:
    return {
        "id": row_id,
        "context": context,
        "state": state,
        "created_at": updated,
        "updated_at": updated,
    }


class EngineeringMergeConsumptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.grant = grant()
        self.context = consumption_context(self.grant)

    def test_context_uses_full_grant_digest_and_stable_base_anchor(self) -> None:
        self.assertEqual(self.context, "engineering-merge-consume/" + self.grant["grant_sha256"])
        self.assertLessEqual(len(self.context), 100)
        result = classify_consumption(self.grant, combined_status={"statuses": []})
        self.assertEqual(result["anchor_sha"], BASE)
        self.assertEqual(result["status"], "RESERVABLE")
        self.assertTrue(result["reservation_allowed"])

    def test_pending_reservation_is_uncertain_and_fail_closed(self) -> None:
        result = classify_consumption(
            self.grant,
            combined_status={"statuses": [status_row(self.context, "pending", row_id=1, updated="2026-08-19T00:00:00Z")]},
        )
        self.assertEqual(result["status"], "UNCERTAIN")
        self.assertFalse(result["reservation_allowed"])
        self.assertFalse(result["merge_allowed"])

    def test_success_means_grant_was_consumed(self) -> None:
        result = classify_consumption(
            self.grant,
            combined_status={"statuses": [status_row(self.context, "success", row_id=2, updated="2026-08-19T00:01:00Z")]},
        )
        self.assertEqual(result["status"], "CONSUMED")
        self.assertFalse(result["reservation_allowed"])

    def test_failed_consumption_requires_new_authority(self) -> None:
        for state in ("failure", "error"):
            with self.subTest(state=state):
                result = classify_consumption(
                    self.grant,
                    combined_status={"statuses": [status_row(self.context, state, row_id=3, updated="2026-08-19T00:02:00Z")]},
                )
                self.assertEqual(result["status"], "FAILED")
                self.assertFalse(result["reservation_allowed"])

    def test_latest_matching_status_wins_and_unrelated_status_is_ignored(self) -> None:
        rows = [
            status_row(self.context, "failure", row_id=1, updated="2026-08-19T00:00:00Z"),
            status_row("quality-quick", "failure", row_id=99, updated="2026-08-19T00:03:00Z"),
            status_row(self.context, "success", row_id=2, updated="2026-08-19T00:02:00Z"),
        ]
        result = classify_consumption(self.grant, combined_status={"statuses": rows})
        self.assertEqual(result["status"], "CONSUMED")
        self.assertEqual(result["observed_status_id"], 2)

    def test_unknown_matching_state_blocks(self) -> None:
        result = classify_consumption(
            self.grant,
            combined_status={"statuses": [status_row(self.context, "mystery", row_id=4, updated="2026-08-19T00:03:00Z")]},
        )
        self.assertEqual(result["status"], "BLOCKED_UNKNOWN")
        self.assertFalse(result["reservation_allowed"])


if __name__ == "__main__":
    unittest.main()
