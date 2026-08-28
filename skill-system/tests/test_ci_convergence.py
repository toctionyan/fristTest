"""Tests for deterministic exact-head CI convergence."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ci_convergence import CIConvergenceError, reduce_ci_convergence  # noqa: E402


HEAD = "a" * 40
CONTROL_PLANE = "c" * 40


def run(run_id: int, name: str, *, status: str = "completed", conclusion: str = "success", attempt: int = 1) -> dict[str, object]:
    return {
        "id": run_id,
        "run_attempt": attempt,
        "name": name,
        "path": f".github/workflows/{name}.yml",
        "event": "pull_request",
        "head_sha": HEAD,
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "pull_requests": [{"number": 2198}],
    }


class CIConvergenceTests(unittest.TestCase):
    def test_exact_success_is_pass_and_deterministic(self) -> None:
        rows = [run(101, "quality"), run(102, "skill-self-validation")]
        first = reduce_ci_convergence(
            rows,
            head_sha=HEAD,
            control_plane_ref=CONTROL_PLANE,
            pull_request_number=2198,
            trigger_run_id=101,
            trigger_run_attempt=1,
            trigger_workflow="quality",
        )
        second = reduce_ci_convergence(
            list(reversed(rows)),
            head_sha=HEAD,
            control_plane_ref=CONTROL_PLANE,
            pull_request_number=2198,
            trigger_run_id=101,
            trigger_run_attempt=1,
            trigger_workflow="quality",
        )
        self.assertEqual(first["status"], "PASS")
        self.assertEqual(first["control_plane_ref"], CONTROL_PLANE)
        self.assertEqual(
            first["trigger"],
            {"workflow": "quality", "run_id": 101, "run_attempt": 1},
        )
        self.assertEqual(first, second)

    def test_control_plane_ref_is_required_and_workflow_rows_are_closed(self) -> None:
        with self.assertRaises(CIConvergenceError):
            reduce_ci_convergence(
                [run(101, "quality"), run(102, "skill-self-validation")],
                head_sha=HEAD,
                control_plane_ref="not-a-sha",
                pull_request_number=2198,
                trigger_run_id=101,
                trigger_run_attempt=1,
                trigger_workflow="quality",
            )
        with self.assertRaises(CIConvergenceError):
            reduce_ci_convergence(
                [run(101, "quality"), "malformed-row"],
                head_sha=HEAD,
                control_plane_ref=CONTROL_PLANE,
                pull_request_number=2198,
                trigger_run_id=101,
                trigger_run_attempt=1,
                trigger_workflow="quality",
            )

    def test_missing_or_running_check_is_pending(self) -> None:
        result = reduce_ci_convergence(
            [run(101, "quality")],
            head_sha=HEAD,
            control_plane_ref=CONTROL_PLANE,
            pull_request_number=2198,
            trigger_run_id=101,
            trigger_run_attempt=1,
            trigger_workflow="quality",
        )
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["checks"]["skill-self-validation"]["reason"], "run_not_terminal_or_not_started")

    def test_failure_wins_over_pending_but_ambiguity_blocks(self) -> None:
        rows = [
            run(101, "quality", conclusion="failure"),
            run(102, "skill-self-validation", status="in_progress"),
        ]
        result = reduce_ci_convergence(
            rows,
            head_sha=HEAD,
            control_plane_ref=CONTROL_PLANE,
            pull_request_number=2198,
            trigger_run_id=101,
            trigger_run_attempt=1,
            trigger_workflow="quality",
        )
        self.assertEqual(result["status"], "FAIL")
        rows.append(run(103, "quality"))
        blocked = reduce_ci_convergence(
            rows,
            head_sha=HEAD,
            control_plane_ref=CONTROL_PLANE,
            pull_request_number=2198,
            trigger_run_id=101,
            trigger_run_attempt=1,
            trigger_workflow="quality",
        )
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("quality:multiple_distinct_runs_for_exact_head", blocked["reasons"])

    def test_trigger_identity_and_scope_are_exact(self) -> None:
        rows = [run(101, "quality"), run(102, "skill-self-validation")]
        changed = copy.deepcopy(rows)
        changed[0]["run_attempt"] = 2
        result = reduce_ci_convergence(
            changed,
            head_sha=HEAD,
            control_plane_ref=CONTROL_PLANE,
            pull_request_number=2198,
            trigger_run_id=101,
            trigger_run_attempt=1,
            trigger_workflow="quality",
        )
        self.assertEqual(result["status"], "STALE_EVENT")
        self.assertIn("quality:stale_trigger_event", result["reasons"])

    def test_stale_trigger_attempt_is_ignored_without_overwriting_status(self) -> None:
        rows = [
            run(101, "quality", attempt=2),
            run(102, "skill-self-validation"),
        ]
        result = reduce_ci_convergence(
            rows,
            head_sha=HEAD,
            control_plane_ref=CONTROL_PLANE,
            pull_request_number=2198,
            trigger_run_id=101,
            trigger_run_attempt=1,
            trigger_workflow="quality",
        )
        self.assertEqual(result["status"], "STALE_EVENT")
        self.assertEqual(result["checks"]["quality"]["reason"], "stale_trigger_event")

    def test_trigger_attempt_missing_from_eventual_consistent_list_is_pending(self) -> None:
        result = reduce_ci_convergence(
            [run(101, "quality", attempt=1), run(102, "skill-self-validation")],
            head_sha=HEAD,
            control_plane_ref=CONTROL_PLANE,
            pull_request_number=2198,
            trigger_run_id=101,
            trigger_run_attempt=2,
            trigger_workflow="quality",
        )
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(
            result["checks"]["quality"]["reason"],
            "trigger_run_attempt_not_visible",
        )

    def test_exact_rerun_attempt_ignores_historical_attempt_record(self) -> None:
        result = reduce_ci_convergence(
            [
                run(101, "quality", conclusion="failure", attempt=1),
                run(101, "quality", attempt=2),
                run(102, "skill-self-validation"),
            ],
            head_sha=HEAD,
            control_plane_ref=CONTROL_PLANE,
            pull_request_number=2198,
            trigger_run_id=101,
            trigger_run_attempt=2,
            trigger_workflow="quality",
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["checks"]["quality"]["run_attempt"], 2)

    def test_newer_attempt_blocks_an_old_successful_attempt(self) -> None:
        result = reduce_ci_convergence(
            [
                run(101, "quality", attempt=1),
                run(101, "quality", status="in_progress", attempt=2),
                run(102, "skill-self-validation"),
            ],
            head_sha=HEAD,
            control_plane_ref=CONTROL_PLANE,
            pull_request_number=2198,
            trigger_run_id=101,
            trigger_run_attempt=1,
            trigger_workflow="quality",
        )
        self.assertEqual(result["status"], "STALE_EVENT")
        self.assertEqual(result["checks"]["quality"]["current_run_attempt"], 2)

    def test_wrong_workflow_path_is_not_evidence(self) -> None:
        row = run(101, "quality")
        row["path"] = ".github/workflows/untrusted-quality.yml"
        result = reduce_ci_convergence(
            [row, run(102, "skill-self-validation")],
            head_sha=HEAD,
            control_plane_ref=CONTROL_PLANE,
            pull_request_number=2198,
            trigger_run_id=101,
            trigger_run_attempt=1,
            trigger_workflow="quality",
        )
        self.assertEqual(result["status"], "PENDING")

    def test_wrong_head_and_wrong_pull_request_are_not_evidence(self) -> None:
        wrong_head = run(101, "quality")
        wrong_head["head_sha"] = "b" * 40
        wrong_pr = run(102, "skill-self-validation")
        wrong_pr["pull_requests"] = [{"number": 2199}]
        result = reduce_ci_convergence(
            [wrong_head, wrong_pr],
            head_sha=HEAD,
            control_plane_ref=CONTROL_PLANE,
            pull_request_number=2198,
            trigger_run_id=101,
            trigger_run_attempt=1,
            trigger_workflow="quality",
        )
        self.assertEqual(result["status"], "PENDING")
        self.assertIn(
            "quality:trigger_run_attempt_not_visible",
            result["reasons"],
        )
        self.assertIn(
            "skill-self-validation:run_not_terminal_or_not_started",
            result["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
