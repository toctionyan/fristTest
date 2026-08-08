from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
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


STATE = _load("wp08_release_state_test", "scripts/wp08_release_state.py")
COORD = _load("wp08_release_coordinator_test", "scripts/wp08_release_coordinator.py")
CONTRACT = _load(
    "wp08_release_coordinator_contract_test",
    "scripts/wp08_release_coordinator_contract.py",
)


def _state(*, status: str = "CERTIFYING", attempt: int = 1, current_run: int = 42) -> dict:
    state = STATE.new_state(
        release_run_id="wp08-release-123456",
        authorized_initial_sha="a" * 40,
        current_candidate_sha="b" * 40,
        max_attempts=8,
        actor="tester",
    )
    state.update({
        "status": status,
        "attempt": attempt,
        "current_wp08_run_id": current_run,
    })
    return STATE.validate_state(state)


class FakeAPI:
    def __init__(self, state: dict) -> None:
        self.issue = {"number": 7, "body": STATE.render_issue_body(state)}
        self.dispatched: list[str] = []
        self.updated: list[dict] = []

    def list_issues(self, *, state: str = "open"):
        return [self.issue] if state == "open" else []

    def update_issue(self, issue_number: int, *, body: str, close: bool = False):
        assert issue_number == 7
        self.issue["body"] = body
        parsed = STATE.parse_issue_state(body)
        assert parsed is not None
        self.updated.append(parsed)
        return self.issue

    def dispatch_wp08(self, *, candidate_sha: str) -> int:
        self.dispatched.append(candidate_sha)
        return 9000 + len(self.dispatched)

    def list_workflow_runs(
        self,
        workflow_file: str,
        *,
        branch: str = "main",
        event: str | None = None,
    ):
        return []


def test_release_coordinator_static_contract_passes() -> None:
    result = CONTRACT.validate_static(ROOT)
    assert result["status"] == "PASS"
    assert result["single_human_authorization"] is True
    assert result["automatic_repair_continuation"] is True
    assert result["main_quality_required_before_continuation"] is True
    assert result["bounded_retry"] is True
    assert result["max_attempts"] == 8
    assert result["semantic_failure_auto_retry"] is False
    assert result["production_closed"] is False
    assert result["state_owner"] == "scripts/wp08_release_state.py"
    assert result["github_adapter"] == "scripts/wp08_release_github.py"
    assert any(
        row["authorized_initial_wp08_run_id"] == 31254298499
        for row in result["bootstraps"]
    )


def test_release_run_issue_state_round_trips_and_forbids_production_closure() -> None:
    state = _state()
    body = STATE.render_issue_body(state)
    parsed = STATE.parse_issue_state(body)
    assert parsed == state
    with pytest.raises(STATE.ReleaseStateError, match="cannot claim production_closed"):
        STATE.validate_state({**state, "production_closed": True})


def test_semantic_wp08_failure_never_blindly_retries() -> None:
    api = FakeAPI(_state())
    COORD.handle_wp08_run(api, {
        "event": "workflow_dispatch",
        "id": 42,
        "head_sha": "b" * 40,
        "conclusion": "failure",
    })
    assert api.dispatched == []
    current = STATE.parse_issue_state(api.issue["body"])
    assert current is not None
    assert current["status"] == STATE.STATUS_FAILED_NEEDS_CLASSIFICATION
    assert current["attempt"] == 1
    assert current["current_wp08_run_id"] == 42


def test_cancelled_wp08_run_retries_within_attempt_budget() -> None:
    api = FakeAPI(_state())
    COORD.handle_wp08_run(api, {
        "event": "workflow_dispatch",
        "id": 42,
        "head_sha": "b" * 40,
        "conclusion": "cancelled",
    })
    assert api.dispatched == ["b" * 40]
    current = STATE.parse_issue_state(api.issue["body"])
    assert current is not None
    assert current["status"] == STATE.STATUS_CERTIFYING
    assert current["attempt"] == 2
    assert current["current_wp08_run_id"] == 9001


