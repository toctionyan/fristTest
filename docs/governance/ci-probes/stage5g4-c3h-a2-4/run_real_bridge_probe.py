from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

EXACT_B36 = "e0e04d51e9da9790bef7bd0482584f60b8e975a9"
A2_HEAD = "610c6f9aff0a8c9336614b85e9d91ba89f51b40b"
CHANGE_ID = "stage5g4-c3h-a2-4-bridge-probe"
TEST_PATH = "services/agent-service/tests/runtime/test_stage4_goal_output_refs.py"
TEST_SELECTOR = "test_dependency_goal_output_is_not_reused_across_different_explicit_targets"
SELECTOR = f"{TEST_PATH}::{TEST_SELECTOR}"
BASE_TEST_SHA256 = "224f63054775bfb00c309035d24896867975685d28ca115c1cc45d5cc812cc0f"
OVERLAY_TEST_SHA256 = "d442fd4d03f67e0272cc7d1b4308195e36986944a31193b5b51c5c1065d01f38"
OVERLAY_ZIP_SHA256 = "ac86e73820716bdf2a995ffe9fc2813b42cbbda741932a14e10597fe11d72da6"
LEGAL_B1_SOURCE_FP = "f9ab6acafe592e49534ed6d2fd2b3e9b0f45debc7bb26fbd9a86d06515e1a2fa"
CLAIM_ID = "C3H.A2.4.BRIDGE.RED"
ALLOWED_PATH = "services/agent-service/src/agent_core/lifecycle/pretool_execution_policy.py"


def run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, check=False)


