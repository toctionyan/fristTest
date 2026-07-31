#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF_PATH = "scripts/b17j_round3_apply.py"
TEMP_WORKFLOW = ".github/workflows/b17j-round3-apply.yml"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"{label}: old contract not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_changelog() -> None:
    path = ROOT / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    if text.startswith("# V20.17 B17j"):
        return
    entry = """# V20.17 B17j — CI profile boundary and Harness isolation repair

- 真实 GitHub CI 将通用项目 `quality` Workflow 与 Skill-only 产品树兼容性 Gate 解耦；Skill-only release 仍保留 `project-compatibility-smoke`。
- 修复 B17e 反例桥接和受保护运行时职责断言，使反例继续指向当前唯一权威实现，而不是不存在的测试助手或已退休 Workflow 步骤。
- 修复 B17h/B17i 子进程测试继承 GitHub Runner 环境变量造成的场景污染；“无 CI 上下文”反例现在使用显式隔离环境。
- 产品 static/quick/integration/release Gate 未减少，客服语义、Prompt、Capability、事务、数据库与 RAG 行为未修改。
- 当前仍为 `PHASE_CANDIDATE_ENVIRONMENT_EXECUTION_PENDING`，尚未执行受保护生产认证，也未生成 `production_closed`。

"""
    path.write_text(entry + text, encoding="utf-8")


def patch_tests() -> None:
    b17h = ROOT / "services/agent-service/tests/runtime/test_b17h_protected_environment_preflight.py"
    text = b17h.read_text(encoding="utf-8")
    if "import os\n" not in text:
        text = text.replace("import json\n", "import json\nimport os\n", 1)
    old = '''def test_local_cli_writes_sanitized_environment_block(tmp_path: Path) -> None:
    output = tmp_path / "preflight.json"
    completed = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--workspace-root", str(ROOT), "--output", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )
'''
    new = '''def test_local_cli_writes_sanitized_environment_block(tmp_path: Path) -> None:
    output = tmp_path / "preflight.json"
    isolated_env = {
        key: value
        for key, value in os.environ.items()
        if key != "CI"
        and not key.startswith("GITHUB_")
        and not key.startswith("PRODUCTION_RELEASE_")
        and not key.startswith("RELEASE_INPUT_")
    }
    completed = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--workspace-root", str(ROOT), "--output", str(output)],
        env=isolated_env,
        text=True,
        capture_output=True,
        check=False,
    )
'''
    if new not in text:
        if old not in text:
            raise SystemExit("B17h no-CI subprocess block not found")
        text = text.replace(old, new, 1)
    b17h.write_text(text, encoding="utf-8")

    b17i = ROOT / "services/agent-service/tests/runtime/test_b17i_production_execution_handoff.py"
    replace_once(
        b17i,
        '''def _run(output: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ)
    merged.update(env)
''',
        '''def _run(output: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = {
        key: value
        for key, value in os.environ.items()
        if key != "CI"
        and not key.startswith("GITHUB_")
        and not key.startswith("PRODUCTION_RELEASE_")
        and not key.startswith("RELEASE_INPUT_")
    }
    merged.update(env)
''',
        "B17i subprocess environment isolation",
    )


