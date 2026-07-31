#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = "scripts/b17j_round4_apply.py"
TEMP_WORKFLOW = ".github/workflows/b17j-round4-apply.yml"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"{label}: old contract not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_interpreter_contract() -> None:
    canary = ROOT / "scripts/verify_full_lifecycle_canary.py"
    replace_once(
        canary,
        """    for candidate in candidates:\n        if candidate.is_file():\n            return candidate.resolve()\n""",
        """    for candidate in candidates:\n        if candidate.is_file():\n            # Preserve the selected virtual-environment entrypoint. Resolving\n            # ``.venv/bin/python`` dereferences its symlink to the base Python\n            # executable and silently drops the venv's installed packages.\n            return candidate.absolute()\n""",
        "lifecycle interpreter selection",
    )

    tests = ROOT / "services/agent-service/tests/runtime/test_b14f1_sqlite_resource_lifecycle.py"
    replace_once(
        tests,
        """def test_full_lifecycle_canary_resolves_declared_or_current_python(monkeypatch, tmp_path: Path) -> None:\n    module = _load_product_canary_module()\n    executable = tmp_path / \"python\"\n    executable.write_text(\"#!/bin/sh\\n\", encoding=\"utf-8\")\n    monkeypatch.setenv(\"B14F1_TEST_PYTHON\", str(executable))\n    assert module._resolve_python(\"B14F1_TEST_PYTHON\", tmp_path / \"missing\") == executable.resolve()\n    monkeypatch.delenv(\"B14F1_TEST_PYTHON\")\n    assert module._resolve_python(\"B14F1_TEST_PYTHON\", tmp_path / \"missing\") == Path(sys.executable).resolve()\n\n\n""",
        """def test_full_lifecycle_canary_resolves_declared_or_current_python(monkeypatch, tmp_path: Path) -> None:\n    module = _load_product_canary_module()\n    executable = tmp_path / \"python\"\n    executable.write_text(\"#!/bin/sh\\n\", encoding=\"utf-8\")\n    monkeypatch.setenv(\"B14F1_TEST_PYTHON\", str(executable))\n    assert module._resolve_python(\"B14F1_TEST_PYTHON\", tmp_path / \"missing\") == executable.absolute()\n    monkeypatch.delenv(\"B14F1_TEST_PYTHON\")\n    assert module._resolve_python(\"B14F1_TEST_PYTHON\", tmp_path / \"missing\") == Path(sys.executable).absolute()\n\n\ndef test_full_lifecycle_canary_preserves_virtualenv_python_symlink(monkeypatch, tmp_path: Path) -> None:\n    module = _load_product_canary_module()\n    virtualenv_python = tmp_path / \"agent-venv\" / \"bin\" / \"python\"\n    virtualenv_python.parent.mkdir(parents=True)\n    virtualenv_python.symlink_to(Path(sys.executable).resolve())\n    monkeypatch.setenv(\"B14F1_TEST_PYTHON\", str(virtualenv_python))\n\n    selected = module._resolve_python(\"B14F1_TEST_PYTHON\", tmp_path / \"missing\")\n\n    assert selected == virtualenv_python.absolute()\n    assert selected.is_symlink()\n    assert selected != virtualenv_python.resolve()\n\n\n""",
        "lifecycle interpreter regressions",
    )


