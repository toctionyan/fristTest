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


def test_context_exits_main_scc_with_canonical_read_only_state_projections():
    root = workspace_root(__file__)
    service = root / "services" / "agent-service"
    core = service / "src" / "agent_core"

    forbidden: list[str] = []
    for path in (core / "context").rglob("*.py"):
        for module in _agent_core_imports(path):
            if (
                module == "agent_core.lifecycle"
                or module.startswith("agent_core.lifecycle.")
                or module == "agent_core.storage"
                or module.startswith("agent_core.storage.")
            ):
                forbidden.append(f"{path.relative_to(root)} -> {module}")
    assert forbidden == []

    sys.path.insert(0, str(service / "src"))
    try:
        from agent_core.context.state_projection import (
            active_goal_blockers as context_blockers,
            clarification_context_projection as context_clarification,
            goal_records_context_projection as context_goals,
        )
        from agent_core.lifecycle.goal_blockers import active_goal_blockers as lifecycle_blockers
        from agent_core.lifecycle.clarification_runtime import (
            clarification_context_projection as lifecycle_clarification,
        )
        from agent_core.lifecycle.goal_lifecycle import goal_records_context_projection as lifecycle_goals
    finally:
        sys.path.pop(0)

    assert lifecycle_blockers is context_blockers
    assert lifecycle_clarification is context_clarification
    assert lifecycle_goals is context_goals
    assert not hasattr(__import__("agent_core.context.state_projection", fromlist=["active_pending_clarification"]), "active_pending_clarification")

    state = {
        "state_schema_version": 2,
        "goal_blockers": [{"blocker_id": "b1", "goal_id": "g1", "status": "OPEN", "question": "哪一个？"}],
        "goal_records": [{"goal_id": "g1", "description": "查询", "lifecycle": "ACTIVE", "revision": 2, "updated_turn": 3}],
    }
    assert context_blockers(state)[0]["blocker_id"] == "b1"
    assert context_goals(state)[0]["revision"] == 2
    assert context_clarification(state)["version"] == "goal-blocker-projection@1"

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
        payload, removed_member="context", maximum_current_members=7
    )
    assert all("context" not in cycle for cycle in debt["current_cycles"])
    assert all(
        all(member not in cycle for member in ("modules", "kernel", "resources", "ledger", "rag", "utils"))
        for cycle in debt["current_cycles"]
    )
