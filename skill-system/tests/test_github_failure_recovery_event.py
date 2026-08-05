from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_failure_recovery_event.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_failure_recovery_event", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()
REPO = "owner/repo"
SHA = "a" * 40


def _run(**overrides):
    payload = {
        "id": 123,
        "run_attempt": 1,
        "name": "quality",
        "status": "completed",
        "conclusion": "failure",
        "event": "pull_request",
        "head_sha": SHA,
        "head_branch": "feature/canary",
        "head_repository": {"full_name": REPO},
        "html_url": "https://example.invalid/actions/runs/123",
        "pull_requests": [
            {
                "number": 7,
                "head": {"ref": "feature/canary", "sha": SHA},
                "base": {"ref": "main"},
            }
        ],
    }
    payload.update(overrides)
    return payload


def _pr(**overrides):
    payload = {
        "number": 7,
        "state": "open",
        "draft": True,
        "head": {
            "sha": SHA,
            "ref": "feature/canary",
            "repo": {"full_name": REPO},
        },
        "base": {"ref": "main", "repo": {"full_name": REPO}},
    }
    payload.update(overrides)
    return payload


def _build(run=None, pull_request=None, *, expected_run_id=123):
    return MODULE.build_event(
        repository=REPO,
        issue_number=7,
        expected_run_id=expected_run_id,
        run=run or _run(),
        pull_request=pull_request or _pr(),
    )


def test_exact_owner_recovery_command_is_numeric_only() -> None:
    assert MODULE.parse_command("/governed-repair-run 123") == 123
    assert MODULE.parse_command("  /governed-repair-run 123  ") == 123
    for invalid in (
        "/governed-repair-run",
        "/governed-repair-run 0",
        "/governed-repair-run 123 extra",
        "/governed-repair-run $(id)",
        "please /governed-repair-run 123",
    ):
        with pytest.raises(MODULE.RecoveryEventError):
            MODULE.parse_command(invalid)


def test_build_event_binds_same_repo_current_open_pr_and_failed_run() -> None:
    event = _build()
    assert event["repository"]["full_name"] == REPO
    assert event["workflow_run"]["id"] == 123
    assert event["workflow_run"]["head_sha"] == SHA
    assert event["recovery"]["source_pr_number"] == 7
    assert event["recovery"]["source_run_id"] == 123
    assert len(event["recovery"]["binding_sha256"]) == 64


def test_requested_run_id_must_match_fetched_run() -> None:
    with pytest.raises(MODULE.RecoveryEventError, match="requested run id"):
        _build(expected_run_id=124)


def test_successful_or_incomplete_runs_are_rejected() -> None:
    with pytest.raises(MODULE.RecoveryEventError, match="conclusion"):
        _build(run=_run(conclusion="success"))
    with pytest.raises(MODULE.RecoveryEventError, match="not completed"):
        _build(run=_run(status="in_progress", conclusion=None))


def test_unknown_workflow_is_rejected() -> None:
    with pytest.raises(MODULE.RecoveryEventError, match="not recoverable"):
        _build(run=_run(name="untrusted-workflow"))


def test_fork_run_and_fork_pull_request_are_rejected() -> None:
    with pytest.raises(MODULE.RecoveryEventError, match="head repository"):
        _build(run=_run(head_repository={"full_name": "fork/repo"}))
    fork_pr = _pr(
        head={"sha": SHA, "ref": "feature/canary", "repo": {"full_name": "fork/repo"}}
    )
    with pytest.raises(MODULE.RecoveryEventError, match="head repository"):
        _build(pull_request=fork_pr)


def test_stale_run_cannot_be_replayed_after_pr_head_moves() -> None:
    moved_pr = _pr(
        head={"sha": "b" * 40, "ref": "feature/canary", "repo": {"full_name": REPO}}
    )
    with pytest.raises(MODULE.RecoveryEventError, match="stale"):
        _build(pull_request=moved_pr)


def test_run_must_reference_commented_pull_request() -> None:
    with pytest.raises(MODULE.RecoveryEventError, match="not bound"):
        _build(run=_run(pull_requests=[{"number": 8}]))


def test_pull_request_must_be_current_and_open() -> None:
    with pytest.raises(MODULE.RecoveryEventError, match="number"):
        _build(pull_request=_pr(number=8))
    with pytest.raises(MODULE.RecoveryEventError, match="remain open"):
        _build(pull_request=_pr(state="closed"))
