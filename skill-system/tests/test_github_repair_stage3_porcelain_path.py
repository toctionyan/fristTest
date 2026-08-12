from __future__ import annotations

import importlib.util
import subprocess
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
    spec = importlib.util.spec_from_file_location("github_repair_stage3_porcelain_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=path,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def test_stage3_changed_paths_preserves_first_character_for_unstaged_tracked_change(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "candidate"
    source = workspace / "services" / "agent-service" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 0\n", encoding="utf-8")
    _git(workspace, "init")
    _git(workspace, "config", "user.name", "test")
    _git(workspace, "config", "user.email", "test@example.invalid")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "baseline")

    source.write_text("value = 1\n", encoding="utf-8")

    assert MODULE._changed_paths(workspace) == ("services/agent-service/app.py",)