def test_retry_stops_when_attempt_budget_is_exhausted() -> None:
    api = FakeAPI(_state(attempt=8))
    COORD.handle_wp08_run(api, {
        "event": "workflow_dispatch",
        "id": 42,
        "head_sha": "b" * 40,
        "conclusion": "timed_out",
    })
    assert api.dispatched == []
    current = STATE.parse_issue_state(api.issue["body"])
    assert current is not None
    assert current["status"] == STATE.STATUS_ATTEMPT_BUDGET_EXHAUSTED
    assert current["attempt"] == 8


def test_repair_merge_is_bound_to_release_and_failed_parent_run() -> None:
    api = FakeAPI(_state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION))
    COORD.handle_pull_request(api, {
        "pull_request": {
            "merged": True,
            "number": 217,
            "html_url": "https://github.com/toctionyan/fristTest/pull/217",
            "merge_commit_sha": "c" * 40,
            "base": {"ref": "main"},
            "body": (
                "WP08-Release-Run-ID: wp08-release-123456\n"
                "WP08-Parent-Run-ID: 42\n"
            ),
        }
    })
    assert api.dispatched == []
    current = STATE.parse_issue_state(api.issue["body"])
    assert current is not None
    assert current["status"] == STATE.STATUS_WAITING_REPAIR_CI
    assert current["current_candidate_sha"] == "c" * 40
    assert current["repair_pr"]["parent_wp08_run_id"] == 42


def test_repair_parent_mismatch_fails_closed() -> None:
    api = FakeAPI(_state(status=STATE.STATUS_FAILED_NEEDS_CLASSIFICATION))
    with pytest.raises(COORD.CoordinatorError, match="parent run does not match"):
        COORD.handle_pull_request(api, {
            "pull_request": {
                "merged": True,
                "number": 218,
                "html_url": "https://github.com/toctionyan/fristTest/pull/218",
                "merge_commit_sha": "c" * 40,
                "base": {"ref": "main"},
                "body": (
                    "WP08-Release-Run-ID: wp08-release-123456\n"
                    "WP08-Parent-Run-ID: 99\n"
                ),
            }
        })


def _copy_contract_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    for relative in (
        ".github/workflows/wp08-release-coordinator.yml",
        "scripts/wp08_release_coordinator.py",
        "scripts/wp08_release_state.py",
        "scripts/wp08_release_github.py",
        "scripts/wp08_release_coordinator_contract.py",
        "deployment/ci/release-toolchain-lock.json",
        "governance/release-runs/wp08-bootstrap-31254298499.json",
    ):
        source = ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return root


def test_coordinator_cannot_enter_production_secret_environment(tmp_path: Path) -> None:
    root = _copy_contract_workspace(tmp_path)
    workflow = root / ".github" / "workflows" / "wp08-release-coordinator.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8")
        + "\n# environment: production-certification\n",
        encoding="utf-8",
    )
    with pytest.raises(CONTRACT.CoordinatorContractError) as caught:
        CONTRACT.validate_static(root)
    assert caught.value.code == "coordinator_secret_boundary_invalid"


def test_coordinator_cannot_gain_contents_write(tmp_path: Path) -> None:
    root = _copy_contract_workspace(tmp_path)
    workflow = root / ".github" / "workflows" / "wp08-release-coordinator.yml"
    text = workflow.read_text(encoding="utf-8").replace(
        "contents: read",
        "contents: write",
        1,
    )
    workflow.write_text(text, encoding="utf-8")
    with pytest.raises(CONTRACT.CoordinatorContractError) as caught:
        CONTRACT.validate_static(root)
    assert caught.value.code in {
        "coordinator_workflow_contract_missing",
        "coordinator_secret_boundary_invalid",
    }


def test_partial_repair_markers_fail_closed() -> None:
    with pytest.raises(
        STATE.ReleaseStateError,
        match="both WP08-Release-Run-ID and WP08-Parent-Run-ID",
    ):
        STATE.parse_repair_markers(
            "WP08-Release-Run-ID: wp08-release-123456"
        )
