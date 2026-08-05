from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_failure_sweeper_event.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_failure_sweeper_event", SCRIPT)
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
        "head_branch": "canary/failure",
        "head_repository": {"full_name": REPO},
        "pull_requests": [{"number": PR_NUMBER}],
        "html_url": "https://example.invalid/run/123456",
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
        expected_run_id=RUN_ID,
        expected_pr_number=PR_NUMBER,
        run=run or _run(),
        pull_request=pull_request or _pr(),
    )


def test_valid_failed_run_is_bound_for_stage1_only() -> None:
    event = _build()
    assert event["workflow_run"]["id"] == RUN_ID
    assert event["recovery"]["schema"] == "failed-run-sweeper@1"
    assert event["recovery"]["mode"] == "scheduled-stage1-only"
    assert event["recovery"]["source_pr_number"] == PR_NUMBER
    assert len(event["recovery"]["binding_sha256"]) == 64


@pytest.mark.parametrize(
    "run",
    [
        _run(status="in_progress"),
        _run(conclusion="success"),
        _run(conclusion="neutral"),
        _run(name="wp08-full-stack-certification"),
        _run(name="unknown-workflow"),
        _run(id=RUN_ID + 1),
        _run(head_repository={"full_name": "fork/repo"}),
        _run(head_sha="not-a-sha"),
        _run(head_branch="governed-repair/quality-123"),
        _run(pull_requests=[]),
    ],
)
def test_invalid_stale_or_recursive_runs_are_rejected(run) -> None:
    with pytest.raises(MODULE.SweeperEventError):
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
def test_closed_stale_or_foreign_prs_are_rejected(pull_request) -> None:
    with pytest.raises(MODULE.SweeperEventError):
        _build(pull_request=pull_request)


def test_selected_identity_cannot_be_substituted() -> None:
    with pytest.raises(MODULE.SweeperEventError):
        MODULE.build_event(
            repository=REPO,
            expected_run_id=RUN_ID + 1,
            expected_pr_number=PR_NUMBER,
            run=_run(),
            pull_request=_pr(),
        )
    with pytest.raises(MODULE.SweeperEventError):
        MODULE.build_event(
            repository=REPO,
            expected_run_id=RUN_ID,
            expected_pr_number=PR_NUMBER + 1,
            run=_run(),
            pull_request=_pr(),
        )


def test_repository_must_be_owner_name() -> None:
    with pytest.raises(MODULE.SweeperEventError):
        MODULE.build_event(
            repository="invalid",
            expected_run_id=RUN_ID,
            expected_pr_number=PR_NUMBER,
            run=_run(),
            pull_request=_pr(),
        )
