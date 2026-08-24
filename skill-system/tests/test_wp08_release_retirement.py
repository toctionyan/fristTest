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


STATE = _load("wp08_release_retirement_state_test", "scripts/wp08_release_state.py")
RECOVERY = _load("wp08_release_retirement_recovery_test", "scripts/wp08_release_recovery.py")
GITHUB = _load("wp08_release_retirement_github_test", "scripts/wp08_release_github.py")


def _state(*, status: str, attempt: int = 8) -> dict:
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
        "last_wp08_run_id": 42,
        "last_wp08_conclusion": "failure",
        "failure_signature": "wp08:failure",
    })
    return STATE.validate_state(row)


class FakeAPI:
    repository = "toctionyan/fristTest"

    def __init__(self, state: dict, *, conclusion: str = "failure") -> None:
        self.issue = {"number": 219, "body": STATE.render_issue_body(state)}
        self.closed = False
        self.dispatched: list[str] = []
        self.run = {
            "id": 42,
            "name": "wp08-full-stack-certification",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": "b" * 40,
            "status": "completed",
            "conclusion": conclusion,
            "run_attempt": 1,
        }

    def list_issues(self, *, state: str = "open"):
        if state == "open":
            return [] if self.closed else [self.issue]
        if state == "closed":
            return [self.issue] if self.closed else []
        return []

    def update_issue(self, issue_number: int, *, body: str, close: bool = False):
        if issue_number != 219:
            raise AssertionError(issue_number)
        self.issue["body"] = body
        if close:
            self.closed = True
        return self.issue

    def get_workflow_run(self, run_id: int):
        if run_id != 42:
            raise AssertionError(run_id)
        return dict(self.run)

    def list_workflow_runs(self, workflow_file: str, *, branch: str = "main", event: str | None = None):
        return []

    def dispatch_wp08(self, **kwargs):
        self.dispatched.append(str(kwargs))
        raise AssertionError("retirement must never dispatch WP-08")


def _comment(body: str, *, actor: str = "toctionyan", issue_number: int = 219) -> dict:
    return {
        "issue": {"number": issue_number},
        "comment": {"body": body, "user": {"login": actor}},
    }


class SearchAPI(GITHUB.GitHubAPI):
    def __init__(self, payload: dict) -> None:
        super().__init__("toctionyan/fristTest", "test-token")
        self.payload = payload
        self.paths: list[str] = []

    def _request(self, method: str, path: str, **kwargs):
        self.paths.append(path)
        if method != "GET" or not path.startswith("/search/issues?"):
            raise AssertionError((method, path, kwargs))
        return 200, self.payload


