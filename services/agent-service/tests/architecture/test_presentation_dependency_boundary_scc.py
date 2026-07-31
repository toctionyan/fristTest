from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.paths import workspace_root
from tests.support.dependency_debt import assert_dependency_debt_monotonic


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agent_core"):
            result.add(node.module)
        elif isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names if alias.name.startswith("agent_core"))
    return result


def test_presentation_is_dependency_neutral_and_exits_main_scc() -> None:
    root = workspace_root(__file__)
    service = root / "services/agent-service"
    core = service / "src/agent_core"
    presentation = core / "presentation"

    forbidden: list[str] = []
    for path in presentation.rglob("*.py"):
        for module in _imports(path):
            if any(module == f"agent_core.{name}" or module.startswith(f"agent_core.{name}.") for name in ("lifecycle", "runtime", "transaction")):
                forbidden.append(f"{path.relative_to(root)} -> {module}")
    assert forbidden == []
    assert (core / "kernel/outcome_contract.py").is_file()

    sys.path.insert(0, str(service / "src"))
    try:
        from agent_core.presentation.actions import validate_catalog_integrity
        from agent_core.presentation.outcome import presentation_from_outcome
        from agent_core.runtime.outcomes import outcome

        row = outcome(
            "transaction_status",
            customer_safe_summary="已查询办理状态。",
            correlation_id="trace-1",
            payload={"status": "COMMITTED"},
        )
        projected_object = presentation_from_outcome(row)
        projected_dict = presentation_from_outcome(row.as_dict())
        assert projected_object == projected_dict
        assert projected_object is not None and projected_object.mode == "transaction_status"

        invalid = presentation_from_outcome({"outcome_type": "invented", "customer_safe_summary": "伪造成功"})
        assert invalid is not None and invalid.mode == "notice"
        assert "未确认创建或提交" in str(invalid.summary)

        validate_catalog_integrity(
            action_ids={"create_refund"},
            gateway_policy_ids={"create_refund"},
            commit_dispatcher_ids={"create_refund"},
        )
        with pytest.raises(RuntimeError):
            validate_catalog_integrity(
                action_ids={"create_refund"},
                gateway_policy_ids=set(),
                commit_dispatcher_ids={"create_refund"},
            )
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
    assert_dependency_debt_monotonic(
        payload, removed_member="presentation", maximum_current_members=3
    )
    assert all("presentation" not in cycle for cycle in debt["current_cycles"])
    prior = ("persistence", "observability", "storage", "context", "modules", "kernel", "resources", "ledger", "rag", "utils")
    assert all(all(name not in cycle for name in prior) for cycle in debt["current_cycles"])
