from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


fixture = Path("services/agent-service/tests/context/test_scenario_topology.py")
replace_once(
    fixture,
    '''                            "requested_effect": {
                                "domain": "conversation",
                                "operation": "continue_request",
                                "object_type": "goal",
                                "raw_description": text,
                            },
''',
    '''                            "requested_effect": {
                                "domain": "conversation",
                                "operation": "continue_request",
                                "object_type": "goal",
                                "raw_description": text,
                                "requested_outputs": [{
                                    "output_id": "open",
                                    "evidence_span": text,
                                    "open_description": text,
                                }],
                            },
''',
)

# Report other statically-declared scripted planner calls that still contain a
# requested_effect without requested_outputs. This is diagnostic only because
# some negative tests intentionally exercise the rejection contract.
def const_str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def dict_value(node: ast.Dict, key: str) -> ast.AST | None:
    for key_node, value_node in zip(node.keys, node.values):
        if const_str(key_node) == key:
            return value_node
    return None


legacy_sites: list[str] = []
for py in Path("services/agent-service/tests").rglob("*.py"):
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        if const_str(dict_value(node, "name")) != "declare_turn_goals":
            continue
        args = dict_value(node, "args")
        if not isinstance(args, ast.Dict):
            continue
        goals = dict_value(args, "goals")
        if not isinstance(goals, ast.List):
            continue
        for goal in goals.elts:
            if not isinstance(goal, ast.Dict):
                continue
            effect = dict_value(goal, "requested_effect")
            if isinstance(effect, ast.Dict) and dict_value(effect, "requested_outputs") is None:
                legacy_sites.append(f"{py}:{getattr(effect, 'lineno', '?')}")
print("static legacy declare_turn_goals fixtures (diagnostic):", legacy_sites)

baseline_path = Path("skill-system/registry/product-source-baseline.json")
baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
files = baseline.get("files")
if not isinstance(files, dict):
    raise SystemExit("product source baseline files map is invalid")
key = fixture.as_posix()
if key not in files:
    raise SystemExit(f"protected fixture missing from source baseline: {key}")
files[key] = hashlib.sha256(fixture.read_bytes()).hexdigest()
baseline["file_count"] = len(files)
baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("updated baseline", key, files[key])
