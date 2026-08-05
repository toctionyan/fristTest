from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_failure_ingest.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_failure_ingest", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def _event(*, workflow: str = "quality", branch: str = "feature/test", conclusion: str = "failure"):
    return {
        "repository": {"full_name": "owner/repo"},
        "workflow_run": {
            "id": 123,
            "run_attempt": 1,
            "name": workflow,
            "conclusion": conclusion,
            "head_sha": "a" * 40,
            "head_branch": branch,
            "head_repository": {"full_name": "owner/repo"},
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


def test_quality_failure_is_repairable_with_frozen_candidate_path(tmp_path: Path) -> None:
    path = tmp_path / "services" / "agent-service" / "app" / "main.py"
    path.parent.mkdir(parents=True)
    path.write_text("x = 1\n", encoding="utf-8")
    files = [(tmp_path / "run-summary.json", '{"results":[{"id":"unit","status":"FAIL","stderr":"services/agent-service/app/main.py:1 failed"}]}')]
    result = MODULE.build_report(_event(), workspace=tmp_path, artifact_files=files)
    assert result["classification"] == "code_or_contract"
    assert result["repair_allowed"] is True
    assert result["candidate_paths"] == ["services/agent-service/app/main.py"]
    assert result["repair_base_branch"] == "feature/test"


def test_environment_failure_is_fail_closed(tmp_path: Path) -> None:
    result = MODULE.build_report(
        _event(workflow="wp08-full-stack-certification", branch="main"),
        workspace=tmp_path,
        artifact_files=[(tmp_path / "log.txt", "ERROR missing secret PRODUCTION_MODEL_API_KEY")],
    )
    assert result["classification"] == "environment"
    assert result["repair_allowed"] is False
    assert result["repair_base_branch"] == "main"


def test_fork_failure_cannot_receive_secrets_or_repairs(tmp_path: Path) -> None:
    event = _event()
    event["workflow_run"]["head_repository"]["full_name"] = "fork/repo"
    path = tmp_path / "scripts" / "normal.py"
    path.parent.mkdir()
    path.write_text("x=1\n", encoding="utf-8")
    result = MODULE.build_report(
        event,
        workspace=tmp_path,
        artifact_files=[(tmp_path / "run-summary.json", '{"results":[{"id":"unit","status":"FAIL","stderr":"scripts/normal.py failed"}]}')],
    )
    assert result["same_repository"] is False
    assert result["repair_allowed"] is False


def test_bridge_and_governance_paths_are_never_candidates(tmp_path: Path) -> None:
    protected = tmp_path / "scripts" / "github_agent_fixer.py"
    protected.parent.mkdir()
    protected.write_text("x=1\n", encoding="utf-8")
    assert MODULE.extract_candidate_paths("scripts/github_agent_fixer.py failed", tmp_path) == []
