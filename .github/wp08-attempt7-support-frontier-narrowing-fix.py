#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path("candidate").resolve()
POLICY = ROOT / "services/agent-service/src/agent_core/lifecycle/pretool_execution_policy.py"
ROOT_TEST = ROOT / "skill-system/tests/test_wp08_attempt7_root_fixes.py"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:180]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    POLICY,
    '''        action_completion_pending = bool(
            not all_active_complete
            and any(
                (contract := capability_registry.contract_for_tool(name)) is not None
                and str(contract.execution_kind or "") == "action_draft"
                for name in active_completion_tools
            )
        )
''',
    '''        # Optional read-support is only exposed when the exact effect has
        # one contract-closed completion path and that path is currently direct.
        # If the registry already declares alternate closed paths (for example
        # eligibility -> draft), their own topology remains the sole frontier;
        # support_effects must not widen it.  This preserves the contract-v2
        # "highest-progress paths only" invariant while still permitting a
        # single direct action path to resolve a verified target through an
        # exact registered safe read.
        action_completion_pending = bool(
            not all_active_complete
            and len(closed_paths) == 1
            and len(active_paths) == 1
            and set(frontier) == active_completion_tools
            and any(
                (contract := capability_registry.contract_for_tool(name)) is not None
                and str(contract.execution_kind or "") == "action_draft"
                for name in active_completion_tools
            )
        )
''',
)

replace_once(
    ROOT_TEST,
    '''    assert 'str(contract.execution_kind or "") == "action_draft"' in policy
    assert '"support_frontier_tools": support_frontier' in policy
''',
    '''    assert 'str(contract.execution_kind or "") == "action_draft"' in policy
    assert 'len(closed_paths) == 1' in policy
    assert 'len(active_paths) == 1' in policy
    assert 'set(frontier) == active_completion_tools' in policy
    assert '"support_frontier_tools": support_frontier' in policy
''',
)

print("attempt7 support frontier narrowed to one direct action path")
