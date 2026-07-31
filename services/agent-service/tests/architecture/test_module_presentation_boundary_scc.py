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


def test_modules_exit_main_scc_when_presentation_registry_is_composed_outside_modules():
    root = workspace_root(__file__)
    service = root / "services" / "agent-service"
    core = service / "src" / "agent_core"

    forbidden: list[str] = []
    for path in (core / "modules").rglob("*.py"):
        for module in _agent_core_imports(path):
            if module == "agent_core.presentation" or module.startswith("agent_core.presentation."):
                forbidden.append(f"{path.relative_to(root)} -> {module}")
    assert forbidden == []

    module_registry_source = (core / "modules" / "registry.py").read_text(encoding="utf-8")
    composition_source = (core / "composition" / "registry.py").read_text(encoding="utf-8")
    adapter_source = (core / "presentation" / "adapters.py").read_text(encoding="utf-8")
    assert "def presentation_adapters(" in module_registry_source
    assert "build_presentation_registry" not in module_registry_source
    assert "PresentationRegistry(registry.presentation_adapters())" in composition_source
    assert "from agent_core.modules.contracts import PresentationAdapter" in adapter_source

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
        payload, removed_member="modules", maximum_current_members=8
    )
    assert all("modules" not in cycle for cycle in debt["current_cycles"])
    assert all(
        all(member not in cycle for member in ("kernel", "resources", "ledger", "rag", "utils"))
        for cycle in debt["current_cycles"]
    )
