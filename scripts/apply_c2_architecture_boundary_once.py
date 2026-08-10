from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CAPABILITY = ROOT / "services/agent-service/src/agent_core/runtime/capability_gate.py"
TEST = ROOT / "skill-system/tests/test_c2_strong_context_scope_repair.py"
BASELINE = ROOT / "skill-system/registry/product-source-baseline.json"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


capability = CAPABILITY.read_text(encoding="utf-8")
capability = replace_once(
    capability,
    "from agent_core.lifecycle.condition_expression import condition_operands\n",
    "",
    label="remove runtime-to-lifecycle condition import",
)

walker = '''\n\ndef _frozen_condition_operands(expression: dict[str, Any]) -> list[dict[str, Any]]:\n    """Read operands from an already-normalized frozen Goal condition.\n\n    Lifecycle owns condition parsing, normalization and semantic validity.\n    Runtime must not import Lifecycle merely to inspect that immutable contract,\n    because doing so recreates the resolved context/lifecycle/runtime cycle.\n    This helper therefore performs no normalization, aliasing or inference: it\n    only projects operand objects that are already present in the frozen tree.\n    """\n    if not isinstance(expression, dict):\n        return []\n    op = str(expression.get("op") or "")\n    if op in {"and", "or", "not"}:\n        return [\n            operand\n            for child in list(expression.get("args") or [])\n            if isinstance(child, dict)\n            for operand in _frozen_condition_operands(child)\n        ]\n    return [\n        dict(expression[key])\n        for key in ("left", "right", "lower", "upper")\n        if isinstance(expression.get(key), dict)\n    ]\n'''
capability = replace_once(
    capability,
    "\ndef _formal_goal_condition_coverage_proof(\n",
    walker + "\n\ndef _formal_goal_condition_coverage_proof(\n",
    label="insert neutral frozen condition walker",
)
capability = replace_once(
    capability,
    "        for operand in condition_operands(condition):\n",
    "        for operand in _frozen_condition_operands(condition):\n",
    label="use neutral frozen condition walker",
)
CAPABILITY.write_text(capability, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
anchor = '''    assert "formal_goal_condition_coverage" in capability_source\n    assert "condition_operands" in capability_source\n    assert "required_condition_execution_evidence_missing" in answer_source\n'''
replacement = '''    assert "formal_goal_condition_coverage" in capability_source\n    assert "_frozen_condition_operands" in capability_source\n    assert "from agent_core.lifecycle.condition_expression" not in capability_source\n    assert "required_condition_execution_evidence_missing" in answer_source\n'''
test = replace_once(test, anchor, replacement, label="C2 architecture regression assertion")
TEST.write_text(test, encoding="utf-8")

payload = json.loads(BASELINE.read_text(encoding="utf-8"))
roots = [str(value) for value in payload.get("protected_roots") or ()]
raw = subprocess.check_output(["git", "ls-files", "-z", "--", *roots], cwd=ROOT)
tracked = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
payload["generated_from"] = "git:" + subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
payload["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
payload["file_count"] = len(tracked)
payload["files"] = {
    relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    for relative in tracked
}
BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

print(json.dumps({
    "status": "PATCHED",
    "runtime_to_lifecycle_import_removed": True,
    "files": [
        str(CAPABILITY.relative_to(ROOT)),
        str(TEST.relative_to(ROOT)),
        str(BASELINE.relative_to(ROOT)),
    ],
}, ensure_ascii=False))
