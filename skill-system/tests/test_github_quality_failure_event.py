from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_quality_failure_event.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_quality_failure_event", SCRIPT)
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


def _source_event(**overrides):
    event = {
        "repository": {"full_name": REPO},
        "pull_request": {
            "number": PR_NUMBER,
            "head": {
                "sha": SHA,
                "ref": "canary/failure",
                "repo": {"full_name": REPO},
            },
            "base": {"ref": "main"},
        },
    }
    event.update(overrides)
    return event


def _build(event=None, repository=REPO):
    return MODULE.build_event(
        source_event=event or _source_event(),
        repository=repository,
        run_id=RUN_ID,
        run_attempt=RUN_ATTEMPT,
        workflow_url="https://example.invalid/actions/runs/123456",
    )


def test_valid_same_repository_pr_becomes_stage1_only_workflow_run() -> None:
    event = _build()
    run = event["workflow_run"]
    direct = event["direct_handoff"]
    assert run["id"] == RUN_ID
    assert run["run_attempt"] == RUN_ATTEMPT
    assert run["name"] == "quality"
    assert run["status"] == "completed"
    assert run["conclusion"] == "failure"
    assert run["head_sha"] == SHA
    assert run["pull_requests"][0]["number"] == PR_NUMBER
    assert direct["mode"] == "in-run-stage1-only"
    assert direct["production_closed"] is False
    assert len(direct["binding_sha256"]) == 64


@pytest.mark.parametrize(
    "event",
    [
        {"repository": {"full_name": REPO}},
        _source_event(repository={"full_name": "other/repo"}),
        _source_event(
            pull_request={
                "number": PR_NUMBER,
                "head": {
                    "sha": SHA,
                    "ref": "canary/failure",
                    "repo": {"full_name": "fork/repo"},
                },
                "base": {"ref": "main"},
            }
        ),
        _source_event(
            pull_request={
                "number": PR_NUMBER,
                "head": {
                    "sha": "not-a-sha",
                    "ref": "canary/failure",
                    "repo": {"full_name": REPO},
                },
                "base": {"ref": "main"},
            }
        ),
        _source_event(
            pull_request={
                "number": PR_NUMBER,
                "head": {
                    "sha": SHA,
                    "ref": "governed-repair/quality-1",
                    "repo": {"full_name": REPO},
                },
                "base": {"ref": "main"},
            }
        ),
    ],
)
def test_missing_foreign_invalid_or_recursive_pr_is_rejected(event) -> None:
    with pytest.raises(MODULE.DirectQualityEventError):
        _build(event=event)


def test_invalid_repository_or_run_identity_is_rejected() -> None:
    with pytest.raises(MODULE.DirectQualityEventError):
        _build(repository="invalid")
    with pytest.raises(MODULE.DirectQualityEventError):
        MODULE.build_event(
            source_event=_source_event(),
            repository=REPO,
            run_id=0,
            run_attempt=RUN_ATTEMPT,
            workflow_url="https://example.invalid",
        )
    with pytest.raises(MODULE.DirectQualityEventError):
        MODULE.build_event(
            source_event=_source_event(),
            repository=REPO,
            run_id=RUN_ID,
            run_attempt=0,
            workflow_url="https://example.invalid",
        )
