#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "governance/quality-loop-policy.json"
TEST = ROOT / "services/agent-service/tests/architecture/test_quality_loop_governance.py"
MANIFEST = ROOT / "PHASE_CANDIDATE_MANIFEST.json"
SELF = Path(__file__).resolve()


def update_policy() -> None:
    payload = json.loads(POLICY.read_text(encoding="utf-8"))
    target_ids = {
        "configured-model-browser-conversation",
        "configured-model-browser-campaign",
    }
    seen: set[str] = set()
    for step in payload["steps"]:
        step_id = str(step.get("id") or "")
        if step_id not in target_ids:
            continue
        seen.add(step_id)
        current = step.get("modes")
        if current != ["integration"]:
            raise RuntimeError(f"unexpected modes for {step_id}: {current!r}")
        step["modes"] = ["release"]
    if seen != target_ids:
        raise RuntimeError(f"missing configured-model gates: {sorted(target_ids - seen)}")
    POLICY.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def update_regression() -> None:
    text = TEST.read_text(encoding="utf-8")
    replacements = {
        "def test_quality_loop_policy_exposes_static_quick_and_release_steps() -> None:\n":
            "def test_quality_loop_policy_separates_integration_and_release_real_model_steps() -> None:\n",
        'for mode in ["static", "quick", "release"]}':
            'for mode in ["static", "quick", "integration", "release"]}',
    }
    for old, new in replacements.items():
        if text.count(old) != 1:
            raise RuntimeError(f"expected exactly one test replacement: {old!r}")
        text = text.replace(old, new, 1)

    anchor = '    assert {"frontend-vitest", "frontend-build"} <= steps_by_mode["release"]\n'
    addition = (
        anchor
        + '    configured_model_gates = {"configured-model-browser-conversation", "configured-model-browser-campaign"}\n'
        + '    assert configured_model_gates.isdisjoint(steps_by_mode["integration"])\n'
        + '    assert configured_model_gates <= steps_by_mode["release"]\n'
    )
    if text.count(anchor) != 1:
        raise RuntimeError("release assertion anchor changed")
    text = text.replace(anchor, addition, 1)
    TEST.write_text(text, encoding="utf-8")


def update_manifest() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("phase manifest files list missing")
    for entry in files:
        relative = str(entry["path"])
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"managed file missing: {relative}")
        data = path.read_bytes()
        entry["size"] = len(data)
        entry["sha256"] = hashlib.sha256(data).hexdigest()
    payload["file_count"] = len(files)
    MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    update_policy()
    update_regression()
    update_manifest()
    SELF.unlink()
    print(json.dumps({
        "status": "PASS",
        "changed": [
            "governance/quality-loop-policy.json",
            "services/agent-service/tests/architecture/test_quality_loop_governance.py",
            "PHASE_CANDIDATE_MANIFEST.json",
        ],
        "configured_model_modes": ["release"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
