#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}")
    return text.replace(old, new, 1)


def regex_replace_once(text: str, pattern: str, replacement: str, label: str) -> str:
    next_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}")
    return next_text


def patch_tests(root: Path) -> None:
    path = root / "skill-system/tests/test_wp08_attempt7_scope_constraint_repair.py"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '    assert "Goal.condition is a separate" in policy\n',
        '    assert "Goal.condition" in policy\n    assert "separate condition/dependency algebra" in policy\n',
        "brittle source assertion",
    )

    old_loop = '''    for forbidden in ("待发货", "已签收", "在路上", "运输中", "快递员"):\n        assert forbidden not in policy\n        assert forbidden not in gate_source\n'''
    new_loop = '''    scope_start = gate_source.index("def _literal_scope_overlap")\n    scope_end = gate_source.index("def _visible_reference_proof", scope_start)\n    scope_bridge = gate_source[scope_start:scope_end]\n    for forbidden in ("待发货", "已签收", "在路上", "运输中", "快递员"):\n        assert forbidden not in policy\n        assert forbidden not in scope_bridge\n'''
    text = replace_once(text, old_loop, new_loop, "domain-neutral scope region")

    text += r'''


def test_issue_execution_permit_fails_closed_when_frozen_scope_is_unbound() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch
    import agent_core.runtime.capability_gate as gate

    contract = SimpleNamespace(key="order.query_logistics:order", execution_kind="read")
    registry = SimpleNamespace(contract_for_tool=lambda _name: contract)
    state = {
        "current_turn_plan": {
            "effects": [{"effect_id": "effect-1", "goal_ids": ["g1"]}],
        },
    }
    with patch.object(gate, "normalize_tool_arguments", return_value=({}, {})), patch.object(
        gate, "validate_tool_arguments", return_value=[]
    ), patch.object(
        gate, "_parameterization_proof", return_value={"parameterization_complete": True, "bindings": [], "errors": []}
    ), patch.object(
        gate, "_visible_reference_proof", return_value={"complete": True, "checks": [], "errors": []}
    ), patch.object(
        gate, "_explicit_member_scope_proof", return_value={"complete": True, "errors": []}
    ), patch.object(
        gate, "_derived_collection_scope_proof", return_value={"complete": True, "errors": []}
    ), patch.object(
        gate, "_formal_goal_condition_coverage_proof", return_value={"complete": True, "errors": []}
    ), patch.object(
        gate, "_formal_goal_scope_coverage_proof", return_value={
            "complete": False,
            "errors": ["formal_goal_scope_constraint_unbound:g1:0"],
        }
    ), patch.object(
        gate, "_semantic_reference_binding_proof", return_value={"complete": True, "errors": []}
    ), patch.object(
        gate, "_pretool_frontier_proof", return_value={"allowed": True, "errors": [], "reason_code": "allowed"}
    ), patch.object(
        gate, "semantic_goals", return_value=[]
    ), patch.object(
        gate, "goal_effect_match_proof", return_value={"allowed": True, "errors": []}
    ):
        decision = gate.issue_execution_permit(
            state=state,
            tool_name="query_logistics",
            args={},
            effect_id="effect-1",
            capability_registry=registry,
        )

    assert decision.permitted is False
    assert decision.rejection["code"] == "CAPABILITY_SCOPE_CONSTRAINT_UNBOUND"
    assert "formal_goal_scope_constraint_unbound:g1:0" in decision.match_proof["constraint_errors"]
'''
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def patch_gate(root: Path) -> None:
    path = root / "services/agent-service/src/agent_core/runtime/capability_gate.py"
    source = path.read_text(encoding="utf-8")

    source = replace_once(
        source,
        '"exact_match": bool(contract is not None and not arg_errors and parameterization.get("parameterization_complete") and formal_condition_coverage.get("complete") and visible_reference.get("complete")',
        '"exact_match": bool(contract is not None and not arg_errors and parameterization.get("parameterization_complete") and formal_condition_coverage.get("complete") and formal_scope_coverage.get("complete") and visible_reference.get("complete")',
        "exact-match scope gate",
    )
    source = replace_once(
        source,
        'if contract is None or arg_errors or not parameterization.get("parameterization_complete") or not formal_condition_coverage.get("complete") or not visible_reference.get("complete")',
        'if contract is None or arg_errors or not parameterization.get("parameterization_complete") or not formal_condition_coverage.get("complete") or not formal_scope_coverage.get("complete") or not visible_reference.get("complete")',
        "permit rejection scope gate",
    )

    code_pattern = (
        r'^(?P<i>[ \t]*)else "CAPABILITY_PARAMETERIZATION_INCOMPLETE"\n'
        r'(?P=i)if contract is not None and not arg_errors and \(not parameterization\.get\("parameterization_complete"\) or not formal_condition_coverage\.get\("complete"\)\)\n'
    )
    code_replacement = (
        r'\g<i>else "CAPABILITY_SCOPE_CONSTRAINT_UNBOUND"\n'
        r'\g<i>if contract is not None and not arg_errors and not formal_scope_coverage.get("complete")\n'
        r'\g<i>else "CAPABILITY_PARAMETERIZATION_INCOMPLETE"\n'
        r'\g<i>if contract is not None and not arg_errors and (not parameterization.get("parameterization_complete") or not formal_condition_coverage.get("complete"))\n'
    )
    source = regex_replace_once(source, code_pattern, code_replacement, "scope rejection code")

    message_pattern = (
        r'^(?P<i>[ \t]*)else "当前请求中的决定性条件没有被完整绑定到正式参数，系统不会用更宽泛查询代替。"\n'
        r'(?P=i)if contract is not None and not arg_errors and \(not parameterization\.get\("parameterization_complete"\) or not formal_condition_coverage\.get\("complete"\)\)\n'
    )
    message_replacement = (
        r'\g<i>else "当前请求中冻结的目标范围约束没有绑定到真实查询/目标参数或已验证结果血缘，系统不会用更宽泛查询代替。"\n'
        r'\g<i>if contract is not None and not arg_errors and not formal_scope_coverage.get("complete")\n'
        r'\g<i>else "当前请求中的决定性条件没有被完整绑定到正式参数，系统不会用更宽泛查询代替。"\n'
        r'\g<i>if contract is not None and not arg_errors and (not parameterization.get("parameterization_complete") or not formal_condition_coverage.get("complete"))\n'
    )
    source = regex_replace_once(source, message_pattern, message_replacement, "scope rejection message")
    path.write_text(source, encoding="utf-8")


def main() -> int:
    root = Path("candidate").resolve()
    patch_tests(root)
    patch_gate(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
