from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


REFRESH = _load("refresh_product_source_baseline", SCRIPTS / "refresh_product_source_baseline.py")


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=path, text=True, capture_output=True, check=True
    )
    return completed.stdout.strip()


def test_protected_baseline_refresh_uses_exact_git_object_tree(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    source = workspace / "services" / "agent-service" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 0\n", encoding="utf-8")
    _git(workspace, "init")
    _git(workspace, "config", "user.name", "test")
    _git(workspace, "config", "user.email", "test@example.invalid")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "source")
    source_sha = _git(workspace, "rev-parse", "HEAD")

    first = REFRESH.refresh_product_source_baseline(
        workspace,
        product_source_ref=source_sha,
    )
    assert first["status"] == "REFRESHED"
    payload = json.loads(
        (workspace / "skill-system/registry/product-source-baseline.json").read_text()
    )
    assert payload["schema_version"] == 3
    assert payload["product_source_ref"] == f"git-commit-sha1:{source_sha}"
    assert payload["entry_count"] == 1
    snapshot = (workspace / "skill-system/registry/product-source-baseline.json").read_text()

    source.write_text("worktree mutation\n", encoding="utf-8")
    second = REFRESH.refresh_product_source_baseline(
        workspace,
        product_source_ref=source_sha,
    )
    assert second["status"] == "CURRENT"
    assert (workspace / "skill-system/registry/product-source-baseline.json").read_text() == snapshot


def test_refresh_can_create_empty_v3_registry_from_git_object_tree(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _git(workspace, "init")
    _git(workspace, "config", "user.name", "test")
    _git(workspace, "config", "user.email", "test@example.invalid")
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "fixture")
    sha = _git(workspace, "rev-parse", "HEAD")
    result = REFRESH.refresh_product_source_baseline(workspace, product_source_ref=sha)
    assert result["entry_count"] == 0
    assert result["product_source_ref"] == f"git-commit-sha1:{sha}"
