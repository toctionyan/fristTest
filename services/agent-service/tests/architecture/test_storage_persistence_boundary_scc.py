from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from tests.support.paths import workspace_root
from tests.support.dependency_debt import assert_dependency_debt_monotonic


def _agent_core_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agent_core"):
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names if alias.name.startswith("agent_core"))
    return modules


def test_storage_is_port_only_and_exits_main_scc(tmp_path, monkeypatch):
    root = workspace_root(__file__)
    service = root / "services" / "agent-service"
    core = service / "src" / "agent_core"
    storage = core / "storage"

    forbidden: list[str] = []
    for path in storage.rglob("*.py"):
        for module in _agent_core_imports(path):
            if any(module == f"agent_core.{name}" or module.startswith(f"agent_core.{name}.") for name in ("persistence", "observability", "runtime")):
                forbidden.append(f"{path.relative_to(root)} -> {module}")
    assert forbidden == []

    for old_name in ("factory.py", "settings.py", "sqlalchemy_provider.py"):
        assert not (storage / old_name).exists(), f"concrete implementation remains in storage: {old_name}"

    expected = {
        "database_settings.py",
        "store_provider.py",
        "sqlalchemy_provider.py",
    }
    assert expected.issubset({path.name for path in (core / "persistence").glob("*.py")})

    stale = []
    for path in service.rglob("*.py"):
        if path.resolve() == Path(__file__).resolve():
            continue
        text = path.read_text(encoding="utf-8")
        for old_module in (
            "agent_core.storage.factory",
            "agent_core.storage.settings",
            "agent_core.storage.sqlalchemy_provider",
        ):
            if old_module in text:
                stale.append(f"{path.relative_to(root)} -> {old_module}")
    assert stale == []

    monkeypatch.setenv("APP_PROFILE", "local")
    sys.path.insert(0, str(service / "src"))
    try:
        from agent_core.persistence.database_settings import DatabaseSettings
        from agent_core.persistence.store_provider import build_sqlite_store_provider
        provider = build_sqlite_store_provider(
            DatabaseSettings(
                backend="sqlite",
                database_url=f"sqlite:///{tmp_path / 'provider.db'}",
                sqlite_path=tmp_path / "provider.db",
                create_schema=True,
            )
        )
        assert provider.threads is not None
        assert provider.messages is not None
        assert provider.transactions is not None
        provider.close()
    finally:
        sys.path.pop(0)

    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(root / "architecture-skill" / "scripts" / "verify_convergence.py"),
            "--workspace-root",
            str(root),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    debt = payload["checks"]["dependency_cycle_debt"]
    assert debt["baseline_member_count"] == 14
    assert_dependency_debt_monotonic(
        payload, removed_member="storage", maximum_current_members=6
    )
    assert all("storage" not in cycle for cycle in debt["current_cycles"])
    assert all(
        all(member not in cycle for member in ("context", "modules", "kernel", "resources", "ledger", "rag", "utils"))
        for cycle in debt["current_cycles"]
    )
