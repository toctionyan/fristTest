from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

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


STATE = _load("wp08_release_state_recovery_test", "scripts/wp08_release_state.py")
RECOVERY = _load("wp08_release_recovery_test", "scripts/wp08_release_recovery.py")


def _state(*, status: str = STATE.STATUS_CERTIFYING, attempt: int = 2) -> dict:
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
        self.run = {
            "id": 42,
            "name": "wp08-full-stack-certification",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": "b" * 40,
            "status": "completed",
            "conclusion": "failure",
        }
        self.dispatched: list[str] = []

    def list_issues(self, *, state: str = "open"):
        return [self.issue] if state == "open" else []

    def update_issue(self, issue_number: int, *, body: str, close: bool = False):
        assert issue_number == 219
        self.issue["body"] = body
        return self.issue

    def get_workflow_run(self, run_id: int):
        assert run_id == 42
        return dict(self.run)

    def list_workflow_runs(self, workflow_file: str, *, branch: str = "main", event: str | None = None):
        return []

    def dispatch_wp08(self, *, candidate_sha: str) -> int:
        self.dispatched.append(candidate_sha)
        return 43


def _comment(body: str, *, actor: str = "toctionyan", issue_number: int = 219) -> dict:
    return {
        "issue": {"number": issue_number},
        "comment": {"body": body, "user": {"login": actor}},
    }


def test_scheduled_reconcile_recovers_missed_completed_wp08_failure() -> None:
    api = FakeAPI(_state())
    RECOVERY.reconcile(api)
    current = STATE.parse_issue_state(api.issue["body"])
    assert current is not None
    assert current["status"] == STATE.STATUS_FAILED_NEEDS_CLASSIFICATION
    assert current["last_wp08_run_id"] == 42
    assert current["failure_signature"] == "wp08:failure"
    assert current["attempt"] == 2
    assert api.dispatched == []


def test_environment_classification_requires_exact_failed_run_and_candidate() -> None:
    api = FakeAPI(_state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION))
    RECOVERY.handle_issue_comment(
        api,
        _comment("/wp08 classify environment_configuration run=42"),
    )
    current = STATE.parse_issue_state(api.issue["body"])
    assert current is not None
    assert current["status"] == STATE.STATUS_AWAITING_ENVIRONMENT_CONFIGURATION
    assert current["failure_signature"] == (
        "environment_configuration:missing_or_invalid_protected_environment_configuration"
    )
    assert api.dispatched == []


def test_environment_classification_rejects_wrong_run_id() -> None:
    api = FakeAPI(_state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION))
    with pytest.raises(RECOVERY.RecoveryError, match="does not match active WP-08 run"):
        RECOVERY.handle_issue_comment(
            api,
            _comment("/wp08 classify environment_configuration run=99"),
        )


def test_environment_control_rejects_unauthorized_commenter() -> None:
    api = FakeAPI(_state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION))
    with pytest.raises(RECOVERY.RecoveryError, match="not authorized"):
        RECOVERY.handle_issue_comment(
            api,
            _comment("/wp08 classify environment_configuration run=42", actor="someone-else"),
        )


def test_environment_resume_dispatches_same_candidate_and_increments_attempt() -> None:
    api = FakeAPI(_state(status=STATE.STATUS_AWAITING_ENVIRONMENT_CONFIGURATION))
    RECOVERY.handle_issue_comment(
        api,
        _comment("/wp08 resume environment_configuration run=42"),
    )
    current = STATE.parse_issue_state(api.issue["body"])
    assert current is not None
    assert api.dispatched == ["b" * 40]
    assert current["status"] == STATE.STATUS_CERTIFYING
    assert current["attempt"] == 3
    assert current["current_wp08_run_id"] == 43
    assert current["production_closed"] is False


def test_environment_resume_is_forbidden_before_classification() -> None:
    api = FakeAPI(_state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION))
    with pytest.raises(RECOVERY.RecoveryError, match="not awaiting protected Environment"):
        RECOVERY.handle_issue_comment(
            api,
            _comment("/wp08 resume environment_configuration run=42"),
        )


def test_coordinator_workflow_has_reconciliation_and_comment_control_without_secrets() -> None:
    workflow = (ROOT / ".github" / "workflows" / "wp08-release-coordinator.yml").read_text(encoding="utf-8")
    assert "schedule:" in workflow
    assert "cron: '2-59/5 * * * *'" in workflow
    assert "issue_comment:" in workflow
    assert "scripts/wp08_release_recovery.py --mode reconcile" in workflow
    assert "scripts/wp08_release_recovery.py --mode issue-comment" in workflow
    assert "actions: write" in workflow
    assert "issues: write" in workflow
    assert "environment: production-certification" not in workflow
    assert "secrets." not in workflow


def test_environment_wait_state_still_cannot_claim_production_closed() -> None:
    row = _state(status=STATE.STATUS_AWAITING_ENVIRONMENT_CONFIGURATION)
    with pytest.raises(STATE.ReleaseStateError, match="cannot claim production_closed"):
        STATE.validate_state({**row, "production_closed": True})
