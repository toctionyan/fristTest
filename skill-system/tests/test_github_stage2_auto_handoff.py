from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_stage2_auto_handoff.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_stage2_auto_handoff", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()
REPO = "owner/repo"
RUN_ID = 123456
RUN_ATTEMPT = 2
PR_NUMBER = 17
SHA = "a" * 40
SIGNATURE = "b" * 64
SOURCE = "services/agent-service/app/main.py"


def _run(**overrides):
    payload = {
        "id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "name": "quality",
        "status": "completed",
        "conclusion": "failure",
        "event": "pull_request",
        "head_sha": SHA,
        "head_branch": "feature/failure",
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


def _failure(**overrides):
    payload = {
        "schema": "github-failure-ingest@1",
        "status": "INGESTED",
        "repository": REPO,
        "workflow_name": "quality",
        "workflow_run_id": str(RUN_ID),
        "workflow_run_attempt": str(RUN_ATTEMPT),
        "head_sha": SHA,
        "same_repository": True,
        "classification": "code_or_contract",
        "repair_allowed": True,
        "failure_signature": SIGNATURE,
        "candidate_paths": [SOURCE, "scripts/incidental_verifier.py"],
        "source_changed_files": [SOURCE],
    }
    payload.update(overrides)
    return payload


def _task(**binding_overrides):
    binding = {
        "repository": REPO,
        "workflow_name": "quality",
        "workflow_run_id": str(RUN_ID),
        "workflow_run_attempt": str(RUN_ATTEMPT),
        "head_sha": SHA,
        "failure_signature": SIGNATURE,
    }
    binding.update(binding_overrides)
    return {"binding": binding}


def _validate(*, run=None, pull_request=None, failure=None, task=None):
    return MODULE.validate_handoff(
        repository=REPO,
        run=run or _run(),
        pull_request=pull_request or _pr(),
        failure=failure or _failure(),
        task=task or _task(),
    )


def test_valid_current_failure_is_ready_for_automatic_stage2() -> None:
    result = _validate()
    assert result["status"] == "READY"
    assert result["source_run_id"] == str(RUN_ID)
    assert result["source_run_attempt"] == str(RUN_ATTEMPT)
    assert result["source_pr_number"] == PR_NUMBER
    assert result["repairable_paths"] == [SOURCE]
    assert result["dispatch_marker"] == f"AUTO_STAGE2_DISPATCHED:{RUN_ID}/{RUN_ATTEMPT}"
    assert len(result["binding_sha256"]) == 64
    assert result["production_closed"] is False


@pytest.mark.parametrize(
    "run",
    [
        _run(status="in_progress"),
        _run(conclusion="success"),
        _run(name="wp08-full-stack-certification"),
        _run(head_repository={"full_name": "fork/repo"}),
        _run(head_sha="not-a-sha"),
        _run(head_branch="governed-repair/quality-123"),
        _run(pull_requests=[]),
    ],
)
def test_non_failed_foreign_stale_or_recursive_runs_are_rejected(run) -> None:
    with pytest.raises(MODULE.AutoHandoffError):
        _validate(run=run)


@pytest.mark.parametrize(
    "pull_request",
    [
        _pr(state="closed"),
        _pr(number=PR_NUMBER + 1),
        _pr(head={"sha": "c" * 40, "repo": {"full_name": REPO}}),
        _pr(head={"sha": SHA, "repo": {"full_name": "fork/repo"}}),
        {"number": PR_NUMBER, "state": "open"},
    ],
)
def test_closed_stale_or_foreign_pull_requests_are_rejected(pull_request) -> None:
    with pytest.raises(MODULE.AutoHandoffError):
        _validate(pull_request=pull_request)


@pytest.mark.parametrize(
    "failure",
    [
        _failure(status="PARTIAL"),
        _failure(repair_allowed=False),
        _failure(classification="environment"),
        _failure(same_repository=False),
        _failure(workflow_run_id="999"),
        _failure(workflow_run_attempt="1"),
        _failure(head_sha="d" * 40),
        _failure(candidate_paths=["scripts/incidental_verifier.py"]),
        _failure(source_changed_files=[]),
    ],
)
def test_unbound_or_nonrepairable_stage1_evidence_is_rejected(failure) -> None:
    with pytest.raises(MODULE.AutoHandoffError):
        _validate(failure=failure)


def test_task_run_binding_must_match_exact_failure() -> None:
    with pytest.raises(MODULE.AutoHandoffError):
        _validate(task=_task(failure_signature="e" * 64))


def test_changed_file_metadata_can_only_narrow_the_candidate_scope() -> None:
    failure = _failure(
        candidate_paths=[SOURCE, "services/agent-service/app/other.py"],
        source_changed_files=[SOURCE, "README.md"],
    )
    result = _validate(failure=failure)
    assert result["repairable_paths"] == [SOURCE]
