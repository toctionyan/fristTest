#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "governance/quality-loop-policy.json"
QUALITY_TEST = ROOT / "services/agent-service/tests/architecture/test_quality_loop_governance.py"
SYSTEMIC_TEST = ROOT / "services/agent-service/tests/architecture/test_systemic_operational_closure.py"
AUTHORITY_TEST = ROOT / "services/agent-service/tests/runtime/test_b17a_production_certification_authority.py"
MANIFEST = ROOT / "PHASE_CANDIDATE_MANIFEST.json"
SELF = Path(__file__).resolve()
RETIRED_IDS = {
    "configured-model-browser-conversation",
    "configured-model-browser-campaign",
}


def retire_policy_steps() -> None:
    payload = json.loads(POLICY.read_text(encoding="utf-8"))
    steps = payload.get("steps")
    if not isinstance(steps, list):
        raise RuntimeError("quality policy steps missing")
    found = {
        str(step.get("id"))
        for step in steps
        if isinstance(step, dict) and str(step.get("id")) in RETIRED_IDS
    }
    if found != RETIRED_IDS:
        raise RuntimeError(f"unexpected duplicate gate set: {sorted(found)}")
    for step in steps:
        if isinstance(step, dict) and str(step.get("id")) in RETIRED_IDS:
            if step.get("modes") != ["release"]:
                raise RuntimeError(f"unexpected staged modes for {step.get('id')}: {step.get('modes')!r}")
    payload["steps"] = [
        step for step in steps
        if not (isinstance(step, dict) and str(step.get("id")) in RETIRED_IDS)
    ]
    remaining_ids = {str(step.get("id")) for step in payload["steps"] if isinstance(step, dict)}
    for step in payload["steps"]:
        unknown = set(step.get("depends_on") or []) - remaining_ids
        if unknown:
            raise RuntimeError(f"step {step.get('id')} depends on retired/unknown gates: {sorted(unknown)}")
    POLICY.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_quality_test() -> None:
    text = QUALITY_TEST.read_text(encoding="utf-8")
    old_name = "def test_quality_loop_policy_separates_integration_and_release_real_model_steps() -> None:\n"
    new_name = "def test_quality_loop_policy_uses_single_production_bundle_for_real_model_browser() -> None:\n"
    if text.count(old_name) != 1:
        raise RuntimeError("quality governance test name changed")
    text = text.replace(old_name, new_name, 1)
    old = '''    configured_model_gates = {"configured-model-browser-conversation", "configured-model-browser-campaign"}\n    assert configured_model_gates.isdisjoint(steps_by_mode["integration"])\n    assert configured_model_gates <= steps_by_mode["release"]\n    steps = {step["id"]: step for step in policy["steps"]}\n'''
    new = '''    steps = {step["id"]: step for step in policy["steps"]}\n    duplicate_real_model_gates = {"configured-model-browser-conversation", "configured-model-browser-campaign"}\n    assert duplicate_real_model_gates.isdisjoint(steps)\n    assert steps["production-certification-bundle"]["modes"] == ["release"]\n'''
    if text.count(old) != 1:
        raise RuntimeError("quality governance boundary block changed")
    QUALITY_TEST.write_text(text.replace(old, new, 1), encoding="utf-8")


def update_systemic_test() -> None:
    text = SYSTEMIC_TEST.read_text(encoding="utf-8")
    old = '''    gate = steps["configured-model-browser-conversation"]\n    assert set(gate["modes"]) == {"release"}\n    assert steps["production-certification-bundle"]["modes"] == ["release"]\n    assert gate["blocking_level"] == "required"\n    assert gate["rerun_contract"] == "dependency_closure_then_downstream"\n    command = " ".join(gate["argv"])\n    assert "--journey strong-context" in command\n    assert "--model-mode configured" in command\n    assert {"product-browser-journey", "strong-context-case-catalog"} <= set(gate["depends_on"])\n\n'''
    new = '''    duplicate_real_model_gates = {"configured-model-browser-conversation", "configured-model-browser-campaign"}\n    assert duplicate_real_model_gates.isdisjoint(steps)\n    assert steps["production-certification-bundle"]["modes"] == ["release"]\n    controller_source = (ROOT / "scripts/verify_production_certification_bundle.py").read_text(encoding="utf-8")\n    browser_bundle_source = (ROOT / "scripts/verify_production_browser_bundle.py").read_text(encoding="utf-8")\n    assert '"browser": SCRIPTS / "verify_production_browser_bundle.py"' in controller_source\n    for marker in (\n        '"configured-strong-context"',\n        '"configured-strong-context-campaign"',\n        '"--model-mode", "configured"',\n        '"--runtime-profile", "protected-preprod"',\n    ):\n        assert marker in browser_bundle_source\n\n'''
    if text.count(old) != 1:
        raise RuntimeError("systemic real-model boundary block changed")
    SYSTEMIC_TEST.write_text(text.replace(old, new, 1), encoding="utf-8")


def update_authority_test() -> None:
    text = AUTHORITY_TEST.read_text(encoding="utf-8")
    old = '''    assert "release" not in by_id["configured-model-browser-conversation"]["modes"]\n    assert "release" not in by_id["configured-model-browser-campaign"]["modes"]\n'''
    new = '''    assert "configured-model-browser-conversation" not in by_id\n    assert "configured-model-browser-campaign" not in by_id\n'''
    if text.count(old) != 1:
        raise RuntimeError("B17a authority assertions changed")
    AUTHORITY_TEST.write_text(text.replace(old, new, 1), encoding="utf-8")


def update_manifest() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("phase manifest files list missing")
    for entry in files:
        path = ROOT / str(entry["path"])
        if not path.is_file():
            raise RuntimeError(f"managed file missing: {entry['path']}")
        data = path.read_bytes()
        entry["size"] = len(data)
        entry["sha256"] = hashlib.sha256(data).hexdigest()
    payload["file_count"] = len(files)
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    retire_policy_steps()
    update_quality_test()
    update_systemic_test()
    update_authority_test()
    update_manifest()
    SELF.unlink()
    print(json.dumps({
        "status": "PASS",
        "retired_duplicate_gates": sorted(RETIRED_IDS),
        "production_authority": "production-certification-bundle",
        "manifest_file_count": 1052,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
