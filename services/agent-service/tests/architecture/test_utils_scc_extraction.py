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


def test_utils_package_exits_main_dependency_scc():
    root = workspace_root(__file__)
    core = root / "services" / "agent-service" / "src" / "agent_core"
    old_owner = core / "utils" / "flow_debug.py"
    new_owner = core / "observability" / "flow_debug.py"
    graph = core / "lifecycle" / "graph.py"

    assert not old_owner.exists(), "flow_debug must no longer be owned by generic utils"
    assert new_owner.is_file(), "observability must own the graph-node tracing wrapper"
    assert "from agent_core.observability.flow_debug import debug_node" in graph.read_text(encoding="utf-8")

    forbidden = []
    for path in (core / "utils").rglob("*.py"):
        for module in _agent_core_imports(path):
            if module.startswith("agent_core.lifecycle") or module.startswith("agent_core.storage"):
                forbidden.append(f"{path.relative_to(root)} -> {module}")
    assert forbidden == []

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
        payload, removed_member="utils", maximum_current_members=13
    )
    assert all("utils" not in cycle for cycle in debt["current_cycles"])
