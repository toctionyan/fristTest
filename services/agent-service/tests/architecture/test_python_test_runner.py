from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.support.paths import workspace_root


def _runner():
    root = workspace_root(__file__)
    path = root / "scripts" / "run_python_test_suites.py"
    spec = importlib.util.spec_from_file_location("python_test_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_suite_runner_requires_each_locked_project_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUALITY_AGENT_PYTHON", raising=False)
    monkeypatch.delenv("QUALITY_BUSINESS_PYTHON", raising=False)
    runner = _runner()

    assert runner._locked_project_python(tmp_path, "services/business-service") is None

    executable = tmp_path / "services" / "business-service" / ".venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    assert runner._locked_project_python(tmp_path, "services/business-service") == executable


def test_suite_runner_accepts_explicit_quality_interpreter_without_resolving_venv_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner()
    executable = tmp_path / "quality-venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("QUALITY_BUSINESS_PYTHON", str(executable))

    assert runner._locked_project_python(tmp_path, "services/business-service") == executable.absolute()


def test_suite_runner_cannot_wait_forever_on_descendant_capture_pipes() -> None:
    root = workspace_root(__file__)
    source = (root / "scripts" / "run_python_test_suites.py").read_text(encoding="utf-8")

    assert 'start_new_session=True' in source
    assert 'os.killpg(proc.pid, signal.SIGTERM)' in source
    assert 'stdout_path = runtime_dir / "stdout.log"' in source
    assert 'env["COVERAGE_FILE"] = str(runtime_dir / ".coverage")' in source
    assert 'capture_output=True' not in source


def test_agent_coverage_uses_python312_sysmon_without_reducing_scope() -> None:
    root = workspace_root(__file__)
    source = (root / "scripts" / "run_python_test_suites.py").read_text(encoding="utf-8")

    assert 'env["COVERAGE_CORE"] = "sysmon"' in source
    assert 'coverage_args = ["--cov=agent_core", "--cov=app"]' in source
