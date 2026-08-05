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
RUN_ID = 123456
PR_NUMBER = 17
SHA = "a" * 40


def _run(**overrides):
    payload = {
        "id": RUN_ID,
        "name": "quality",
        "status": "completed",
        "conclusion": "failure",
        "run_attempt": 1,
        "head_sha": SHA,
        "head_repository": {"full_name": REPO},
        "pull_requests": [{"number": PR_NUMBER}],
    }
    payload.update(overrides)
    return payload


def _pr(**overrides):
    payload = {
        "number": PR_NUMBER,
        "state": "open",
        "head": {"sha": SHA, "repo": {"full_name": REPO}},
    }
    payload.update(overrides)
    return payload


def _build(run=None, pull_request=None):
    return MODULE.build_event(
        repository=REPO,
        issue_number=PR_NUMBER,
        expected_run_id=RUN_ID,
        run=run or _run(),
        pull_request=pull_request or _pr(),
    )


def test_exact_owner_recovery_command_is_numeric_only() -> None:
    assert MODULE.parse_command(f"/governed-repair-ingest {RUN_ID}") == RUN_ID
    for value in (
        "/governed-repair-ingest",
        "/governed-repair-run 123",
        "/governed-repair-ingest 123; echo pwned",
        "/governed-repair-ingest $RUN_ID",
        "/governed-repair-ingest -1",
    ):
        with pytest.raises(MODULE.RecoveryEventError):
            MODULE.parse_command(value)


def test_valid_failure_is_bound_to_current_open_pr() -> None:
    event = _build()
    assert event["workflow_run"]["id"] == RUN_ID
    assert event["recovery"]["mode"] == "stage1-only"
    assert event["recovery"]["source_pr_number"] == PR_NUMBER
    assert len(event["recovery"]["binding_sha256"]) == 64


@pytest.mark.parametrize(
    "run",
    [
        _run(status="in_progress"),
        _run(conclusion="success"),
        _run(conclusion="neutral"),
        _run(name="unknown-workflow"),
        _run(id=RUN_ID + 1),
        _run(head_repository={"full_name": "fork/repo"}),
        _run(head_sha="not-a-sha"),
        _run(pull_requests=[]),
    ],
)
def test_invalid_or_unbound_runs_are_rejected(run) -> None:
    with pytest.raises(MODULE.RecoveryEventError):
        _build(run=run)


@pytest.mark.parametrize(
    "pull_request",
    [
        _pr(number=PR_NUMBER + 1),
        _pr(state="closed"),
        _pr(head={"sha": "b" * 40, "repo": {"full_name": REPO}}),
        _pr(head={"sha": SHA, "repo": {"full_name": "fork/repo"}}),
        {"number": PR_NUMBER, "state": "open"},
    ],
)
def test_stale_closed_or_foreign_prs_are_rejected(pull_request) -> None:
    with pytest.raises(MODULE.RecoveryEventError):
        _build(pull_request=pull_request)


def test_only_current_repository_name_is_accepted() -> None:
    with pytest.raises(MODULE.RecoveryEventError):
        MODULE.build_event(
            repository="invalid",
            issue_number=PR_NUMBER,
            expected_run_id=RUN_ID,
            run=_run(),
            pull_request=_pr(),
        )
