from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys

from agent_core.composition import get_runtime_registry
from agent_core.resources.targets import TargetResolver
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


def test_resources_exit_main_scc_through_explicit_registry_injection():
    root = workspace_root(__file__)
    core = root / "services" / "agent-service" / "src" / "agent_core"

    forbidden: list[str] = []
    for path in (core / "resources").rglob("*.py"):
        for module in _agent_core_imports(path):
            if module == "agent_core.modules" or module.startswith("agent_core.modules."):
                forbidden.append(f"{path.relative_to(root)} -> {module}")
    assert forbidden == []

    parameter = inspect.signature(TargetResolver.__init__).parameters["resource_registry"]
    assert parameter.default is inspect.Parameter.empty

    resolver = TargetResolver(get_runtime_registry().resources)
    target_set = resolver.from_verified_members(
        resource_type="order",
        handles=["artifact:order:10002"],
        source="verified_test",
        evidence_handles=["artifact:order:10002"],
        resolution_basis="explicit_handle",
        resolved_at_turn=1,
    )
    assert target_set.resource_type == "order"
    assert target_set.handles == ("artifact:order:10002",)

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
        payload, removed_member="resources", maximum_current_members=10
    )
    assert all("resources" not in cycle for cycle in debt["current_cycles"])
    assert all("ledger" not in cycle and "rag" not in cycle and "utils" not in cycle for cycle in debt["current_cycles"])
