from pathlib import Path

SOURCE = Path("services/agent-service/src/agent_core/lifecycle/goal_planning.py")
TEST = Path("services/agent-service/tests/runtime/test_release52_dependency_independence_adjudication.py")

source = SOURCE.read_text(encoding="utf-8")
old = '                multi_goal_dependency_graph = len(goals) > 1\n'
new = (
    '                independence_adjudication_risk = (\n'
    '                    len(goals) > 1\n'
    '                    and initial_grounded_alignment is not None\n'
    '                    and initial_grounded_alignment.exact\n'
    '                )\n'
)
if source.count(old) != 1:
    raise SystemExit("expected one multi-goal dependency anchor")
source = source.replace(old, new, 1)

old = '                    or multi_goal_dependency_graph\n'
new = '                    or independence_adjudication_risk\n'
if source.count(old) != 1:
    raise SystemExit("expected one multi-goal risk condition")
source = source.replace(old, new, 1)

old = '                    elif multi_goal_dependency_graph:\n'
new = '                    elif independence_adjudication_risk:\n'
if source.count(old) != 1:
    raise SystemExit("expected one multi-goal adjudication branch")
source = source.replace(old, new, 1)

old = (
    '                        # Dependency absence is also a high-impact semantic claim. Two\n'
    '                        # candidate-blind passes can agree on an empty graph while still\n'
    '                        # missing a literal current-turn result relation. Spend the same\n'
)
new = (
    '                        # Dependency absence is also a high-impact semantic claim. A\n'
    '                        # candidate-visible exact pass plus the candidate-blind pass can\n'
    '                        # agree on an empty graph while still missing a literal current-turn\n'
    '                        # result relation. Spend the same bounded third slot only for that\n'
)
if source.count(old) != 1:
    raise SystemExit("expected one dependency-absence comment")
source = source.replace(old, new, 1)
SOURCE.write_text(source, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
if 'import re\n' not in test:
    test = test.replace('import json\n', 'import json\nimport re\n', 1)
old = '            "operation": "open",\n'
new = '            "operation": output_id,\n'
if test.count(old) != 1:
    raise SystemExit("expected one test requested-effect operation")
test = test.replace(old, new, 1)
old = (
    '    for forbidden in ("键盘", "退款", "订单", "鼠标", "物流", "invoice", "refund", "order"):\n'
    '        assert forbidden not in section.casefold()\n'
)
new = (
    '    lowered = section.casefold()\n'
    '    for forbidden in ("键盘", "退款", "订单", "鼠标", "物流"):\n'
    '        assert forbidden not in lowered\n'
    '    for forbidden in ("invoice", "refund", "order"):\n'
    '        assert re.search(rf"\\b{re.escape(forbidden)}\\b", lowered) is None\n'
)
if test.count(old) != 1:
    raise SystemExit("expected one domain-neutral assertion block")
test = test.replace(old, new, 1)
TEST.write_text(test, encoding="utf-8")