def git(root: Path, *args: str) -> str:
    cp = run(["git", "-C", str(root), *args])
    if cp.returncode:
        raise RuntimeError(cp.stderr)
    return cp.stdout.strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--overlay", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    control = Path(args.control).resolve()
    source = Path(args.source).resolve()
    overlay_external = Path(args.overlay).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    if git(control, "rev-parse", "HEAD") != A2_HEAD:
        raise AssertionError("control HEAD is not exact A2 candidate")
    if git(source, "rev-parse", "HEAD") != EXACT_B36:
        raise AssertionError("source HEAD is not exact B36")
    if run(["git", "-C", str(source), "diff", "--exit-code", "--", "."]).returncode:
        raise AssertionError("source has tracked changes before probe")

    base_test = source / TEST_PATH
    if sha256(base_test) != BASE_TEST_SHA256:
        raise AssertionError("exact B36 test bytes mismatch")
    if f"def {TEST_SELECTOR}(" in base_test.read_text(encoding="utf-8"):
        raise AssertionError("preferred selector unexpectedly exists in exact B36")
    if not overlay_external.is_file() or sha256(overlay_external) != OVERLAY_ZIP_SHA256:
        raise AssertionError("immutable Oracle overlay mismatch")

    import sys
    sys.path.insert(0, str(control / "scripts"))
    from quality_control.contracts import workspace_snapshot
    from quality_control.baseline_oracle import _canonical_json_fingerprint

    before = workspace_snapshot(source)
    if before["fingerprint"] != LEGAL_B1_SOURCE_FP:
        raise AssertionError(f"unexpected exact-B36 workspace fingerprint: {before['fingerprint']}")

    probe = source / ".quality" / "c3h-a2-4"
    probe.mkdir(parents=True, exist_ok=True)
    overlay = probe / "overlay.zip"
    shutil.copy2(overlay_external, overlay)

    import zipfile
    with zipfile.ZipFile(overlay) as zf:
        if zf.namelist() != [TEST_PATH]:
            raise AssertionError(f"unexpected Oracle members: {zf.namelist()}")
        overlay_test = zf.read(TEST_PATH)
    if hashlib.sha256(overlay_test).hexdigest() != OVERLAY_TEST_SHA256:
        raise AssertionError("Oracle test bytes mismatch")

    claims = {
        "schema_version": 1,
        "target_id": CHANGE_ID,
        "claims": [{
            "id": CLAIM_ID,
            "statement": "The real product-quality Bridge can preserve exact-B36 source authority while the immutable Oracle exposes the cross-target dependency regression.",
            "risk": "P1",
            "required_mode": "quick",
            "evidence_kind": "counterexample",
            "required_gates": ["python-test-suites"],
            "evidence_refs": [SELECTOR],
            "owner": "quality-controller",
            "closure_requirement": "regression-transition",
        }],
    }
    write_json(probe / "claim.json", claims)

    target = probe / "target.md"
    target.write_text("\n".join([
        "# 目标",
        f"- 目标 ID：{CHANGE_ID}",
        f"- 变更标识：{CHANGE_ID}",
        "- 执行上下文：ci-probe",
        "- 目标类型：repair",
        "",
        "验证 A2 candidate Bridge 可用 external exact-B36 product workspace 运行 immutable Oracle RED baseline probe。",
        "",
        "## 允许范围",
        f"- 允许变更路径：{ALLOWED_PATH}",
        "- 新增抽象记录：无",
        "",
        "## 禁止范围",
        "probe 不修改产品源码，不进入 contract-begin。",
        "",
        "## 验收条件",
        "- 最低质量模式：quick",
        "- 声明清单：.quality/c3h-a2-4/claim.json",
        f"- 验收 ID：{CLAIM_ID}",
        "",
        "## 基线",
        "baseline = untouched exact B36 + immutable B1 Oracle。",
        "",
        "## 修复轮次",
        "- 最大轮次：1",
        "- 当前轮次：1",
        "- 失败后：停止；本 probe 不做产品修复。",
        "",
    ]) + "\n", encoding="utf-8")

    identity: dict[str, Any] = {
        "schema_version": 1,
        "oracle_id": "stage5g4-c3g3-b1-preferred-red",
        "base_source_identity": {"repository": "toctionyan/fristTest", "commit_sha": EXACT_B36},
        "base_workspace_fingerprint": before["fingerprint"],
        "overlay_artifact_sha256": OVERLAY_ZIP_SHA256,
        "overlay_file_map": [{
            "path": TEST_PATH,
            "base_file_sha256": BASE_TEST_SHA256,
            "overlay_file_sha256": OVERLAY_TEST_SHA256,
        }],
        "claim_bindings": [{"claim_id": CLAIM_ID, "selector": SELECTOR}],
        "provenance": {
            "provider": "github-actions",
            "run_id": 31179398373,
            "job_id": 92868795829,
            "artifact_id": 8994125634,
            "artifact_digest": OVERLAY_ZIP_SHA256,
        },
        "execution_mode": "ephemeral_overlay_view",
    }
    identity["canonical_fingerprint"] = _canonical_json_fingerprint(identity)
    write_json(probe / "baseline-oracle-manifest.json", identity)

    active = source / "governance" / "active-change.json"
    prior_active = active.read_bytes() if active.is_file() else None
    contract_cli = control / "skill-system" / "controller" / "change_contract_cli.py"
    init = run([
        sys.executable, "-B", str(contract_cli), "init",
        "--profile", "product-code",
        "--change-id", CHANGE_ID,
        "--goal", "Probe real Bridge external workspace authority without persistent product admission",
        "--target-kind", "repair",
        "--allow", ALLOWED_PATH,
        "--minimum-mode", "quick",
        "--quality-target", ".quality/c3h-a2-4/target.md",
        "--approve", "--force",
    ], cwd=source)
    (output / "contract-init.stdout").write_text(init.stdout, encoding="utf-8")
    (output / "contract-init.stderr").write_text(init.stderr, encoding="utf-8")
    if init.returncode:
        raise AssertionError(f"contract init failed: {init.stderr}")

    after_inputs = workspace_snapshot(source)
    if after_inputs["fingerprint"] != before["fingerprint"]:
        raise AssertionError("probe control inputs changed product source fingerprint")

    bridge = control / "skill-system" / "controller" / "product_quality_bridge.py"
    env = os.environ.copy()
    result = run([
        sys.executable, "-B", str(bridge), "baseline",
        "--workspace-root", str(source),
        "--baseline-oracle-manifest", ".quality/c3h-a2-4/baseline-oracle-manifest.json",
        "--baseline-oracle-artifact", ".quality/c3h-a2-4/overlay.zip",
    ], cwd=control, env=env)
    (output / "bridge.stdout").write_text(result.stdout, encoding="utf-8")
    (output / "bridge.stderr").write_text(result.stderr, encoding="utf-8")
    (output / "bridge.exit-code.txt").write_text(str(result.returncode) + "\n", encoding="utf-8")
    try:
        bridge_json = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Bridge did not return JSON: {result.stdout}") from exc
    write_json(output / "bridge-result.json", bridge_json)

    evidence = source / ".quality" / "product-code" / CHANGE_ID / "baseline"
    summary_path = evidence / "run-summary.json"
    record_path = evidence / "baseline-record.json"
    if not summary_path.is_file() or not record_path.is_file():
        raise AssertionError("real Bridge baseline probe did not produce baseline evidence")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    claim = next((row for row in summary.get("claim_results", []) if row.get("id") == CLAIM_ID), None)

    if result.returncode != 0 or bridge_json.get("status") != "PASS":
        raise AssertionError(f"Bridge did not accept expected RED baseline probe: {bridge_json}")
    if bridge_json.get("workspace_root") != str(source):
        raise AssertionError("Bridge result is not bound to explicit source workspace")
    if summary.get("decision") != "FAIL" or summary.get("loop_status") != "BASELINE_RECORDED":
        raise AssertionError(f"unexpected baseline summary: {summary.get('decision')}/{summary.get('loop_status')}")
    if not claim or claim.get("status") != "FAILED" or claim.get("evidence_ref_statuses", {}).get(SELECTOR) != "FAILED":
        raise AssertionError(f"preferred probe Claim was not actually executed/FAILED: {claim}")
    if summary.get("baseline_oracle_overlay_identity", {}).get("canonical_fingerprint") != identity["canonical_fingerprint"]:
        raise AssertionError("run-summary Oracle fingerprint mismatch")
    if record.get("baseline_oracle_overlay_identity", {}).get("canonical_fingerprint") != identity["canonical_fingerprint"]:
        raise AssertionError("baseline-record Oracle fingerprint mismatch")

    updated_contract = json.loads(active.read_text(encoding="utf-8"))
    expected_baseline = f".quality/product-code/{CHANGE_ID}/baseline"
    if updated_contract.get("baseline_evidence") != expected_baseline:
        raise AssertionError("Bridge did not record probe baseline into disposable Change Contract")

    after = workspace_snapshot(source)
    if after["fingerprint"] != before["fingerprint"]:
        raise AssertionError("source fingerprint changed during Bridge integration probe")
    if sha256(base_test) != BASE_TEST_SHA256 or f"def {TEST_SELECTOR}(" in base_test.read_text(encoding="utf-8"):
        raise AssertionError("Oracle overlay persisted into source test")
    if git(source, "rev-parse", "HEAD") != EXACT_B36:
        raise AssertionError("source HEAD changed")
    if run(["git", "-C", str(source), "diff", "--exit-code", "--", "."]).returncode:
        raise AssertionError("tracked product source changed")

    shutil.copytree(evidence, output / "probe-baseline-evidence")
    write_json(output / "probe-active-change-after-bridge.json", updated_contract)
    report = {
        "schema_version": 1,
        "stage": "5G4-C3H-A2-4",
        "verdict": "REAL_BRIDGE_EXTERNAL_EXACT_B36_ORACLE_PROBE_PASS",
        "control_head": A2_HEAD,
        "source_head": EXACT_B36,
        "source_workspace_fingerprint_before": before["fingerprint"],
        "source_workspace_fingerprint_after": after["fingerprint"],
        "source_workspace_unchanged": True,
        "bridge_cli_status": bridge_json.get("status"),
        "bridge_workspace_root": bridge_json.get("workspace_root"),
        "baseline_decision": summary.get("decision"),
        "baseline_loop_status": summary.get("loop_status"),
        "claim_status": claim.get("status"),
        "evidence_ref_status": claim.get("evidence_ref_statuses", {}).get(SELECTOR),
        "oracle_canonical_fingerprint": identity["canonical_fingerprint"],
        "disposable_contract_baseline_evidence": updated_contract.get("baseline_evidence"),
        "formal_product_baseline_admitted": False,
        "contract_begin_run": False,
        "canonical_b38_pushed": False,
        "production_closed": False,
    }
    write_json(output / "C3H_A2_4_INTEGRATION_PROBE.json", report)

    if prior_active is None:
        active.unlink(missing_ok=True)
    else:
        active.write_bytes(prior_active)
    if run(["git", "-C", str(source), "diff", "--exit-code", "--", "."]).returncode:
        raise AssertionError("tracked source not clean after disposable contract restoration")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
