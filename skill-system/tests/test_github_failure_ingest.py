from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_failure_ingest.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_failure_ingest", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def _event(
    *,
    workflow: str = "quality",
    branch: str = "feature/test",
    conclusion: str = "failure",
    repository: str = "owner/repo",
):
    return {
        "repository": {"full_name": repository},
        "workflow_run": {
            "id": 123,
            "run_attempt": 1,
            "name": workflow,
            "event": "pull_request",
            "conclusion": conclusion,
            "head_sha": "a" * 40,
            "head_branch": branch,
            "head_repository": {"full_name": repository},
            "html_url": "https://example.invalid/run/123",
            "pull_requests": [
                {
                    "number": 7,
                    "head": {"ref": branch},
                    "base": {"ref": "main"},
                }
            ],
        },
    }


def _source_file(tmp_path: Path, relative: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n", encoding="utf-8")
    return path


def _summary(tmp_path: Path, stderr: str, *, status: str = "FAIL"):
    return [
        (
            tmp_path / "run-summary.json",
            json.dumps(
                {
                    "results": [
                        {
                            "id": "unit",
                            "status": status,
                            "category": "test",
                            "owner": "agent-runtime",
                            "stderr": stderr,
                        }
                    ]
                }
            ),
        )
    ]


def test_quality_failure_is_repairable_only_with_gate_and_evidence_path(
    tmp_path: Path,
) -> None:
    _source_file(tmp_path, "services/agent-service/app/main.py")
    files = _summary(
        tmp_path,
        "services/agent-service/app/main.py:1 assertion failed",
    )
    result = MODULE.build_report(
        _event(),
        workspace=tmp_path,
        artifact_files=files,
        changed_files=["services/agent-service/app/main.py", "README.md"],
    )
    assert result["classification"] == "code_or_contract"
    assert result["repair_allowed"] is True
    assert result["candidate_paths"] == ["services/agent-service/app/main.py"]
    assert result["repair_base_branch"] == "feature/test"
    assert result["production_closed"] is False


def test_changed_files_alone_do_not_expand_repair_scope(tmp_path: Path) -> None:
    _source_file(tmp_path, "services/agent-service/app/main.py")
    result = MODULE.build_report(
        _event(),
        workspace=tmp_path,
        artifact_files=[(tmp_path / "job.log", "process failed without a source path")],
        changed_files=["services/agent-service/app/main.py"],
    )
    assert result["classification"] == "unknown_failure_without_gate_evidence"
    assert result["candidate_paths"] == []
    assert result["repair_allowed"] is False


def test_environment_failure_is_fail_closed(tmp_path: Path) -> None:
    result = MODULE.build_report(
        _event(
            workflow="wp08-full-stack-certification",
            branch="main",
        ),
        workspace=tmp_path,
        artifact_files=[
            (
                tmp_path / "log.txt",
                "ERROR missing secret PRODUCTION_MODEL_API_KEY",
            )
        ],
    )
    assert result["classification"] == "environment"
    assert result["repair_allowed"] is False
    assert result["repair_base_branch"] == "main"


@pytest.mark.parametrize(
    ("conclusion", "expected"),
    [
        ("timed_out", "timeout"),
        ("cancelled", "cancelled"),
        ("action_required", "policy_or_approval"),
        ("startup_failure", "runner_or_platform"),
    ],
)
def test_non_code_conclusions_never_start_source_repair(
    tmp_path: Path,
    conclusion: str,
    expected: str,
) -> None:
    _source_file(tmp_path, "services/agent-service/app/main.py")
    result = MODULE.build_report(
        _event(conclusion=conclusion),
        workspace=tmp_path,
        artifact_files=_summary(
            tmp_path,
            "services/agent-service/app/main.py failed",
        ),
    )
    assert result["classification"] == expected
    assert result["repair_allowed"] is False


def test_fork_failure_cannot_be_repairable(tmp_path: Path) -> None:
    event = _event()
    event["workflow_run"]["head_repository"]["full_name"] = "fork/repo"
    _source_file(tmp_path, "services/agent-service/app/main.py")
    result = MODULE.build_report(
        event,
        workspace=tmp_path,
        artifact_files=_summary(
            tmp_path,
            "services/agent-service/app/main.py failed",
        ),
    )
    assert result["same_repository"] is False
    assert result["repair_allowed"] is False


def test_bridge_and_governance_paths_are_never_candidates(tmp_path: Path) -> None:
    _source_file(tmp_path, "scripts/github_failure_ingest.py")
    _source_file(tmp_path, "governance/task-ledger.json")
    text = (
        "scripts/github_failure_ingest.py failed\n"
        "governance/task-ledger.json failed"
    )
    assert MODULE.extract_candidate_paths(text, tmp_path) == []


def test_secret_redaction_covers_provider_and_github_tokens() -> None:
    text = (
        "api_key=sk-abcdefghijklmnopqrstuvwxyz "
        "Authorization: Bearer github_pat_abcdefghijklmnopqrstuvwxyz "
        "token=ghp_abcdefghijklmnopqrstuvwxyz"
    )
    redacted = MODULE.redact(text)
    assert "sk-" not in redacted
    assert "github_pat_" not in redacted
    assert "ghp_" not in redacted
    assert redacted.count("[REDACTED]") >= 2


def test_bounded_reader_does_not_follow_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "safe.log").write_text("failed safely\n", encoding="utf-8")
    outside = tmp_path / "outside.log"
    outside.write_text("api_key=sk-abcdefghijklmnopqrstuvwxyz\n", encoding="utf-8")
    link = root / "outside-link.log"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    rows = MODULE._bounded_text_files([root])
    names = [path.name for path, _text in rows]
    assert "safe.log" in names
    assert "outside-link.log" not in names


def test_task_run_waits_for_future_repair_when_code_failure_is_authorized(
    tmp_path: Path,
) -> None:
    report = {
        "repository": "owner/repo",
        "workflow_name": "quality",
        "workflow_run_id": "123",
        "workflow_run_attempt": "1",
        "head_sha": "a" * 40,
        "failure_signature": "b" * 64,
        "classification": "code_or_contract",
        "repair_allowed": True,
        "same_repository": True,
        "failed_gates": [{"gate_id": "unit"}],
        "candidate_paths": ["services/agent-service/app/main.py"],
    }
    task_path = tmp_path / "task-run.json"
    MODULE._create_task_run(report, task_path)
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    assert payload["status"] == "WAITING_EXTERNAL_RESULT"
    assert payload["phase"] == "REPAIR_READY"
    assert payload["conditions"]["failure_ingested"]["satisfied"] is True
    assert payload["conditions"]["classification_complete"]["satisfied"] is True
    assert payload["conditions"]["source_changed"]["satisfied"] is False


def test_task_run_is_blocked_for_environment_failure(tmp_path: Path) -> None:
    report = {
        "repository": "owner/repo",
        "workflow_name": "wp08-full-stack-certification",
        "workflow_run_id": "124",
        "workflow_run_attempt": "1",
        "head_sha": "c" * 40,
        "failure_signature": "d" * 64,
        "classification": "environment",
        "repair_allowed": False,
        "same_repository": True,
        "failed_gates": [],
        "candidate_paths": [],
    }
    task_path = tmp_path / "task-run.json"
    MODULE._create_task_run(report, task_path)
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["blockers"][-1]["code"] == "AUTOMATIC_REPAIR_NOT_AUTHORIZED"
