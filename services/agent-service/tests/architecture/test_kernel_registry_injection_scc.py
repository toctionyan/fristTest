from __future__ import annotations

import ast
import inspect
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


def test_kernel_exits_main_scc_through_explicit_runtime_registry_injection():
    root = workspace_root(__file__)
    service = root / "services" / "agent-service"
    core = service / "src" / "agent_core"
    kernel_integrity = core / "kernel" / "integrity.py"
    app_main = service / "app" / "main.py"

    forbidden: list[str] = []
    for path in (core / "kernel").rglob("*.py"):
        for module in _agent_core_imports(path):
            if module == "agent_core.modules" or module.startswith("agent_core.modules."):
                forbidden.append(f"{path.relative_to(root)} -> {module}")
    assert forbidden == []

    sys.path.insert(0, str(service / "src"))
    try:
        from agent_core.kernel import validate_runtime_architecture as public_validate
        from agent_core.kernel.integrity import validate_runtime_architecture as implementation_validate
    finally:
        sys.path.pop(0)
    for function in (implementation_validate, public_validate):
        parameter = inspect.signature(function).parameters["registry"]
        assert parameter.default is inspect.Parameter.empty

    app_source = app_main.read_text(encoding="utf-8")
    assert "from agent_core.composition import get_runtime_registry" in app_source
    assert "validate_runtime_architecture(get_runtime_registry())" in app_source
    assert "current_runtime_registry" not in kernel_integrity.read_text(encoding="utf-8")

    completed = subprocess.run([sys.executable,"-B",str(root / "architecture-skill" / "scripts" / "verify_convergence.py"),"--workspace-root",str(root)],cwd=root,text=True,capture_output=True,check=False)
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    debt = payload["checks"]["dependency_cycle_debt"]
    assert debt["baseline_member_count"] == 14
    assert_dependency_debt_monotonic(
        payload, removed_member="kernel", maximum_current_members=9
    )
    assert all("kernel" not in cycle for cycle in debt["current_cycles"])
    assert all(all(member not in cycle for member in ("resources","ledger","rag","utils")) for cycle in debt["current_cycles"])
