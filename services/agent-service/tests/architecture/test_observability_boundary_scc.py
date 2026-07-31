from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
from pathlib import Path

from tests.support.paths import workspace_root
from tests.support.dependency_debt import assert_dependency_debt_monotonic


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agent_core"):
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names if alias.name.startswith("agent_core"))
    return modules


def test_observability_is_dependency_neutral_and_exits_main_scc():
    root = workspace_root(__file__)
    service = root / "services" / "agent-service"
    core = service / "src" / "agent_core"
    observability = core / "observability"

    forbidden: list[str] = []
    for path in observability.rglob("*.py"):
        for module in _imports(path):
            if any(module == f"agent_core.{name}" or module.startswith(f"agent_core.{name}.") for name in ("lifecycle", "persistence")):
                forbidden.append(f"{path.relative_to(root)} -> {module}")
    assert forbidden == []

    trace_module = observability / "trace_logger.py"
    text = trace_module.read_text(encoding="utf-8")
    assert "class TraceLogger(SQLiteBase)" not in text
    assert (core / "persistence" / "trace_store.py").exists()

    sys.path.insert(0, str(service / "src"))
    try:
        from agent_core.observability.flow_debug import debug_node
        signature = inspect.signature(debug_node)
        assert "state_validator" in signature.parameters
        assert "trace_repository" in signature.parameters
        assert signature.parameters["state_validator"].default is inspect.Parameter.empty

        from agent_core.lifecycle.graph import build_lifecycle_graph
        graph_source = inspect.getsource(build_lifecycle_graph)
        assert "validate_state_update" in graph_source
        assert "runtime_deps.trace_logger" in graph_source
    finally:
        sys.path.pop(0)

    completed = subprocess.run(
        [sys.executable, "-B", str(root / "architecture-skill" / "scripts" / "verify_convergence.py"), "--workspace-root", str(root)],
        cwd=root, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    debt = payload["checks"]["dependency_cycle_debt"]
    assert debt["baseline_member_count"] == 14
    assert_dependency_debt_monotonic(
        payload, removed_member="observability", maximum_current_members=5
    )
    assert all("observability" not in cycle for cycle in debt["current_cycles"])
    removed = ("storage", "context", "modules", "kernel", "resources", "ledger", "rag", "utils")
    assert all(all(member not in cycle for member in removed) for cycle in debt["current_cycles"])
