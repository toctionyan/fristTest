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
    assert result["automatic_repair_roots"] == ["services/", "web/", "contracts/"]
    assert result["repair_base_branch"] == "feature/test"


def test_skill_self_validation_can_repair_product_path_but_not_skill_control_plane(tmp_path: Path) -> None:
    product = tmp_path / "services" / "agent-service" / "app" / "main.py"
    product.parent.mkdir(parents=True)
    product.write_text("x = 1\n", encoding="utf-8")
    skill = tmp_path / "skill-system" / "controller" / "task_run.py"
    skill.parent.mkdir(parents=True)
    skill.write_text("x = 1\n", encoding="utf-8")
    result = MODULE.build_report(
        _event(workflow="skill-self-validation"),
        workspace=tmp_path,
        artifact_files=[(tmp_path / "log.txt", "services/agent-service/app/main.py failed; skill-system/controller/task_run.py failed")],
    )
    assert result["classification"] == "code_or_contract"
    assert result["repair_allowed"] is True
    assert result["candidate_paths"] == ["services/agent-service/app/main.py"]


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
    path = tmp_path / "services" / "agent-service" / "app" / "main.py"
    path.parent.mkdir(parents=True)
    path.write_text("x=1\n", encoding="utf-8")
    result = MODULE.build_report(
        event,
        workspace=tmp_path,
        artifact_files=[(tmp_path / "run-summary.json", '{"results":[{"id":"unit","status":"FAIL","stderr":"services/agent-service/app/main.py failed"}]}')],
    )
    assert result["same_repository"] is False
    assert result["repair_allowed"] is False


def test_scripts_workflows_and_governance_paths_are_never_candidates(tmp_path: Path) -> None:
    for relative in (
        "scripts/github_agent_fixer.py",
        ".github/workflows/quality.yml",
        "governance/quality-loop-policy.json",
    ):
        protected = tmp_path / relative
        protected.parent.mkdir(parents=True, exist_ok=True)
        protected.write_text("x=1\n", encoding="utf-8")
    text = "scripts/github_agent_fixer.py failed .github/workflows/quality.yml failed governance/quality-loop-policy.json failed"
    assert MODULE.extract_candidate_paths(text, tmp_path) == []
