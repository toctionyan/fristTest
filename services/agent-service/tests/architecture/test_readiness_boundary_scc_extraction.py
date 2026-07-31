from __future__ import annotations

import ast
import json
import subprocess
import sys

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


def test_rag_exits_main_scc_when_readiness_moves_to_application_layer():
    root = workspace_root(__file__)
    service = root / "services" / "agent-service"
    core = service / "src" / "agent_core"
    old_owner = core / "runtime" / "readiness.py"
    new_owner = service / "app" / "services" / "readiness_service.py"
    health_api = service / "app" / "api" / "health_api.py"

    assert not old_owner.exists(), "cross-subsystem readiness must not be owned by agent_core.runtime"
    assert new_owner.is_file(), "app.services must own deployment readiness aggregation"
    assert "from app.services.readiness_service import readiness_report" in health_api.read_text(encoding="utf-8")

    runtime_rag_edges: list[str] = []
    for path in (core / "runtime").rglob("*.py"):
        for module in _agent_core_imports(path):
            if module.startswith("agent_core.rag"):
                runtime_rag_edges.append(f"{path.relative_to(root)} -> {module}")
    assert runtime_rag_edges == []

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
        payload, removed_member="rag", maximum_current_members=12
    )
    assert all("rag" not in cycle for cycle in debt["current_cycles"])
