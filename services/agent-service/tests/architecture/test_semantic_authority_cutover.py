from __future__ import annotations

import ast
from pathlib import Path

from tests.support.paths import workspace_root


_RETIRED_STATE_KEYS = {"turn_goal_plan", "workflow_plan", "pending_clarification"}
_ALLOWED_MIGRATION_PATH = Path("lifecycle/state_schema.py")


def _retired_state_accesses(path: Path) -> list[tuple[int, str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"get", "pop", "setdefault"} and node.args:
                key = node.args[0]
                if isinstance(key, ast.Constant) and key.value in _RETIRED_STATE_KEYS:
                    findings.append((node.lineno, str(key.value), node.func.attr))
        elif isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value in _RETIRED_STATE_KEYS:
                findings.append((node.lineno, str(key.value), "subscript"))
        elif isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value in _RETIRED_STATE_KEYS:
                    findings.append((node.lineno, str(key.value), "dict-write"))
    return findings


def test_retired_semantic_state_access_is_quarantined_to_checkpoint_migrator() -> None:
    root = workspace_root(__file__)
    core = root / "services/agent-service/src/agent_core"
    violations: list[str] = []
    for path in core.rglob("*.py"):
        relative = path.relative_to(core)
        findings = _retired_state_accesses(path)
        if findings and relative != _ALLOWED_MIGRATION_PATH:
            violations.extend(
                f"{relative}:{line}:{mode}:{key}"
                for line, key, mode in findings
            )
    assert violations == []


def test_retired_fallback_api_is_absent_from_current_runtime() -> None:
    root = workspace_root(__file__)
    core = root / "services/agent-service/src/agent_core"
    forbidden_symbols = {
        "legacy_fallback_allowed",
        "legacy_turn_goal_plan_from_contract",
        "active_pending_clarification",
        "suspend_for_clarification",
        "transition_after_goal_declaration",
    }
    violations: list[str] = []
    for path in core.rglob("*.py"):
        if path.relative_to(core) == _ALLOWED_MIGRATION_PATH:
            continue
        source = path.read_text(encoding="utf-8")
        for symbol in forbidden_symbols:
            if symbol in source:
                violations.append(f"{path.relative_to(core)}:{symbol}")
    assert violations == []


def test_checkpoint_migrator_tombstones_every_retired_field() -> None:
    root = workspace_root(__file__)
    source = (
        root
        / "services/agent-service/src/agent_core/lifecycle/state_schema.py"
    ).read_text(encoding="utf-8")
    assert "for key in RETIRED_TOP_LEVEL_FIELDS:" in source
    assert "source[key] = None" in source
    assert set(_RETIRED_STATE_KEYS) == {"turn_goal_plan", "workflow_plan", "pending_clarification"}
    assert "turn_goal_plan->frozen_semantic_contract" in source
    assert "pending_clarification->goal_blockers" in source
    assert "LegacyStateRestartRequired" in source


def test_prepare_turn_migrates_checkpoint_before_current_state_projection() -> None:
    root = workspace_root(__file__)
    source = (
        root
        / "services/agent-service/src/agent_core/lifecycle/context_runtime.py"
    ).read_text(encoding="utf-8")
    migration = source.index("migrate_checkpoint_state(state)")
    first_current_turn_read = source.index("current_turn =")
    assert migration < first_current_turn_read
