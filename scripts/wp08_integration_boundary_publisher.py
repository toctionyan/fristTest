#!/usr/bin/env python3
"""Temporary byte-identity publisher for the proven WP-08 integration repair.

This helper exists only on the governed repair branch.  It recreates the exact
four repair files that already passed full deterministic Integration in run
31256791072 and refuses promotion unless their SHA-256 digests match that PASS
oracle.  It then refreshes only the protected-source baseline metadata/index.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

ORACLE_SHA256 = {
    "governance/quality-loop-policy.json": "e0dc98bb6e28ab43f130df39471f692acf941acb723eb82e3ec05d740895c4ae",
    "services/agent-service/tests/architecture/test_quality_loop_governance.py": "743c870478aa28a9bc11f2e3aaf934652d428cfcc7ce743f2c69e4545550c067",
    "services/agent-service/tests/architecture/test_systemic_operational_closure.py": "b68edda731890fa500ed9756c549d45b16d974c65cfa0291d15a53584b3179c2",
    "services/agent-service/tests/runtime/test_b17a_production_certification_authority.py": "e405bc41fa6ea30da7ab377c9ed75debb57dfaf41c6f2345742261d0c27782e2",
}
PROTECTED_CHANGED = {
    "services/agent-service/tests/architecture/test_quality_loop_governance.py",
    "services/agent-service/tests/architecture/test_systemic_operational_closure.py",
    "services/agent-service/tests/runtime/test_b17a_production_certification_authority.py",
}
IGNORED_PARTS = {".venv", ".pytest_cache", "node_modules", "__pycache__"}


class PublishError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_oracle() -> dict[str, str]:
    actual: dict[str, str] = {}
    for relative, expected in ORACLE_SHA256.items():
        value = sha256(ROOT / relative)
        actual[relative] = value
        if value != expected:
            raise PublishError(
                f"PASS oracle mismatch for {relative}: expected={expected} actual={value}"
            )
    return actual


def replace_once(path: Path, before: str, after: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(before) != 1:
        raise PublishError(f"repair anchor must occur exactly once: {path}")
    path.write_text(text.replace(before, after, 1), encoding="utf-8")


def apply_repair() -> dict[str, str]:
    policy_path = ROOT / "governance" / "quality-loop-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    retired = {
        "configured-model-browser-conversation",
        "configured-model-browser-campaign",
    }
    present = {str(step.get("id") or "") for step in policy["steps"]} & retired
    if present == retired:
        policy["steps"] = [
            step for step in policy["steps"] if str(step.get("id") or "") not in retired
        ]
        policy_path.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif present:
        raise PublishError(f"partial legacy gate state: {sorted(present)}")

    governance = ROOT / "services/agent-service/tests/architecture/test_quality_loop_governance.py"
    old_governance = '''def test_quality_loop_policy_exposes_static_quick_and_release_steps() -> None:
    root = workspace_root(__file__)
    policy = json.loads((root / "governance" / "quality-loop-policy.json").read_text(encoding="utf-8"))
    steps_by_mode = {mode: {step["name"] for step in policy["steps"] if mode in step.get("modes", [])} for mode in ["static", "quick", "release"]}
    assert {"skill-package-verify", "architecture-convergence", "module-vertical-closure", "version-consistency"} <= steps_by_mode["static"]
    assert "python-test-suites" in steps_by_mode["quick"]
    assert {"frontend-vitest", "frontend-build"} <= steps_by_mode["release"]
    steps = {step["id"]: step for step in policy["steps"]}
    assert steps["frontend-vitest"]["argv"] == ["{npm}", "run", "test:ci"]
'''
    new_governance = '''def test_quality_loop_policy_uses_single_production_bundle_for_real_model_browser() -> None:
    root = workspace_root(__file__)
    policy = json.loads((root / "governance" / "quality-loop-policy.json").read_text(encoding="utf-8"))
    steps_by_mode = {mode: {step["name"] for step in policy["steps"] if mode in step.get("modes", [])} for mode in ["static", "quick", "integration", "release"]}
    assert {"skill-package-verify", "architecture-convergence", "module-vertical-closure", "version-consistency"} <= steps_by_mode["static"]
    assert "python-test-suites" in steps_by_mode["quick"]
    assert {"frontend-vitest", "frontend-build"} <= steps_by_mode["release"]
    steps = {step["id"]: step for step in policy["steps"]}
    duplicate_real_model_gates = {"configured-model-browser-conversation", "configured-model-browser-campaign"}
    assert duplicate_real_model_gates.isdisjoint(steps)
    assert steps["production-certification-bundle"]["modes"] == ["release"]
    assert steps["frontend-vitest"]["argv"] == ["{npm}", "run", "test:ci"]
'''
    if sha256(governance) != ORACLE_SHA256[governance.relative_to(ROOT).as_posix()]:
        replace_once(governance, old_governance, new_governance)

    systemic = ROOT / "services/agent-service/tests/architecture/test_systemic_operational_closure.py"
    old_systemic = '''    gate = steps["configured-model-browser-conversation"]
    assert set(gate["modes"]) == {"integration"}
    assert steps["production-certification-bundle"]["modes"] == ["release"]
    assert gate["blocking_level"] == "required"
    assert gate["rerun_contract"] == "dependency_closure_then_downstream"
    command = " ".join(gate["argv"])
    assert "--journey strong-context" in command
    assert "--model-mode configured" in command
    assert {"product-browser-journey", "strong-context-case-catalog"} <= set(gate["depends_on"])
'''
    new_systemic = '''    duplicate_real_model_gates = {"configured-model-browser-conversation", "configured-model-browser-campaign"}
    assert duplicate_real_model_gates.isdisjoint(steps)
    assert steps["production-certification-bundle"]["modes"] == ["release"]
    controller_source = (ROOT / "scripts/verify_production_certification_bundle.py").read_text(encoding="utf-8")
    browser_bundle_source = (ROOT / "scripts/verify_production_browser_bundle.py").read_text(encoding="utf-8")
    assert '"browser": SCRIPTS / "verify_production_browser_bundle.py"' in controller_source
    for marker in (
        '"configured-strong-context"',
        '"configured-strong-context-campaign"',
        '"--model-mode", "configured"',
        '"--runtime-profile", "protected-preprod"',
    ):
        assert marker in browser_bundle_source
'''
    if sha256(systemic) != ORACLE_SHA256[systemic.relative_to(ROOT).as_posix()]:
        replace_once(systemic, old_systemic, new_systemic)

    authority = ROOT / "services/agent-service/tests/runtime/test_b17a_production_certification_authority.py"
    old_authority = '''    assert "release" not in by_id["configured-model-browser-conversation"]["modes"]
    assert "release" not in by_id["configured-model-browser-campaign"]["modes"]
'''
    new_authority = '''    assert "configured-model-browser-conversation" not in by_id
    assert "configured-model-browser-campaign" not in by_id
'''
    if sha256(authority) != ORACLE_SHA256[authority.relative_to(ROOT).as_posix()]:
        replace_once(authority, old_authority, new_authority)

    return assert_oracle()


def protected_inventory(baseline: dict) -> dict[str, str]:
    current: dict[str, str] = {}
    for name in tuple(str(x) for x in baseline["protected_roots"]):
        base = ROOT / name
        if not base.exists():
            continue
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            if any(part in IGNORED_PARTS for part in path.parts):
                continue
            current[path.relative_to(ROOT).as_posix()] = sha256(path)
    return current


def refresh_baseline(generated_from: str) -> dict:
    baseline_path = ROOT / "skill-system/registry/product-source-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    old = {str(k): str(v) for k, v in baseline["files"].items()}
    current = protected_inventory(baseline)
    changed = {path for path in set(current) | set(old) if current.get(path) != old.get(path)}
    if changed != PROTECTED_CHANGED:
        raise PublishError(
            "unexpected protected-source delta: "
            + json.dumps(sorted(changed), ensure_ascii=False)
        )
    if len(current) != int(baseline["file_count"]):
        raise PublishError(
            f"protected-source file count drift: {len(current)} != {baseline['file_count']}"
        )
    for relative in PROTECTED_CHANGED:
        if current[relative] != ORACLE_SHA256[relative]:
            raise PublishError(f"protected source does not match PASS oracle: {relative}")

    baseline["generated_from"] = "git:" + generated_from
    baseline["generated_at"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    baseline["files"] = dict(sorted(current.items()))
    baseline_path.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "PASS",
        "generated_from": baseline["generated_from"],
        "baseline_sha256": sha256(baseline_path),
        "changed_protected_paths": sorted(PROTECTED_CHANGED),
        "file_count": len(current),
        "oracle_sha256": dict(sorted(ORACLE_SHA256.items())),
        "production_closed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("repair", "baseline", "verify"))
    parser.add_argument("--generated-from")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        if args.mode == "repair":
            result = {
                "status": "PASS",
                "oracle_sha256": apply_repair(),
                "source_evidence_run_id": 31256791072,
                "source_evidence_artifact_id": 9021655897,
                "production_closed": False,
            }
        elif args.mode == "baseline":
            generated_from = str(args.generated_from or "").strip()
            if len(generated_from) != 40:
                raise PublishError("--generated-from must be a full commit SHA")
            result = refresh_baseline(generated_from)
        else:
            result = {
                "status": "PASS",
                "oracle_sha256": assert_oracle(),
                "production_closed": False,
            }
    except (PublishError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        result = {
            "status": "FAIL",
            "reason": exc.__class__.__name__,
            "error": str(exc),
            "production_closed": False,
        }
    if args.output:
        out = Path(args.output).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