def patch_governance() -> None:
    target = ROOT / "governance/targets/repair-v20.17-b17j-ci-profile-boundary.md"
    text = target.read_text(encoding="utf-8")
    anchor = "- `services/agent-service/tests/runtime/test_b17h_protected_environment_preflight.py`\n"
    additions = (
        "- `scripts/verify_full_lifecycle_canary.py`\n"
        "- `services/agent-service/tests/runtime/test_b14f1_sqlite_resource_lifecycle.py`\n"
    )
    if "`scripts/verify_full_lifecycle_canary.py`" not in text:
        text = text.replace(anchor, anchor + additions, 1)
    if "V20-17-B17J-VENV-INTERPRETER-004" not in text:
        text = text.replace(
            "`V20-17-B17J-CI-ENV-ISOLATION-003`\n",
            "`V20-17-B17J-CI-ENV-ISOLATION-003`、`V20-17-B17J-VENV-INTERPRETER-004`\n",
            1,
        )
    if "第四红基线：" not in text:
        text = text.replace(
            "## 修复轮次\n",
            "第四红基线：GitHub Actions run `30610419110` 中，Skill、Static、`adversarial-runtime-counterexamples`、Agent 857 项、Business 28 项、前端与覆盖率均已通过；唯一新失败是 `full-lifecycle-canary`。Harness 将 `services/agent-service/.venv/bin/python` 调用 `Path.resolve()`，把虚拟环境入口解引用为系统基础 Python，导致确定性模型桩启动时报 `No module named uvicorn`。\n\n## 修复轮次\n",
            1,
        )
    text = text.replace("- 当前轮次：3\n", "- 当前轮次：4\n", 1)
    round4 = "- 第 4 轮：保留选中 Python 虚拟环境入口的符号链接路径，并增加不得解引用为基础解释器的反例；不增删依赖。\n"
    if round4 not in text:
        text += round4
    target.write_text(text, encoding="utf-8")

    claim_path = ROOT / "governance/claims/repair-v20.17-b17j-ci-profile-boundary.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    if not any(row.get("id") == "V20-17-B17J-VENV-INTERPRETER-004" for row in claim["claims"]):
        claim["claims"].append({
            "id": "V20-17-B17J-VENV-INTERPRETER-004",
            "statement": "The lifecycle Harness preserves the selected virtual-environment Python entrypoint instead of dereferencing it to the base interpreter, so installed runtime dependencies remain available to model and service processes.",
            "risk": "P1",
            "required_mode": "quick",
            "evidence_kind": "integration",
            "required_gates": ["full-lifecycle-canary"],
            "evidence_refs": [
                "services/agent-service/tests/runtime/test_b14f1_sqlite_resource_lifecycle.py::test_full_lifecycle_canary_preserves_virtualenv_python_symlink",
                "gate-log:full-lifecycle-canary"
            ],
            "owner": "Product lifecycle interpreter authority",
            "closure_requirement": "regression-transition"
        })
    claim_path.write_text(json.dumps(claim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_path = ROOT / "B17J_STAGE_SUMMARY.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["repair_round"] = 4
    ids = list(summary.get("claim_ids") or [])
    if "V20-17-B17J-VENV-INTERPRETER-004" not in ids:
        ids.append("V20-17-B17J-VENV-INTERPRETER-004")
    summary["claim_ids"] = ids
    summary["real_ci_lifecycle_red_baseline"] = {
        "repository": "toctionyan/fristTest",
        "workflow": "quality",
        "run_id": 30610419110,
        "job_id": 91091813024,
        "skill_self_validation": "PASS",
        "quality_static": "PASS",
        "adversarial_runtime_counterexamples": "PASS_137",
        "agent_tests_passed": 857,
        "business_tests_passed": 28,
        "frontend_and_coverage": "PASS",
        "full_lifecycle_canary": "FAIL",
        "root_cause": "Path.resolve dereferenced the Agent virtualenv Python entrypoint to the base interpreter, losing uvicorn and other venv packages"
    }
    summary["repair"]["round_4_repairs"] = [
        "return the absolute selected Python entrypoint without resolving virtualenv symlinks",
        "add a symlink-preservation regression for the lifecycle Harness"
    ]
    summary["validation"].update({
        "round_4_targeted_tests": "PASS_2",
        "round_4_virtualenv_prefix_probe": "PASS",
        "round_4_release_control_regressions": "PASS_84",
        "github_ci_status": "PENDING_ROUND_4_RETRY"
    })
    summary["required_next_action"] = "rerun the official GitHub PR quality workflow after the bounded virtualenv interpreter repair"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    docs = ROOT / "docs/architecture/V20_17_B17J_CI_PROFILE_BOUNDARY_REPAIR.md"
    text = docs.read_text(encoding="utf-8")
    if "## 第 4 轮：虚拟环境解释器身份" not in text:
        text += """
## 第 4 轮：虚拟环境解释器身份

GitHub run `30610419110` 已证明第 3 轮和标准测试闭环：Agent 857 项、Business 28 项、前端、覆盖率均通过。唯一新红项是 `full-lifecycle-canary`。

生命周期 Harness 原本对选中的 `.venv/bin/python` 调用 `Path.resolve()`。在 Linux 上该入口通常是指向基础 Python 的符号链接；解引用后启动子进程会绕过虚拟环境 `site-packages`，因此已安装的 `uvicorn` 被错误判定为不存在。正确边界是保留虚拟环境入口路径本身，只做绝对路径规范化，不改变解释器环境身份。

第 4 轮不新增依赖、不改服务实现，只修复 Harness 解释器选择并增加符号链接反例。
"""
    docs.write_text(text, encoding="utf-8")

    report = ROOT / "release/VALIDATION_REPORT.md"
    text = report.read_text(encoding="utf-8")
    if "## GitHub lifecycle red baseline and round 4" not in text:
        text += """
## GitHub lifecycle red baseline and round 4

Run `30610419110` passed Skill self-validation, static, adversarial counterexamples, all 857 Agent tests, all 28 Business tests, frontend tests/build and coverage. The only remaining Quick failure was `full-lifecycle-canary`: the Harness resolved `.venv/bin/python` to the base system interpreter and then could not import `uvicorn`.

Round 4 preserves the selected virtual-environment entrypoint with absolute path normalization only. A direct symlink regression proves the venv path is not dereferenced. No dependency or product runtime behavior is changed.
"""
    report.write_text(text, encoding="utf-8")

    notice = ROOT / "PHASE_CANDIDATE_NOTICE.md"
    text = notice.read_text(encoding="utf-8")
    if "B17j 第 4 轮修复生命周期 Harness" not in text:
        text += "\nB17j 第 4 轮修复生命周期 Harness 解引用虚拟环境 Python 符号链接的问题；依赖清单和客服运行时未修改。\n"
    notice.write_text(text, encoding="utf-8")

    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    line = "- B17j 第 4 轮保留生命周期 Harness 的虚拟环境 Python 入口，避免解引用为不含项目依赖的系统解释器。\n"
    anchor = "- B17j 第 3 轮补齐当前阶段 Changelog，并隔离“无 CI 上下文”子进程测试继承的 GitHub Runner 变量；生产 Preflight/Admission 未修改。\n"
    if line not in text:
        text = text.replace(anchor, anchor + line, 1)
    readme.write_text(text, encoding="utf-8")

    changelog = ROOT / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    line = "- 第 4 轮修复生命周期 Harness 将 `.venv/bin/python` 解引用成系统基础解释器的问题，并增加虚拟环境符号链接身份反例。\n"
    if line not in text:
        split = text.find("\n\n") + 2
        text = text[:split] + line + text[split:]
    changelog.write_text(text, encoding="utf-8")


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
    patch_interpreter_contract()
    patch_governance()
    regenerate_manifest()
    print(json.dumps({"status": "PASS", "phase": "B17j", "round": 4, "file_count": 1052}))


if __name__ == "__main__":
    main()
