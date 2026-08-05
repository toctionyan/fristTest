from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_repair_orchestrator.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_repair_orchestrator", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.com")
    source = repo / "services" / "app.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "services/app.py")
    _git(repo, "commit", "-m", "base")
    return repo, source


def test_git_snapshot_changes_after_committed_repair(tmp_path: Path) -> None:
    repo, source = _repo(tmp_path)
    before = MODULE._git_snapshot(repo)

    source.write_text("value = 2\n", encoding="utf-8")
    commit = MODULE._commit_repair_cycle(
        repo,
        cycle=1,
        run_id="123",
        allowed_paths=["services/app.py"],
    )
    after = MODULE._git_snapshot(repo)

    assert before != after
    assert commit == MODULE._git_head(repo)
    assert MODULE._git_status(repo) == []


def test_dirty_candidate_is_visible_before_secret_bearing_repair(tmp_path: Path) -> None:
    repo, source = _repo(tmp_path)
    source.write_text("supply_chain_mutation = True\n", encoding="utf-8")
    assert MODULE._git_status(repo) == [" M services/app.py"]


def test_untracked_candidate_file_is_visible_before_secret_bearing_repair(tmp_path: Path) -> None:
    repo, _source = _repo(tmp_path)
    unexpected = repo / "services" / "unexpected.py"
    unexpected.write_text("value = 3\n", encoding="utf-8")
    assert MODULE._git_status(repo) == ["?? services/unexpected.py"]
