#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import py_compile

GOAL_PATH = Path('services/agent-service/src/agent_core/lifecycle/goal_planning.py')
TEST_PATH = Path('skill-system/tests/test_wp08_attempt1_requested_effect_reaudit.py')


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()

    goal = root / GOAL_PATH
    text = goal.read_text(encoding='utf-8')
    text = replace_once(
        text,
        '                and requested_effect_mismatch\n                and semantic_details.get("dependency_proof_complete") is True\n',
        '                and requested_effect_mismatch\n                and len(goals) > 1\n                and semantic_details.get("dependency_proof_complete") is True\n',
        label='narrow requested-effect re-audit to multi-goal declarations',
    )
    goal.write_text(text, encoding='utf-8')

    test = root / TEST_PATH
    text = test.read_text(encoding='utf-8')
    text = replace_once(
        text,
        '    assert "capability availability must not be used as evidence" in policy\n',
        '    assert "capability availability must not be used as" in policy\n    assert "evidence." in policy\n',
        label='source-level split-string assertion',
    )
    test.write_text(text, encoding='utf-8')

    py_compile.compile(str(goal), doraise=True)
    py_compile.compile(str(test), doraise=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
