#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts/github_repair_stage3.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")

    text = replace_once(
        text,
        'MAX_OUTPUT_CHARS = 40_000\n',
        'MAX_OUTPUT_CHARS = 40_000\nANTI_DRIFT_REQUIRED_GATE_ID = "python-test-suites"\n',
        "anti-drift gate constant",
    )

    anchor = '''def record_validation(\n'''
    helper = '''def _require_anti_drift_proof(summary: dict[str, Any]) -> dict[str, str]:\n    """Require the permanent mutation-kill proof to be mandatory and PASS in Quick.\n\n    The python-test-suites gate executes\n    tests/architecture/test_governed_repair_mutation_proof.py, which deliberately\n    drifts a secondary lifecycle projection and requires the architecture verifier\n    to turn RED. Removing that gate from Quick is therefore itself fail-closed.\n    """\n\n    required = {str(item) for item in summary.get("required_gate_ids") or []}\n    statuses = {\n        str(row.get("id")): str(row.get("status"))\n        for row in summary.get("results") or []\n        if isinstance(row, dict)\n    }\n    gate = ANTI_DRIFT_REQUIRED_GATE_ID\n    if gate not in required:\n        raise Stage3Error(\n            f"anti_drift_proof_not_reverified: required Quick gate missing: {gate}"\n        )\n    if statuses.get(gate) != "PASS":\n        raise Stage3Error(\n            f"anti_drift_proof_not_reverified: Quick gate did not PASS: {gate}"\n        )\n    return {gate: "PASS"}\n\n\n'''
    text = replace_once(text, anchor, helper + anchor, "anti-drift helper")

    old = '''    guard_proof = _require_permanent_guard_reverified(\n        summary,\n        plan.get("required_guard_ids") or [],\n    )\n    workspace = workspace.resolve()\n'''
    new = '''    guard_proof = _require_permanent_guard_reverified(\n        summary,\n        plan.get("required_guard_ids") or [],\n    )\n    anti_drift_proof = _require_anti_drift_proof(summary)\n    workspace = workspace.resolve()\n'''
    text = replace_once(text, old, new, "anti-drift invocation")

    old = '''            "G3_MUTATION": {\n                "status": "PASS",\n                "evidence": [str(quick_summary_path), f"quick-snapshot:{snapshot}"],\n            },\n'''
    new = '''            "G3_MUTATION": {\n                "status": "PASS",\n                "governed_repair_state": "ANTI_DRIFT_PROOF",\n                "evidence": [\n                    str(quick_summary_path),\n                    f"quick-snapshot:{snapshot}",\n                    *[f"anti-drift-gate:{gate}:PASS" for gate in anti_drift_proof],\n                ],\n            },\n'''
    text = replace_once(text, old, new, "G3 mutation proof")

    old = '''            "write_grant_sha256": plan.get("write_grant_sha256"),\n        },\n    )\n    result = {\n'''
    new = '''            "write_grant_sha256": plan.get("write_grant_sha256"),\n            "anti_drift_proof": anti_drift_proof,\n        },\n    )\n    result = {\n'''
    text = replace_once(text, old, new, "TaskRun anti-drift evidence")

    old = '''        "permanent_guard_reverification": guard_proof,\n        "violated_invariant": plan.get("violated_invariant"),\n'''
    new = '''        "permanent_guard_reverification": guard_proof,\n        "anti_drift_proof": {\n            "governed_repair_state": "ANTI_DRIFT_PROOF",\n            "required_gate_id": ANTI_DRIFT_REQUIRED_GATE_ID,\n            "gate_statuses": anti_drift_proof,\n            "status": "PASS",\n        },\n        "violated_invariant": plan.get("violated_invariant"),\n'''
    text = replace_once(text, old, new, "validation anti-drift receipt")

    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
