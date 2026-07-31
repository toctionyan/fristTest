#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP_WORKFLOW = ".github/workflows/b17j-round2-apply.yml"
SELF_PATH = "scripts/b17j_round2_apply.py"


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"{label}: expected old contract was not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_tests() -> None:
    counter = ROOT / "services/agent-service/tests/runtime/test_goal_binding_counterexamples.py"
    replace_once(
        counter,
        '''def test_b17e_mutable_postgres_image_is_rejected() -> None:\n    module = _load_test_module(\n        "test_b17e_release_supply_chain_authority_counterexample_image",\n        "test_b17e_release_supply_chain_authority.py",\n    )\n    # Static source assertion is a deterministic red-path guard for mutable image tags.\n    module.test_protected_postgres_image_is_immutable_and_digest_locked()\n\n\ndef test_b17e_cross_container_image_bundle_is_rejected() -> None:\n    module = _load_test_module(\n        "test_b17e_release_supply_chain_authority_counterexample_cross_image",\n        "test_b17e_release_supply_chain_authority.py",\n    )\n    module.test_postgres_and_browser_from_different_container_images_cannot_form_bundle()\n''',
        '''def test_b17e_mutable_postgres_image_is_rejected() -> None:\n    from tests.runtime.test_b17e_release_supply_chain_authority import (\n        test_protected_postgres_image_is_immutable_and_digest_locked,\n    )\n\n    # Static source assertion is a deterministic red-path guard for mutable image tags.\n    test_protected_postgres_image_is_immutable_and_digest_locked()\n\n\ndef test_b17e_cross_container_image_bundle_is_rejected() -> None:\n    from tests.runtime.test_b17e_release_supply_chain_authority import (\n        test_postgres_and_browser_from_different_container_images_cannot_form_bundle,\n    )\n\n    test_postgres_and_browser_from_different_container_images_cannot_form_bundle()\n''',
        label="B17e adversarial bridges",
    )

    governance = ROOT / "services/agent-service/tests/architecture/test_quality_loop_governance.py"
    replace_once(
        governance,
        '''def test_protected_release_starts_the_services_with_the_protected_contract() -> None:\n    root = workspace_root(__file__)\n    workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")\n    protected = workflow.split("  protected-release:", 1)[1]\n    start_step = protected.split("- name: Start actual protected-profile services", 1)[1].split(\n        "- name: Run every release gate", 1\n    )[0]\n\n    assert "APP_PROFILE: local" not in protected\n    for required in (\n        "APP_PROFILE: preprod",\n        "BUSINESS_DB_BACKEND: postgres",\n        "BUSINESS_DATABASE_URL: postgresql://",\n        "BUSINESS_REQUIRE_ACTOR_SIGNATURE: 'true'",\n        "AGENT_AUTH_PROVIDER: jwt_hs256",\n        "AGENT_DB_BACKEND: postgres",\n        "CHECKPOINT_BACKEND: postgres",\n        "RAG_BACKEND: pgvector",\n        "DOCUMENT_JOB_BACKEND: sqlalchemy",\n        "DOCUMENT_OBJECT_STORE_BACKEND: shared_filesystem",\n        "STATE_CONTRACT_MODE: strict",\n        "CAPABILITY_SEMANTIC_VERIFIER_MODE: model",\n        "GOAL_ALIGNMENT_VERIFIER_MODE: model",\n        "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE: model",\n    ):\n        assert required in start_step\n''',
        '''def test_protected_release_delegates_service_startup_to_the_certification_bundle() -> None:\n    root = workspace_root(__file__)\n    workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")\n    protected = workflow.split("  protected-release:", 1)[1]\n    runtime_owner = (root / "scripts" / "verify_full_lifecycle_canary.py").read_text(encoding="utf-8")\n    certification_bundle = (\n        root / "scripts" / "verify_production_certification_bundle.py"\n    ).read_text(encoding="utf-8")\n\n    assert "APP_PROFILE: local" not in protected\n    assert "Start actual protected-profile services" not in protected\n    assert "Validate protected runtime prerequisites" in protected\n    assert "scripts/run_production_release.py" in protected\n    assert "scripts/verify_production_certification_bundle.py" in protected\n    assert '"browser": SCRIPTS / "verify_production_browser_bundle.py"' in certification_bundle\n\n    for required in (\n        '"APP_PROFILE": "preprod"',\n        '"BUSINESS_DB_BACKEND": "postgres"',\n        '"BUSINESS_DATABASE_URL": self.persistence_url',\n        '"BUSINESS_REQUIRE_ACTOR_SIGNATURE": "true"',\n        '"AGENT_AUTH_PROVIDER": "jwt_hs256"',\n        '"AGENT_DB_BACKEND": "postgres"',\n        '"CHECKPOINT_BACKEND": "postgres"',\n        '"RAG_BACKEND": "pgvector"',\n        '"DOCUMENT_JOB_BACKEND": "sqlalchemy"',\n        '"DOCUMENT_OBJECT_STORE_BACKEND": "shared_filesystem"',\n        '"STATE_CONTRACT_MODE": "strict"',\n        '"CAPABILITY_SEMANTIC_VERIFIER_MODE": "model"',\n        '"GOAL_ALIGNMENT_VERIFIER_MODE": "model"',\n        '"ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model"',\n    ):\n        assert required in runtime_owner\n''',
        label="protected runtime ownership assertion",
    )


