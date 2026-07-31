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


def test_ledger_exits_main_scc_through_neutral_draft_contract():
    root = workspace_root(__file__)
    core = root / "services" / "agent-service" / "src" / "agent_core"
    neutral_owner = core / "operations" / "draft.py"
    compatibility = core / "transaction" / "model.py"
    ledger = core / "ledger" / "ledger.py"

    assert neutral_owner.is_file(), "operations must own the pure TransactionDraft contract"
    assert "from agent_core.operations.draft import" in compatibility.read_text(encoding="utf-8")
    assert "from agent_core.operations.draft import" in ledger.read_text(encoding="utf-8")

    forbidden: list[str] = []
    for path in (core / "ledger").rglob("*.py"):
        for module in _agent_core_imports(path):
            if module == "agent_core.transaction" or module.startswith("agent_core.transaction."):
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
        payload, removed_member="ledger", maximum_current_members=11
    )
    assert all("ledger" not in cycle for cycle in debt["current_cycles"])
    assert all("rag" not in cycle and "utils" not in cycle for cycle in debt["current_cycles"])