class WP08ReleaseRetirementTests(unittest.TestCase):
    def test_release_discovery_uses_exact_scoped_search_for_old_ledger(self) -> None:
        state = _state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION)
        api = SearchAPI({
            "total_count": 1,
            "incomplete_results": False,
            "items": [{
                "number": 696,
                "title": "[WP08 Release] wp08-release-123456",
                "body": STATE.render_issue_body(state),
            }],
        })
        found = GITHUB.find_release_issue(api)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(696, found[0])
        self.assertEqual(state, found[1])
        self.assertEqual(1, len(api.paths))
        self.assertIn("%5BWP08+Release%5D", api.paths[0])
        self.assertIn("is%3Aissue", api.paths[0])
        self.assertIn("is%3Aopen", api.paths[0])
        self.assertNotIn("/repos/toctionyan/fristTest/issues", api.paths[0])

    def test_release_discovery_rejects_incomplete_search(self) -> None:
        api = SearchAPI({
            "total_count": 1,
            "incomplete_results": True,
            "items": [],
        })
        with self.assertRaisesRegex(GITHUB.GitHubCoordinatorError, "results are incomplete"):
            GITHUB.find_release_issue(api)

    def test_release_discovery_rejects_over_limit_search(self) -> None:
        api = SearchAPI({
            "total_count": 1001,
            "incomplete_results": False,
            "items": [],
        })
        with self.assertRaisesRegex(GITHUB.GitHubCoordinatorError, "bounded result limit"):
            GITHUB.find_release_issue(api)

    def test_release_discovery_rejects_out_of_scope_title(self) -> None:
        state = _state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION)
        api = SearchAPI({
            "total_count": 1,
            "incomplete_results": False,
            "items": [{
                "number": 854,
                "title": "[WP08 Repair] unrelated repair issue",
                "body": STATE.render_issue_body(state),
            }],
        })
        with self.assertRaisesRegex(GITHUB.GitHubCoordinatorError, "out-of-scope candidates"):
            GITHUB.find_release_issue(api)

    def test_exhausted_failed_run_can_be_retired_and_issue_closed(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION))
        RECOVERY.handle_issue_comment(
            api,
            _comment("/wp08 retire attempt_budget_exhausted run=42"),
        )
        current = STATE.parse_issue_state(api.issue["body"])
        assert current is not None
        self.assertEqual(STATE.STATUS_ABORTED, current["status"])
        self.assertEqual(8, current["attempt"])
        self.assertFalse(current["production_closed"])
        self.assertEqual("wp08:failure", current["failure_signature"])
        self.assertEqual("release_run_retired_attempt_budget_exhausted", current["history"][-1]["event"])
        self.assertEqual(42, current["history"][-1]["wp08_run_id"])
        self.assertEqual("toctionyan", current["history"][-1]["actor"])
        self.assertTrue(api.closed)
        self.assertEqual([], api.dispatched)

    def test_attempt_budget_must_be_fully_exhausted(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION, attempt=7))
        with self.assertRaisesRegex(RECOVERY.RecoveryError, "attempt budget is not exhausted"):
            RECOVERY.handle_issue_comment(
                api,
                _comment("/wp08 retire attempt_budget_exhausted run=42"),
            )
        self.assertFalse(api.closed)

    def test_retirement_requires_exact_active_run_id(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION))
        with self.assertRaisesRegex(RECOVERY.RecoveryError, "does not match active WP-08 run"):
            RECOVERY.handle_issue_comment(
                api,
                _comment("/wp08 retire attempt_budget_exhausted run=99"),
            )
        self.assertFalse(api.closed)

    def test_retirement_rejects_nonterminal_control_state(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_CERTIFYING))
        with self.assertRaisesRegex(RECOVERY.RecoveryError, "not eligible for exhausted-budget retirement"):
            RECOVERY.handle_issue_comment(
                api,
                _comment("/wp08 retire attempt_budget_exhausted run=42"),
            )
        self.assertFalse(api.closed)

    def test_retirement_rejects_unauthorized_commenter(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION))
        with self.assertRaisesRegex(RECOVERY.RecoveryError, "not authorized"):
            RECOVERY.handle_issue_comment(
                api,
                _comment("/wp08 retire attempt_budget_exhausted run=42", actor="someone-else"),
            )
        self.assertFalse(api.closed)

    def test_attempt_budget_exhausted_cancelled_run_can_be_retired(self) -> None:
        api = FakeAPI(
            _state(status=STATE.STATUS_ATTEMPT_BUDGET_EXHAUSTED),
            conclusion="cancelled",
        )
        RECOVERY.handle_issue_comment(
            api,
            _comment("/wp08 retire attempt_budget_exhausted run=42"),
        )
        current = STATE.parse_issue_state(api.issue["body"])
        assert current is not None
        self.assertEqual(STATE.STATUS_ABORTED, current["status"])
        self.assertTrue(api.closed)
        self.assertEqual("cancelled", current["history"][-1]["conclusion"])

    def test_retirement_rejects_non_failure_like_run(self) -> None:
        api = FakeAPI(_state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION), conclusion="success")
        with self.assertRaisesRegex(RECOVERY.RecoveryError, "terminal failure-like"):
            RECOVERY.handle_issue_comment(
                api,
                _comment("/wp08 retire attempt_budget_exhausted run=42"),
            )
        self.assertFalse(api.closed)


if __name__ == "__main__":
    unittest.main()
