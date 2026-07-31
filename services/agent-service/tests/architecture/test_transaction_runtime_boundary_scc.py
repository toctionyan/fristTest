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
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agent_core"):
            result.add(node.module)
        elif isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names if alias.name.startswith("agent_core"))
    return result


def test_transaction_consumes_explicit_execution_dependencies_and_exits_main_scc() -> None:
    root = workspace_root(__file__)
    service = root / "services/agent-service"
    core = service / "src/agent_core"
    transaction = core / "transaction"

    forbidden: list[str] = []
    hidden_port_lookups: list[str] = []
    for path in transaction.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for module in _imports(path):
            if module == "agent_core.runtime" or module.startswith("agent_core.runtime."):
                forbidden.append(f"{path.relative_to(root)} -> {module}")
        if "get_business_port(" in text:
            hidden_port_lookups.append(str(path.relative_to(root)))
    assert forbidden == []
    assert hidden_port_lookups == []
    assert (transaction / "deps.py").is_file()
    assert (core / "kernel/decision_trace.py").is_file()

    sys.path.insert(0, str(service / "src"))
    try:
        from agent_core.transaction.deps import TransactionExecutionDeps
        from agent_core.transaction.gateway_runtime import action_gateway_node
        from agent_core.transaction.commit_runtime import commit_action_node
        from agent_core.transaction.interaction_runtime import action_confirmation_node
        from agent_core.transaction.availability import check_transaction_repository_available
        from agent_core.transaction.lifecycle_query import TransactionLifecycleQuery
        from agent_core.transaction.operation_preparation import OperationPreparationRuntime

        for callable_ in (action_gateway_node, commit_action_node, action_confirmation_node):
            assert "deps" in inspect.signature(callable_).parameters
        assert "outcome_factory" in inspect.signature(check_transaction_repository_available).parameters
        assert "outcome_factory" in inspect.signature(TransactionLifecycleQuery).parameters
        assert "outcome_factory" in inspect.signature(OperationPreparationRuntime).parameters
        assert set(TransactionExecutionDeps.__dataclass_fields__) == {"business_port", "outcome_factory"}
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
        payload, removed_member="transaction", maximum_current_members=2
    )
    assert all("transaction" not in cycle for cycle in debt["current_cycles"])
    prior = (
        "presentation", "persistence", "observability", "storage", "context", "modules",
        "kernel", "resources", "ledger", "rag", "utils",
    )
    assert all(all(name not in cycle for name in prior) for cycle in debt["current_cycles"])
