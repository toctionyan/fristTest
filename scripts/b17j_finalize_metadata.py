#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = "scripts/b17j_finalize_metadata.py"
TEMP_WORKFLOW = ".github/workflows/b17j-finalize-metadata.yml"


def update_metadata() -> None:
    summary_path = ROOT / "B17J_STAGE_SUMMARY.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["validation"].update({
        "github_preclosure_run_id": 30611637518,
        "github_preclosure_head_sha": "26f3d058e77d5e025585e8c311883b07d32db325",
        "github_preclosure_status": "PASS",
        "github_skill_self_validation": "PASS",
        "github_quality_static": "PASS",
        "github_quality_quick": "PASS",
        "github_quality_integration": "SKIPPED_BY_PR_CONTRACT",
        "github_quick_loop_status": "CI_VERIFIED",
        "github_quick_decision": "PASS",
        "github_quick_completion_eligible": True,
        "github_quick_required_gates": 18,
        "github_agent_tests": 858,
        "github_business_tests": 28,
        "github_test_skips": 0,
        "github_python_coverage_line_rate": 0.7288,
        "github_frontend_coverage_line_rate": 0.5432,
        "github_full_lifecycle_canary": "PASS",
        "github_product_browser_journey": "PASS",
        "github_static_artifact": {
            "artifact_id": 8785676268,
            "size_in_bytes": 78106,
            "sha256": "51a6f9e745732a6afd1cf0ccc33019e8913e0ef0da4bb66aacd392df6195dab8"
        },
        "github_quick_artifact": {
            "artifact_id": 8785743142,
            "size_in_bytes": 334160,
            "sha256": "87bf278e5b91dab62750f6417530367508d1f07d1ba435989c366ec9f01b0746"
        },
        "github_ci_status": "EXACT_FINAL_TREE_RERUN_REQUIRED",
        "source_metadata_self_reference_policy": "authoritative final run identity is recorded in external delivery evidence, not by mutating the already-verified source tree"
    })
    summary["required_next_action"] = "run official GitHub quality CI on the exact metadata-normalized head, merge only after PASS, and record that final immutable run in external delivery evidence"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_path = ROOT / "release/VALIDATION_REPORT.md"
    report = report_path.read_text(encoding="utf-8")
    if "## Round 4 pre-closure GitHub PASS" not in report:
        report += """
## Round 4 pre-closure GitHub PASS

GitHub run `30611637518` on commit `26f3d058e77d5e025585e8c311883b07d32db325` passed Skill self-validation, Static and Quick. Quick produced `CI_VERIFIED`, decision `PASS`, `completion_eligible=true`, no missing prerequisites and all 18 required gates PASS. Standard suites reported 858 Agent tests and 28 Business tests with zero skips. Coverage passed at Python 0.7288 and frontend 0.5432. The authenticated HTTP lifecycle and Chromium product journey both passed.

Downloaded evidence artifacts were CRC/read verified and matched GitHub digests: Static `51a6f9e745732a6afd1cf0ccc33019e8913e0ef0da4bb66aacd392df6195dab8`; Quick `87bf278e5b91dab62750f6417530367508d1f07d1ba435989c366ec9f01b0746`.

This report update changes the source tree. To avoid a self-referential endless metadata loop, the exact final metadata-normalized commit must pass once more; that final run identity is recorded in the external delivery evidence rather than mutating source after the final run.
"""
    report_path.write_text(report, encoding="utf-8")

    notice_path = ROOT / "PHASE_CANDIDATE_NOTICE.md"
    notice = notice_path.read_text(encoding="utf-8")
    if "30611637518" not in notice:
        notice += "\nGitHub run `30611637518` 已证明 B17j 第 4 轮的 Skill、Static、Quick、完整 HTTP 生命周期与 Chromium 产品旅程通过；元数据归一化后的精确最终提交仍须再跑一次，最终运行身份写入外部交付证据。\n"
    notice_path.write_text(notice, encoding="utf-8")


def regenerate_manifest() -> None:
    self_path = ROOT / SELF
    if self_path.exists():
        self_path.unlink()
    manifest_path = ROOT / "PHASE_CANDIDATE_MANIFEST.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in {"PHASE_CANDIDATE_MANIFEST.json", TEMP_WORKFLOW} or relative.startswith(".git/"):
            continue
        payload = path.read_bytes()
        files.append({"path": relative, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    manifest = {
        "schema_version": 1,
        "status": "PHASE_CANDIDATE_ENVIRONMENT_EXECUTION_PENDING",
        "phase": "B17j",
        "root_name": "customer_agent_workspace_v20_17_b17j_ci_profile_boundary_repair_phase_candidate_env_blocked_20260731",
        "file_count": len(files),
        "required_environment": old.get("required_environment", []),
        "production_closed": False,
        "files": files,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if len(files) != 1052:
        raise SystemExit(f"unexpected managed file count: {len(files)}")
    for entry in files:
        payload = (ROOT / entry["path"]).read_bytes()
        if len(payload) != entry["size"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise SystemExit(f"manifest verification failed: {entry['path']}")


def main() -> None:
    update_metadata()
    regenerate_manifest()
    print(json.dumps({"status": "PASS", "phase": "B17j", "file_count": 1052, "metadata": "normalized"}))


if __name__ == "__main__":
    main()
