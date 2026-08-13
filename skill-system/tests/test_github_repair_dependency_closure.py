from __future__ import annotations

import hashlib
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


def test_protected_baseline_refresh_is_deterministic_dependency_closure(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    source = workspace / "services" / "agent-service" / "app.py"
    baseline = workspace / "skill-system" / "registry" / "product-source-baseline.json"
    source.parent.mkdir(parents=True)
    baseline.parent.mkdir(parents=True)
    source.write_text("value = 0\n", encoding="utf-8")
    baseline.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "file_count": 1,
                "files": {
                    "services/agent-service/app.py": hashlib.sha256(source.read_bytes()).hexdigest()
                },
                "generated_at": "2000-01-01T00:00:00Z",
                "generated_from": "git:" + "0" * 40,
                "protected_roots": ["services"],
                "source_release_sha256": "x" * 64,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _git(workspace, "init")
    _git(workspace, "config", "user.name", "test")
    _git(workspace, "config", "user.email", "test@example.invalid")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "baseline")

    source.write_text("value = 1\n", encoding="utf-8")
    _git(workspace, "add", "services/agent-service/app.py")
    _git(workspace, "commit", "-m", "repair source")
    source_sha = _git(workspace, "rev-parse", "HEAD")

    first = REFRESH.refresh_product_source_baseline(
        workspace,
        generated_from_sha=source_sha,
    )
    assert first["status"] == "REFRESHED"
    assert first["changed"] is True
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert payload["generated_from"] == f"git:{source_sha}"
    assert payload["file_count"] == 1
    assert payload["files"]["services/agent-service/app.py"] == hashlib.sha256(source.read_bytes()).hexdigest()

    snapshot = baseline.read_text(encoding="utf-8")
    second = REFRESH.refresh_product_source_baseline(
        workspace,
        generated_from_sha=source_sha,
    )
    assert second["status"] == "CURRENT"
    assert second["changed"] is False
    assert baseline.read_text(encoding="utf-8") == snapshot


def test_missing_baseline_is_explicitly_non_configured_only_when_allowed(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _git(workspace, "init")
    _git(workspace, "config", "user.name", "test")
    _git(workspace, "config", "user.email", "test@example.invalid")
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "fixture")
    sha = _git(workspace, "rev-parse", "HEAD")

    result = REFRESH.refresh_product_source_baseline(
        workspace,
        generated_from_sha=sha,
        allow_missing=True,
    )
    assert result == {
        "status": "NOT_CONFIGURED",
        "changed": False,
        "path": None,
        "file_count": 0,
        "generated_from": None,
    }