def patch_governance() -> None:
    target = ROOT / "governance/targets/repair-v20.17-b17j-ci-profile-boundary.md"
    text = target.read_text(encoding="utf-8")
    if "services/agent-service/tests/runtime/test_goal_binding_counterexamples.py" not in text:
        text = text.replace(
            "- `skill-system/tests/test_ci_profile_boundary.py`\n",
            "- `skill-system/tests/test_ci_profile_boundary.py`\n"
            "- `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`\n"
            "- `services/agent-service/tests/architecture/test_quality_loop_governance.py`\n",
            1,
        )
    text = text.replace(
        "- 验收 ID：`V20-17-B17J-CI-PROFILE-BOUNDARY-001`\n",
        "- 验收 ID：`V20-17-B17J-CI-PROFILE-BOUNDARY-001`、`V20-17-B17J-ADVERSARIAL-HARNESS-002`\n",
        1,
    )
    text = text.replace(
        "项目 CI 必须运行四个 Skill 自身 Profile，而不运行 Skill-only 产品树兼容性 Gate；Skill-only release 必须继续包含兼容性 Gate。净零差异 GitHub PR 必须越过 Skill 自检 Job，继续进入项目 static/quick Gate。\n",
        "项目 CI 必须运行四个 Skill 自身 Profile，而不运行 Skill-only 产品树兼容性 Gate；Skill-only release 必须继续包含兼容性 Gate。净零差异 GitHub PR 必须越过 Skill 自检和 static，并且 Quick 的 adversarial-runtime-counterexamples 不得再因缺失测试助手或已退休 Workflow 步骤而失败。\n",
        1,
    )
    first_baseline = (
        "红基线：GitHub Actions run `30607885939`，Job `91084022386`。四个 Skill 自检均 PASS，"
        "`project-compatibility-smoke` 因当前产品候选与历史 Skill-only 基线不同而 FAIL，导致后续项目质量 Job 全部无法启动。\n"
    )
    if "第二红基线：" not in text:
        text = text.replace(
            first_baseline,
            first_baseline.replace("红基线：", "第一红基线：")
            + "\n第二红基线：修复 Profile 边界后的 GitHub Actions run `30608910835` 中，"
            "`skill-self-validation` 与 `quality-static` 已 PASS；Quick Job `91087188820` 在 "
            "`adversarial-runtime-counterexamples` 暴露三个过期 Harness 断言：两个 B17e 桥接测试调用不存在的 "
            "`_load_test_module`，一个架构测试仍要求已经退休的 `Start actual protected-profile services` Workflow 步骤。\n",
            1,
        )
    text = text.replace("- 当前轮次：1\n", "- 当前轮次：2\n", 1)
    text = text.replace(
        "- 失败后：只修复 CI Profile 编排和对应合同测试，不触碰产品运行时。\n",
        "- 第 2 轮：只修复两个过期反例桥接和一个过期发布职责断言，不触碰产品运行时。\n",
        1,
    )
    target.write_text(text, encoding="utf-8")

    claim_path = ROOT / "governance/claims/repair-v20.17-b17j-ci-profile-boundary.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    if not any(row.get("id") == "V20-17-B17J-ADVERSARIAL-HARNESS-002" for row in claim["claims"]):
        claim["claims"].append({
            "id": "V20-17-B17J-ADVERSARIAL-HARNESS-002",
            "statement": "The adversarial runtime suite invokes B17e counterexamples through valid imports and asserts the current controller-owned protected runtime boundary instead of a retired workflow service-start step.",
            "risk": "P1",
            "required_mode": "quick",
            "evidence_kind": "counterexample",
            "required_gates": ["adversarial-runtime-counterexamples"],
            "evidence_refs": [
                "services/agent-service/tests/runtime/test_goal_binding_counterexamples.py::test_b17e_mutable_postgres_image_is_rejected",
                "services/agent-service/tests/runtime/test_goal_binding_counterexamples.py::test_b17e_cross_container_image_bundle_is_rejected",
                "services/agent-service/tests/architecture/test_quality_loop_governance.py::test_protected_release_delegates_service_startup_to_the_certification_bundle",
            ],
            "owner": "Adversarial test harness authority",
            "closure_requirement": "regression-transition",
        })
    claim_path.write_text(json.dumps(claim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_path = ROOT / "B17J_STAGE_SUMMARY.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["claim_ids"] = [
        "V20-17-B17J-CI-PROFILE-BOUNDARY-001",
        "V20-17-B17J-ADVERSARIAL-HARNESS-002",
    ]
    summary.pop("claim_id", None)
    summary["repair_round"] = 2
    summary["real_ci_quick_red_baseline"] = {
        "repository": "toctionyan/fristTest",
        "workflow": "quality",
        "run_id": 30608910835,
        "job_id": 91087188820,
        "skill_self_validation": "PASS",
        "quality_static": "PASS",
        "adversarial_runtime_counterexamples": "FAIL",
        "passed_before_failure": 134,
        "failed": 3,
        "root_causes": [
            "two B17e bridge tests called an undefined _load_test_module helper",
            "one architecture test asserted a retired workflow-owned service startup step instead of the current certification-bundle owner",
        ],
    }
    summary["repair"]["adversarial_harness_repairs"] = [
        "replace undefined dynamic loader calls with direct test imports",
        "assert release workflow delegation and verify_full_lifecycle_canary protected runtime ownership",
    ]
    summary["validation"].update({
        "targeted_round_2_contracts": "PASS",
        "targeted_round_2_count": 3,
        "verified_agent_pytest_runtime": "BLOCKED_BY_ENVIRONMENT",
        "verified_agent_pytest_runtime_reason": "locked Agent virtual environment is absent locally; GitHub Quick retry is authoritative",
        "github_ci_status": "PENDING_ROUND_2_RETRY",
    })
    summary["required_next_action"] = (
        "rerun GitHub PR quality Quick after round-2 harness repair; merge only after all PR quality jobs pass, "
        "then execute protected production certification"
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_path = ROOT / "release/VALIDATION_REPORT.md"
    report = report_path.read_text(encoding="utf-8")
    if "## GitHub Quick red baseline and round 2" not in report:
        report += (
            "\n## GitHub Quick red baseline and round 2\n\n"
            "Run `30608910835` proved the Profile-boundary repair: `skill-self-validation` and `quality-static` passed. "
            "Quick Job `91087188820` then failed only three stale adversarial Harness tests while 134 tests in that gate passed. "
            "Two bridges called an undefined `_load_test_module`; the third expected Workflow-owned service startup that B17d had "
            "deliberately retired in favor of the production certification bundle.\n\n"
            "Round 2 replaces the missing-loader calls with direct imports of the authoritative B17e counterexamples. The stale "
            "architecture assertion now proves that `release.yml` delegates to `run_production_release.py` / "
            "`verify_production_certification_bundle.py`, while `verify_full_lifecycle_canary.py` owns the protected preprod service "
            "contract. Three targeted deterministic contracts and Python compilation pass locally. A locked Agent pytest runtime is "
            "absent locally, so the GitHub Quick retry remains the authoritative closure test.\n"
        )
    report_path.write_text(report, encoding="utf-8")

    notice_path = ROOT / "PHASE_CANDIDATE_NOTICE.md"
    notice = notice_path.read_text(encoding="utf-8")
    old_notice = (
        "Project CI now runs Skill static, unit, host-integration and security profiles directly. The Skill-only compatibility "
        "guard remains unchanged inside `skill-control-plane` and `skill-release`. No customer-agent runtime behavior changed.\n"
    )
    new_notice = (
        "Project CI now runs Skill static, unit, host-integration and security profiles directly. The Skill-only compatibility "
        "guard remains unchanged inside `skill-control-plane` and `skill-release`. GitHub run `30608910835` proved this boundary "
        "and then exposed three stale adversarial Harness tests; B17j round 2 repairs only those test contracts. No customer-agent "
        "runtime behavior changed.\n"
    )
    if old_notice in notice:
        notice = notice.replace(old_notice, new_notice, 1)
    notice_path.write_text(notice, encoding="utf-8")

    architecture_path = ROOT / "docs/architecture/V20_17_B17J_CI_PROFILE_BOUNDARY_REPAIR.md"
    architecture = architecture_path.read_text(encoding="utf-8")
    if "## 第 2 轮：真实 Quick Harness 收敛" not in architecture:
        architecture += (
            "\n## 第 2 轮：真实 Quick Harness 收敛\n\n"
            "GitHub run `30608910835` 已证明新的 Profile 边界正确：Skill 自检与 static 均 PASS。Quick 的唯一红项位于 "
            "`adversarial-runtime-counterexamples`：两个桥接测试引用不存在的加载助手，一个测试仍把受保护服务启动职责错误地放在 GitHub Workflow。\n\n"
            "修复保持唯一权威：B17e 反例通过 Python 直接 import 调用原始权威测试；发布职责测试证明 Workflow 只委托 "
            "`run_production_release.py` 和 `verify_production_certification_bundle.py`，而真正的 preprod 服务环境由 "
            "`verify_full_lifecycle_canary.py` 动态拥有。没有把旧步骤加回 Workflow，也没有降低反例。\n"
        )
    architecture_path.write_text(architecture, encoding="utf-8")


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
        files.append({
            "path": relative,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    manifest.update({
        "schema_version": 1,
        "status": "PHASE_CANDIDATE_ENVIRONMENT_EXECUTION_PENDING",
        "phase": "B17j",
        "root_name": "customer_agent_workspace_v20_17_b17j_ci_profile_boundary_repair_phase_candidate_env_blocked_20260731",
        "file_count": len(files),
        "production_closed": False,
        "files": files,
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if len(files) != 1052:
        raise SystemExit(f"unexpected managed file count: {len(files)}")


def main() -> None:
    patch_tests()
    patch_governance()
    regenerate_manifest()
    print(json.dumps({"status": "PASS", "phase": "B17j", "round": 2, "file_count": 1052}))


if __name__ == "__main__":
    main()
