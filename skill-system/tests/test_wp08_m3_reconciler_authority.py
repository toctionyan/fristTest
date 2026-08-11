from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STATE = _load("wp08_m3_state_test", "scripts/wp08_release_state.py")
COORD = _load("wp08_m3_coordinator_test", "scripts/wp08_release_coordinator.py")
RECOVERY = _load("wp08_m3_recovery_test", "scripts/wp08_release_recovery.py")
CONTRACT = _load("wp08_m3_contract_test", "scripts/wp08_release_coordinator_contract.py")


def _state(*, status: str, attempt: int = 2) -> dict:
    row = STATE.new_state(
        release_run_id="wp08-release-123456",
        authorized_initial_sha="a" * 40,
        current_candidate_sha="b" * 40,
        max_attempts=8,
        actor="toctionyan",
    )
    row.update({
        "status": status,
        "attempt": attempt,
        "current_wp08_run_id": 42,
    })
    return STATE.validate_state(row)


class FakeAPI:
    repository = "toctionyan/fristTest"

    def __init__(self, state: dict) -> None:
        self.issue = {"number": 219, "body": STATE.render_issue_body(state)}
        self.updated: list[dict] = []
        self.dispatched: list[str] = []
        self.run = {
            "id": 42,
            "name": "wp08-full-stack-certification",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": "b" * 40,
            "status": "completed",
            "conclusion": "failure",
        }
        self.quality_runs: list[dict] = []

    def list_issues(self, *, state: str = "open"):
        return [self.issue] if state == "open" else []

    def update_issue(self, issue_number: int, *, body: str, close: bool = False):
        self.assert_issue(issue_number)
        self.issue["body"] = body
        parsed = STATE.parse_issue_state(body)
        assert parsed is not None
        self.updated.append(parsed)
        return self.issue

    def assert_issue(self, issue_number: int) -> None:
        assert issue_number == 219

    def get_workflow_run(self, run_id: int):
        assert run_id == 42
        return dict(self.run)

    def list_workflow_runs(self, workflow_file: str, *, branch: str = "main", event: str | None = None):
        return [dict(row) for row in self.quality_runs]

    def dispatch_wp08(self, *, candidate_sha: str) -> int:
        self.dispatched.append(candidate_sha)
        return 9000 + len(self.dispatched)


class M3ReconcilerAuthorityTests(unittest.TestCase):
    def test_recovery_reconcile_replays_completed_wp08_without_direct_mutation(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_CERTIFYING))
        calls: list[tuple[dict, str]] = []
        original = RECOVERY.consume_workflow_run
        try:
            RECOVERY.consume_workflow_run = lambda _api, event, *, source="event": calls.append((event, source))
            RECOVERY.reconcile(api)
        finally:
            RECOVERY.consume_workflow_run = original
        self.assertEqual([], api.updated)
        self.assertEqual(1, len(calls))
        self.assertEqual(42, calls[0][0]["workflow_run"]["id"])
        self.assertEqual("reconcile", calls[0][1])

    def test_quality_reconcile_replays_latest_exact_completed_run_only(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_WAITING_REPAIR_CI))
        api.quality_runs = [
            {"id": 50, "name": "quality", "event": "push", "head_branch": "main", "head_sha": "b" * 40, "status": "completed", "conclusion": "success"},
            {"id": 51, "name": "quality", "event": "push", "head_branch": "main", "head_sha": "c" * 40, "status": "completed", "conclusion": "failure"},
            {"id": 52, "name": "quality", "event": "push", "head_branch": "main", "head_sha": "b" * 40, "status": "in_progress", "conclusion": None},
            {"id": 53, "name": "quality", "event": "push", "head_branch": "main", "head_sha": "b" * 40, "status": "completed", "conclusion": "failure"},
        ]
        calls: list[tuple[dict, str]] = []
        original = RECOVERY.consume_workflow_run
        try:
            RECOVERY.consume_workflow_run = lambda _api, event, *, source="event": calls.append((event, source))
            RECOVERY.reconcile(api)
        finally:
            RECOVERY.consume_workflow_run = original
        self.assertEqual([53], [row[0]["workflow_run"]["id"] for row in calls])
        self.assertEqual("reconcile", calls[0][1])

    def test_coordinator_owns_reconciled_failure_transition_and_history(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_CERTIFYING))
        COORD.handle_workflow_run(api, {"workflow_run": dict(api.run)}, source="reconcile")
        current = STATE.parse_issue_state(api.issue["body"])
        assert current is not None
        self.assertEqual(STATE.STATUS_FAILED_NEEDS_CLASSIFICATION, current["status"])
        self.assertEqual("failure", current["last_wp08_conclusion"])
        self.assertEqual("wp08_reconciled_completed_failure", current["history"][-1]["event"])
        self.assertEqual([], api.dispatched)

    def test_reconciled_retry_still_cannot_cross_attempt_budget(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_CERTIFYING, attempt=8))
        run = dict(api.run)
        run["conclusion"] = "cancelled"
        COORD.handle_workflow_run(api, {"workflow_run": run}, source="reconcile")
        current = STATE.parse_issue_state(api.issue["body"])
        assert current is not None
        self.assertEqual(STATE.STATUS_ATTEMPT_BUDGET_EXHAUSTED, current["status"])
        self.assertEqual(8, current["attempt"])
        self.assertEqual([], api.dispatched)

    def test_reconcile_source_rejects_unknown_authority(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_CERTIFYING))
        with self.assertRaisesRegex(COORD.CoordinatorError, "source must be event or reconcile"):
            COORD.handle_workflow_run(api, {"workflow_run": dict(api.run)}, source="model_guess")

    def test_permanent_workflow_has_manual_reconcile_and_staggered_fallback(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "wp08-release-coordinator.yml").read_text(encoding="utf-8")
        self.assertIn("operation:", workflow)
        self.assertIn("default: authorize", workflow)
        self.assertIn("- reconcile", workflow)
        self.assertIn("cron: '2-59/5 * * * *'", workflow)
        self.assertNotIn("cron: '*/5 * * * *'", workflow)
        self.assertIn("WP08_COORDINATOR_OPERATION", workflow)
        self.assertIn("scripts/wp08_release_recovery.py --mode reconcile", workflow)
        self.assertNotIn("environment: production-certification", workflow)
        self.assertNotIn("secrets.", workflow)

    def test_reconcile_function_has_no_direct_state_transition_authority(self) -> None:
        source = (ROOT / "scripts" / "wp08_release_recovery.py").read_text(encoding="utf-8")
        segment = source[source.index("def reconcile("):source.index("def _authorized_commenter(")]
        self.assertIn("_replay_workflow_run", segment)
        self.assertNotIn("persist_release_state", segment)
        self.assertNotIn("_dispatch(", segment)
        self.assertNotIn("def _complete_wp08", source)
        self.assertNotIn("def _reconcile_quality", source)

    def test_static_contract_declares_manual_reconcile_authority(self) -> None:
        result = CONTRACT.validate_static(ROOT)
        self.assertEqual("PASS", result["status"])
        self.assertIs(True, result["manual_reconcile"])
        self.assertEqual("coordinator-event-replay", result["reconciliation_authority"])
        self.assertIs(False, result["production_closed"])


if __name__ == "__main__":
    unittest.main()
