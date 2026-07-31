from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from tests.support.paths import workspace_root


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agent_core"):
            result.add(node.module)
        elif isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names if alias.name.startswith("agent_core"))
    return result


def test_runtime_is_lifecycle_neutral_and_dependency_cycle_debt_is_resolved() -> None:
    root = workspace_root(__file__)
    service = root / "services/agent-service"
    core = service / "src/agent_core"
    runtime = core / "runtime"

    forbidden: list[str] = []
    for path in runtime.rglob("*.py"):
        for module in _imports(path):
            if module == "agent_core.lifecycle" or module.startswith("agent_core.lifecycle."):
                forbidden.append(f"{path.relative_to(root)} -> {module}")
    assert forbidden == []

    expected = (
        core / "kernel/loop_contract.py",
        core / "kernel/semantic_contract.py",
        core / "kernel/state_schema_contract.py",
    )
    assert all(path.is_file() for path in expected)

    sys.path.insert(0, str(service / "src"))
    try:
        from agent_core.kernel.loop_contract import (
            MAX_AGENT_LOOP_STEPS_DEFAULT as kernel_loop_steps,
            MAX_SAME_CALLS_PER_TURN_DEFAULT as kernel_same_calls,
        )
        from agent_core.kernel.semantic_contract import semantic_goals as kernel_semantic_goals
        from agent_core.kernel.state_schema_contract import legacy_fallback_allowed as kernel_legacy
        from agent_core.lifecycle.protocol import (
            MAX_AGENT_LOOP_STEPS_DEFAULT as lifecycle_loop_steps,
            MAX_SAME_CALLS_PER_TURN_DEFAULT as lifecycle_same_calls,
        )
        from agent_core.lifecycle.semantic_contract import (
            freeze_semantic_contract,
            semantic_goals as lifecycle_semantic_goals,
        )
        from agent_core.lifecycle.state_schema import legacy_fallback_allowed as lifecycle_legacy

        assert lifecycle_loop_steps == kernel_loop_steps == 6
        assert lifecycle_same_calls == kernel_same_calls == 1
        assert lifecycle_semantic_goals is kernel_semantic_goals
        assert lifecycle_legacy is kernel_legacy

        contract = freeze_semantic_contract(
            turn=1,
            user_text="查订单",
            summary="查询订单",
            goals=[{
                "goal_id": "g1",
                "description": "查询订单",
                "evidence_span": "查订单",
                "requested_effect": {"domain": "order", "operation": "query", "object_type": "order"},
                "expected_result_cardinality": "collection",
                "required": True,
                "depends_on": [],
            }],
            alignment_proof={"verdict": "aligned"},
        )
        assert [row["goal_id"] for row in kernel_semantic_goals(contract)] == ["g1"]
        corrupted = dict(contract)
        corrupted["summary"] = "tampered"
        assert kernel_semantic_goals(corrupted) == []
        assert kernel_legacy({"state_schema_version": 1}) is True
        assert kernel_legacy({"state_schema_version": 2}) is False
    finally:
        sys.path.pop(0)

    completed = subprocess.run(
        [sys.executable, "-B", str(root / "architecture-skill/scripts/verify_convergence.py"), "--workspace-root", str(root)],
        cwd=root,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    debt = payload["checks"]["dependency_cycle_debt"]
    assert payload["architecture_status"] == "PASS"
    assert payload["architecture_debt_status"] == "RESOLVED"
    assert debt["current_cycles"] == []
    assert debt["current_member_count"] == 0
    assert debt["untracked_or_expanded_cycles"] == []
    assert any(row.get("id") == "agent-core-main-scc-v20.16" for row in debt["resolved_components"])
