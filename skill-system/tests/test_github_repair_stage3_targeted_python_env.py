from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPTS = ROOT / "scripts"
for entry in (str(CONTROL), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

SCRIPT = SCRIPTS / "github_repair_stage3.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_repair_stage3_targeted_env_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def test_stage3_agent_python_targeted_suite_uses_standard_deterministic_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "existing-path")
    monkeypatch.setenv("APP_PROFILE", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "inherited-real-secret")
    monkeypatch.setenv("APP_UNDECLARED_SETTING", "must-not-leak")
    workspace = tmp_path / "candidate"
    runtime_dir = tmp_path / "evidence" / "runtime" / "agent-python"

    command, cwd = MODULE._component_command("agent-python", workspace)
    env = MODULE._targeted_env("agent-python", runtime_dir=runtime_dir)

    assert command == [
        str(workspace / "services/agent-service/.venv/bin/python"),
        "-B",
        "-m",
        "pytest",
        "-q",
        "-ra",
        "-p",
        "no:cacheprovider",
        "-m",
        "not integration and not preprod",
        "tests",
    ]
    assert cwd == workspace / "services/agent-service"
    assert env["APP_PROFILE"] == MODULE.STANDARD_ENV["APP_PROFILE"] == "local"
    assert env["OPENAI_API_KEY"] == MODULE.STANDARD_ENV["OPENAI_API_KEY"]
    assert "APP_UNDECLARED_SETTING" not in env
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTHONPATH"].split(os.pathsep)[:3] == ["src", ".", "existing-path"]
    assert env["SQLITE_DB_PATH"] == str(runtime_dir.resolve() / "agent.sqlite3")
    assert env["CHECKPOINT_DB_PATH"] == str(runtime_dir.resolve() / "checkpoints.sqlite3")
    assert env["BUSINESS_DB_PATH"] == str(runtime_dir.resolve() / "business.sqlite3")
    assert env["UPLOAD_DIR"] == str(runtime_dir.resolve() / "uploads")
    assert runtime_dir.is_dir()


def test_stage3_business_python_uses_same_standard_config_without_agent_pythonpath_rewrite(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "existing-path")
    monkeypatch.setenv("BUSINESS_SERVICE_TOKEN", "inherited-token")
    runtime_dir = tmp_path / "evidence" / "runtime" / "business-python"

    env = MODULE._targeted_env("business-python", runtime_dir=runtime_dir)

    assert env["APP_PROFILE"] == "local"
    assert env["BUSINESS_SERVICE_TOKEN"] == MODULE.STANDARD_ENV["BUSINESS_SERVICE_TOKEN"]
    assert env["PYTHONPATH"] == "existing-path"
    assert env["DATABASE_URL"] == f"sqlite:///{runtime_dir.resolve() / 'agent.sqlite3'}"


def test_stage3_python_target_requires_isolated_runtime_directory() -> None:
    with pytest.raises(MODULE.Stage3Error, match="runtime directory is required"):
        MODULE._targeted_env("agent-python")


def test_stage3_non_python_target_does_not_rewrite_environment(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "existing-path")
    monkeypatch.setenv("APP_PROFILE", "production")

    env = MODULE._targeted_env("agent-frontend")

    assert env["PYTHONPATH"] == "existing-path"
    assert env["APP_PROFILE"] == "production"