def patch_governance() -> None:
    target = ROOT / "governance/targets/repair-v20.17-b17j-ci-profile-boundary.md"
    text = target.read_text(encoding="utf-8")
    anchor = "- `services/agent-service/tests/architecture/test_quality_loop_governance.py`\n"
    for line in (
        "- `services/agent-service/tests/runtime/test_b17h_protected_environment_preflight.py`\n",
        "- `services/agent-service/tests/runtime/test_b17i_production_execution_handoff.py`\n",
    ):
        if line not in text:
            text = text.replace(anchor, anchor + line, 1)
    text = text.replace("- 当前轮次：2\n", "- 当前轮次：3\n", 1)
    if "V20-17-B17J-CI-ENV-ISOLATION-003" not in text:
        text = text.replace(
            "- 验收 ID：`V20-17-B17J-CI-PROFILE-BOUNDARY-001`、`V20-17-B17J-ADVERSARIAL-HARNESS-002`\n",
            "- 验收 ID：`V20-17-B17J-CI-PROFILE-BOUNDARY-001`、`V20-17-B17J-ADVERSARIAL-HARNESS-002`、`V20-17-B17J-CI-ENV-ISOLATION-003`\n",
            1,
        )
    if "第三红基线：" not in text:
        text = text.replace(
            "## 修复轮次\n",
            "第三红基线：GitHub Actions run `30609735023` 中，`adversarial-runtime-counterexamples` 已 `137 passed`，前两轮修复得到验证；标准 `python-test-suites` 在 857 个 Agent 测试中仅剩 3 个失败：B17j Changelog 首段缺失，以及 B17h/B17i 的“无 CI 环境”子进程测试继承了 Runner 的 Workflow/GitHub 环境变量。\n\n## 修复轮次\n",
            1,
        )
    round2 = "- 第 2 轮：只修复两个过期反例桥接和一个过期发布职责断言，不触碰产品运行时。\n"
    round3 = "- 第 3 轮：补齐 B17j 当前阶段 Changelog，并隔离 B17h/B17i 无 CI 上下文子进程环境，不改变生产脚本失败优先级。\n"
    if round3 not in text:
        text = text.replace(round2, round2 + round3, 1)
    target.write_text(text, encoding="utf-8")

    claim_path = ROOT / "governance/claims/repair-v20.17-b17j-ci-profile-boundary.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    if not any(row.get("id") == "V20-17-B17J-CI-ENV-ISOLATION-003" for row in claim["claims"]):
        claim["claims"].append({
            "id": "V20-17-B17J-CI-ENV-ISOLATION-003",
            "statement": "Current-phase metadata identifies B17j, and subprocess counterexamples that claim to run without CI context explicitly isolate inherited GitHub and release variables.",
            "risk": "P1",
            "required_mode": "quick",
            "evidence_kind": "counterexample",
            "required_gates": ["python-test-suites"],
            "evidence_refs": [
                "services/agent-service/tests/runtime/test_b17g_production_execution_readiness.py::test_candidate_metadata_preserves_b17g_history_and_identifies_current_phase",
                "services/agent-service/tests/runtime/test_b17h_protected_environment_preflight.py::test_local_cli_writes_sanitized_environment_block",
                "services/agent-service/tests/runtime/test_b17i_production_execution_handoff.py::test_admission_cli_persists_environment_block_without_credentials"
            ],
            "owner": "Release Harness subprocess isolation and phase metadata authority",
            "closure_requirement": "regression-transition"
        })
    claim_path.write_text(json.dumps(claim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_path = ROOT / "B17J_STAGE_SUMMARY.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    ids = list(summary.get("claim_ids") or [])
    for cid in (
        "V20-17-B17J-CI-PROFILE-BOUNDARY-001",
        "V20-17-B17J-ADVERSARIAL-HARNESS-002",
        "V20-17-B17J-CI-ENV-ISOLATION-003",
    ):
        if cid not in ids:
            ids.append(cid)
    summary["claim_ids"] = ids
    summary["repair_round"] = 3
    summary["real_ci_python_suite_red_baseline"] = {
        "repository": "toctionyan/fristTest",
        "workflow": "quality",
        "run_id": 30609735023,
        "job_id": 91089703105,
        "adversarial_runtime_counterexamples": "PASS_137",
        "agent_tests": 857,
        "agent_passed": 854,
        "agent_failed": 3,
        "business_tests_passed": 28,
        "root_causes": [
            "CHANGELOG current-phase header remained B17i after phase moved to B17j",
            "B17h no-CI subprocess inherited GitHub Actions variables from the runner",
            "B17i no-credentials admission subprocess inherited workflow-level release variables"
        ]
    }
    summary["repair"]["round_3_repairs"] = [
        "prepend current B17j changelog metadata",
        "strip inherited CI/GITHUB_/PRODUCTION_RELEASE_/RELEASE_INPUT_ variables for explicit no-CI subprocess scenarios"
    ]
    summary["validation"].update({
        "github_round_2_adversarial_counterexamples": "PASS_137",
        "github_round_2_python_standard": "FAIL_3_OF_857",
        "round_3_targeted_tests": "PASS_3",
        "b17_release_control_regressions": "PASS_86",
        "skill_unit_tests": 48,
        "skill_security_tests": 7,
        "skill_static": "PASS",
        "skill_host_integration": "PASS",
        "version_consistency": "PASS",
        "architecture": "PASS",
        "architecture_debt": "RESOLVED",
        "quality_evidence_contract": "PASS",
        "workflow_yaml": "PASS",
        "github_ci_status": "PENDING_ROUND_3_RETRY"
    })
    summary["required_next_action"] = "rerun GitHub PR quality after round-3 metadata and subprocess-environment isolation repair"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = ROOT / "release/VALIDATION_REPORT.md"
    text = report.read_text(encoding="utf-8")
    if "## GitHub standard Python red baseline and round 3" not in text:
        text += """
## GitHub standard Python red baseline and round 3

Run `30609735023` verified the round-2 repair with `137 passed` in `adversarial-runtime-counterexamples`. The standard Agent suite then reported only 3 failures out of 857 tests; Business reported 28 passed. One failure was stale current-phase Changelog metadata. The other two were false scenario contamination: tests intended to prove behavior outside CI inherited GitHub Actions and Workflow-level release variables from the parent Runner.

Round 3 updates metadata and makes the no-CI subprocess tests construct an isolated environment. Production Preflight and Admission code remains unchanged, preserving real fail-closed ordering.
"""
    report.write_text(text, encoding="utf-8")

    architecture = ROOT / "docs/architecture/V20_17_B17J_CI_PROFILE_BOUNDARY_REPAIR.md"
    text = architecture.read_text(encoding="utf-8")
    if "## 第 3 轮：Runner 环境隔离" not in text:
        text += """
## 第 3 轮：Runner 环境隔离

GitHub run `30609735023` 证明第 2 轮三个反例已全部修复（`137 passed`）。标准全量测试剩余的三个失败中，一个是 B17j Changelog 首段未同步，两个是测试场景污染：声明“无 CI 上下文”的子进程继承了父 GitHub Runner 的环境。

正确修复是在测试 Harness 构造明确的隔离环境，而不是修改生产 Preflight/Admission 的错误优先级。这样真实 Workflow 仍按 fail-closed 顺序裁决，离线反例也能稳定复现目标状态。
"""
    architecture.write_text(text, encoding="utf-8")

    notice = ROOT / "PHASE_CANDIDATE_NOTICE.md"
    text = notice.read_text(encoding="utf-8")
    line = "\nB17j 第 3 轮补齐当前阶段 Changelog，并隔离两个“无 CI 上下文”子进程测试继承的 GitHub Runner 变量；生产发布合同未修改。\n"
    if "B17j 第 3 轮补齐当前阶段 Changelog" not in text:
        text += line
    notice.write_text(text, encoding="utf-8")

    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    line = "- B17j 第 3 轮补齐当前阶段 Changelog，并隔离“无 CI 上下文”子进程测试继承的 GitHub Runner 变量；生产 Preflight/Admission 未修改。\n"
    anchor = "- 产品 Quality Loop、客服运行时和生产关单边界均未降低。\n"
    if line not in text:
        text = text.replace(anchor, anchor + line, 1)
    readme.write_text(text, encoding="utf-8")


def regenerate_manifest() -> None:
    self_file = ROOT / SELF_PATH
    if self_file.exists():
        self_file.unlink()
    manifest_path = ROOT / "PHASE_CANDIDATE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in {"PHASE_CANDIDATE_MANIFEST.json", TEMP_WORKFLOW} or relative.startswith(".git/"):
            continue
        payload = path.read_bytes()
        files.append({"path": relative, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    manifest.update({
        "schema_version": 1,
        "status": "PHASE_CANDIDATE_ENVIRONMENT_EXECUTION_PENDING",
        "phase": "B17j",
        "root_name": "customer_agent_workspace_v20_17_b17j_ci_profile_boundary_repair_phase_candidate_env_blocked_20260731",
        "file_count": len(files),
        "production_closed": False,
        "files": files
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if len(files) != 1052:
        raise SystemExit(f"unexpected managed file count: {len(files)}")


def main() -> None:
    patch_changelog()
    patch_tests()
    patch_governance()
    regenerate_manifest()
    print(json.dumps({"status": "PASS", "phase": "B17j", "round": 3, "file_count": 1052}))


if __name__ == "__main__":
    main()
