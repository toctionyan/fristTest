from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


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


def test_stage3_agent_python_targeted_suite_uses_src_layout_and_standard_marker_scope(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "existing-path")
    workspace = tmp_path / "candidate"

    command, cwd = MODULE._component_command("agent-python", workspace)
    env = MODULE._targeted_env("agent-python")

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
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTHONPATH"].split(os.pathsep)[:3] == ["src", ".", "existing-path"]


def test_stage3_non_python_target_does_not_rewrite_pythonpath(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "existing-path")

    env = MODULE._targeted_env("agent-frontend")

    assert env["PYTHONPATH"] == "existing-path"
