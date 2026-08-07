from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

EXACT_B36 = "e0e04d51e9da9790bef7bd0482584f60b8e975a9"
TEST_PATH = "services/agent-service/tests/runtime/test_stage4_goal_output_refs.py"
TEST_SELECTOR = "test_dependency_goal_output_is_not_reused_across_different_explicit_targets"
SELECTOR = f"{TEST_PATH}::{TEST_SELECTOR}"
BASE_TEST_SHA256 = "224f63054775bfb00c309035d24896867975685d28ca115c1cc45d5cc812cc0f"
OVERLAY_TEST_SHA256 = "d442fd4d03f67e0272cc7d1b4308195e36986944a31193b5b51c5c1065d01f38"
OVERLAY_ZIP_SHA256 = "ac86e73820716bdf2a995ffe9fc2813b42cbbda741932a14e10597fe11d72da6"
CLAIM_ID = "B1.PREFERRED.RED"
TARGET_ID = "stage5g4-c3g3-b1-preferred-red"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(source: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)
    return completed.stdout.strip()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--overlay", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    control = Path(args.control).resolve()
    source = Path(args.source).resolve()
    overlay = Path(args.overlay).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)

    if git(source, "rev-parse", "HEAD") != EXACT_B36:
        raise AssertionError("source HEAD is not exact B36")
    if subprocess.run(["git", "-C", str(source), "diff", "--exit-code", "--", "."], check=False).returncode != 0:
        raise AssertionError("exact B36 source has tracked changes before replay")

    base_test = source / TEST_PATH
    if sha256(base_test) != BASE_TEST_SHA256:
        raise AssertionError("exact B36 test bytes mismatch")
    if f"def {TEST_SELECTOR}(" in base_test.read_text(encoding="utf-8"):
        raise AssertionError("preferred selector unexpectedly exists in exact B36")
    if not overlay.is_file() or sha256(overlay) != OVERLAY_ZIP_SHA256:
        raise AssertionError("immutable B1-1 overlay bytes mismatch")

    import zipfile
    with zipfile.ZipFile(overlay) as archive:
        if archive.namelist() != [TEST_PATH]:
            raise AssertionError(f"unexpected overlay members: {archive.namelist()}")
        overlay_test = archive.read(TEST_PATH)
    if hashlib.sha256(overlay_test).hexdigest() != OVERLAY_TEST_SHA256:
        raise AssertionError("newer test bytes mismatch")
    if f"def {TEST_SELECTOR}(".encode() not in overlay_test:
        raise AssertionError("preferred selector absent from immutable overlay")

    scripts = control / "scripts"
    sys.path.insert(0, str(scripts))
    from quality_control.contracts import workspace_snapshot
    from quality_control.baseline_oracle import _canonical_json_fingerprint

    source_before = workspace_snapshot(source)
    b1 = source / ".quality" / "b1"
    b1.mkdir(parents=True, exist_ok=True)

    claims = {
        "schema_version": 1,
        "target_id": TARGET_ID,
        "claims": [
            {
                "id": CLAIM_ID,
                "statement": "A dependency goal output must not authorize a consumer goal targeting a different explicit order.",
                "risk": "P1",
                "required_mode": "static",
                "evidence_kind": "counterexample",
                "required_gates": ["preferred-red-proof"],
                "evidence_refs": [SELECTOR],
                "owner": "quality-controller",
                "closure_requirement": "regression-transition",
            }
        ],
    }
    write_json(b1 / "claim.json", claims)

    target_lines = [
        "# 目标",
        f"- 目标 ID：{TARGET_ID}",
        f"- 变更标识：{TARGET_ID}",
        "- 执行上下文：local-change",
        "- 目标类型：migration",
        "",
        "证明 exact B36 产品实现对 newer immutable acceptance oracle 真实为 RED。",
        "",
        "## 允许范围",
        "- 允许变更路径：services/agent-service/src/agent_core/lifecycle/pretool_execution_policy.py",
        "- 新增抽象记录：无",
        "",
        "## 禁止范围",
        "本阶段不修改任何产品源码；只记录正式 RED baseline evidence。",
        "",
        "## 验收条件",
        "- 最低质量模式：static",
        "- 声明清单：.quality/b1/claim.json",
        f"- 验收 ID：{CLAIM_ID}",
        "",
        "## 基线",
        "baseline = untouched exact B36 product source + immutable BaselineOracleOverlay。",
        "",
        "## 修复轮次",
        "- 最大轮次：2",
        "- 当前轮次：1",
        "- 失败后：只允许后续受控产品修复，不修改 acceptance oracle。",
        "",
    ]
    (b1 / "target.md").write_text("\n".join(target_lines), encoding="utf-8")

    gate_python = (control / "services" / "agent-service" / ".venv" / "bin" / "python").resolve()
    if not gate_python.is_file():
        raise AssertionError(f"gate interpreter missing: {gate_python}")
    gate_script = (
        "from pathlib import Path; import os, pytest; "
        "j=Path(os.environ['QUALITY_EVIDENCE_DIR'])/'junit'; j.mkdir(parents=True, exist_ok=True); "
        f"raise SystemExit(pytest.main(['-q','{SELECTOR}','--junitxml='+str(j/'preferred-red-proof.xml')]))"
    )
    policy = {
        "version": "stage5g4-c3g3-b1",
        "steps": [
            {
                "id": "preferred-red-proof",
                "name": "preferred exact-B36 RED acceptance proof",
                "modes": ["static"],
                "kind": "shell",
                "argv": [str(gate_python), "-B", "-c", gate_script],
                "owner": "quality-controller",
                "category": "unit-contract",
                "blocking_level": "required",
                "repair_playbook": "repair product semantics; never modify the immutable oracle",
                "rerun_contract": "dependency_closure_then_downstream",
                "depends_on": [],
                "environment": {
                    "APP_PROFILE": "local",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": "services/agent-service/src:services/agent-service",
                },
                "timeout_seconds": 120,
            }
        ],
    }
    write_json(b1 / "policy.json", policy)

    source_after_inputs = workspace_snapshot(source)
    if source_after_inputs["fingerprint"] != source_before["fingerprint"]:
        raise AssertionError("ignored B1 control inputs changed source workspace fingerprint")
    source_fp = source_before["fingerprint"]
    (output / "source-fingerprint-before.txt").write_text(source_fp + "\n", encoding="utf-8")

    identity: dict[str, Any] = {
        "schema_version": 1,
        "oracle_id": "stage5g4-c3g3-b1-preferred-red",
        "base_source_identity": {"repository": "toctionyan/fristTest", "commit_sha": EXACT_B36},
        "base_workspace_fingerprint": source_fp,
        "overlay_artifact_sha256": OVERLAY_ZIP_SHA256,
        "overlay_file_map": [
            {"path": TEST_PATH, "base_file_sha256": BASE_TEST_SHA256, "overlay_file_sha256": OVERLAY_TEST_SHA256}
        ],
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
    manifest = runtime / "baseline-oracle-manifest.json"
    write_json(manifest, identity)

    controller = control / "scripts" / "quality_loop.py"

    def run_controller(label: str, *, use_oracle: bool) -> tuple[int, dict[str, Any]]:
        evidence = b1 / f"{label}-evidence"
        state = b1 / f"{label}-state"
        argv = [
            sys.executable,
            "-B",
            str(controller),
            "--workspace-root",
            str(source),
            "--policy",
            ".quality/b1/policy.json",
            "--mode",
            "static",
            "--target",
            str(b1 / "target.md"),
            "--baseline",
            "--evidence-dir",
            str(evidence),
            "--state-dir",
            str(state),
        ]
        if use_oracle:
            argv += ["--baseline-oracle-manifest", str(manifest), "--baseline-oracle-artifact", str(overlay)]
        completed = subprocess.run(argv, text=True, capture_output=True, check=False)
        (output / f"{label}.stdout").write_text(completed.stdout, encoding="utf-8")
        (output / f"{label}.stderr").write_text(completed.stderr, encoding="utf-8")
        (output / f"{label}.exit-code.txt").write_text(str(completed.returncode) + "\n", encoding="utf-8")
        summary_path = evidence / "run-summary.json"
        if not summary_path.is_file():
            raise AssertionError(f"{label} produced no run-summary.json")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return completed.returncode, summary

    no_oracle_rc, no_oracle = run_controller("no-oracle", use_oracle=False)
    if no_oracle_rc == 0 or no_oracle.get("decision") != "FAIL" or no_oracle.get("loop_status") != "INVALID_INPUT":
        raise AssertionError(f"unexpected no-oracle result: rc={no_oracle_rc}, summary={no_oracle}")
    no_oracle_text = json.dumps(no_oracle, ensure_ascii=False) + (output / "no-oracle.stderr").read_text(encoding="utf-8")
    if "test selector does not exist" not in no_oracle_text:
        raise AssertionError("no-oracle failure did not prove missing selector")
    write_json(output / "no-oracle-run-summary.json", no_oracle)
    shutil.rmtree(b1 / "no-oracle-evidence", ignore_errors=True)
    shutil.rmtree(b1 / "no-oracle-state", ignore_errors=True)

    formal_rc, summary = run_controller("formal-red", use_oracle=True)
    if formal_rc == 0:
        raise AssertionError("formal RED baseline unexpectedly returned PASS process status")
    evidence = b1 / "formal-red-evidence"
    record = json.loads((evidence / "baseline-record.json").read_text(encoding="utf-8"))
    oracle_file = json.loads((evidence / "baseline-oracle-overlay-identity.json").read_text(encoding="utf-8"))
    if summary.get("run_kind") != "baseline" or summary.get("decision") != "FAIL" or summary.get("loop_status") != "BASELINE_RECORDED":
        raise AssertionError(f"formal RED baseline not recorded: {summary}")
    claim = next(item for item in summary["claim_results"] if item["id"] == CLAIM_ID)
    if claim["status"] != "FAILED" or claim["evidence_ref_statuses"].get(SELECTOR) != "FAILED":
        raise AssertionError(f"preferred Claim is not real FAILED: {claim}")

    fp = identity["canonical_fingerprint"]
    if summary["baseline_oracle_overlay_identity"]["canonical_fingerprint"] != fp:
        raise AssertionError("run-summary Oracle fingerprint mismatch")
    if record["baseline_oracle_overlay_identity"]["canonical_fingerprint"] != fp:
        raise AssertionError("baseline-record Oracle fingerprint mismatch")
    if oracle_file["canonical_fingerprint"] != fp:
        raise AssertionError("independent Oracle identity fingerprint mismatch")
    if summary["baseline_oracle_overlay_identity"] != oracle_file or record["baseline_oracle_overlay_identity"] != oracle_file:
        raise AssertionError("Oracle identities are not byte-semantically equal")

    source_after = workspace_snapshot(source)["fingerprint"]
    if source_after != source_fp:
        raise AssertionError("source workspace fingerprint changed during Oracle replay")
    if record["workspace_snapshot_fingerprint"] != source_fp:
        raise AssertionError("baseline record does not bind source workspace fingerprint")
    if summary["workspace_snapshot_start_fingerprint"] != source_fp or summary["workspace_snapshot_fingerprint"] != source_fp:
        raise AssertionError("run summary does not bind unchanged source fingerprint")
    if sha256(base_test) != BASE_TEST_SHA256 or f"def {TEST_SELECTOR}(" in base_test.read_text(encoding="utf-8"):
        raise AssertionError("source test bytes were modified by Oracle replay")
    if git(source, "rev-parse", "HEAD") != EXACT_B36:
        raise AssertionError("source HEAD changed")
    if subprocess.run(["git", "-C", str(source), "diff", "--exit-code", "--", "."], check=False).returncode != 0:
        raise AssertionError("tracked exact-B36 source changed")

    report = {
        "schema_version": 1,
        "stage": "5G4-C3G3-B1-2",
        "verdict": "FORMAL_EXACT_B36_RED_BASELINE_RECORDED",
        "exact_b36_commit": EXACT_B36,
        "preferred_selector": SELECTOR,
        "without_oracle": {"selector_parse": "MISSING", "loop_status": no_oracle["loop_status"]},
        "with_oracle": {
            "decision": summary["decision"],
            "loop_status": summary["loop_status"],
            "claim_status": claim["status"],
            "evidence_ref_status": claim["evidence_ref_statuses"][SELECTOR],
        },
        "source_workspace_fingerprint_before": source_fp,
        "source_workspace_fingerprint_after": source_after,
        "source_workspace_unchanged": True,
        "base_test_sha256": BASE_TEST_SHA256,
        "overlay_test_sha256": OVERLAY_TEST_SHA256,
        "overlay_artifact_sha256": OVERLAY_ZIP_SHA256,
        "oracle_canonical_fingerprint": fp,
        "oracle_provenance": identity["provenance"],
        "target_identity": summary["target_identity"],
        "policy_fingerprint": summary["policy_fingerprint"],
        "formal_baseline_record_file": "formal-red-evidence/baseline-record.json",
        "product_baseline_run": False,
        "contract_begin_run": False,
        "production_closed": False,
    }
    write_json(output / "B1_FORMAL_RED_REPLAY.json", report)
    write_json(output / "baseline-oracle-manifest.json", identity)
    (output / "source-fingerprint-after.txt").write_text(source_after + "\n", encoding="utf-8")
    shutil.copytree(evidence, output / "formal-red-evidence")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
