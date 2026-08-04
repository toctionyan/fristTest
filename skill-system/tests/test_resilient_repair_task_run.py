from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _temporary_quality_target(tmp_path: Path, *, target_id: str) -> tuple[Path, Path, Path]:
    workspace = tmp_path
    (workspace / "governance/claims").mkdir(parents=True)
    (workspace / "VERSION").write_text("test\n", encoding="utf-8")
    (workspace / "source.txt").write_text("baseline\n", encoding="utf-8")
    claims = {
        "schema_version": 1,
        "target_id": target_id,
        "claims": [
            {
                "id": "TEST-REPAIR-001",
                "statement": "The temporary gate proves the interrupted repair transition.",
                "risk": "P2",
                "required_mode": "static",
                "evidence_kind": "static-contract",
                "required_gates": ["proof"],
                "evidence_refs": ["gate-log:proof"],
                "owner": "task-run-tests",
                "closure_requirement": "regression-transition",
            }
        ],
    }
    (workspace / "governance/claims/test.json").write_text(
        json.dumps(claims), encoding="utf-8"
    )
    target = workspace / "target.md"
    target.write_text(
        f"""# 目标
- 目标 ID：{target_id}
- 变更标识：temporary-change
- 执行上下文：local-change
- 目标类型：repair

验证可恢复修复任务。

## 允许范围
- 允许变更路径：source.txt
- 新增抽象记录：无

## 禁止范围
不修改其他文件。

## 验收条件
- 最低质量模式：static
- 声明清单：governance/claims/test.json
- 验收 ID：TEST-REPAIR-001

## 基线
修复前 baseline。

## 修复轮次
- 最大轮次：2
- 当前轮次：1
- 失败后：修改唯一 Owner。
""",
        encoding="utf-8",
    )
    policy = workspace / "governance/policy.json"
    policy.write_text(
        json.dumps(
            {
                "version": "test",
                "steps": [
                    {
                        "id": "proof",
                        "name": "proof",
                        "modes": ["static"],
                        "kind": "shell",
                        "argv": [
                            sys.executable,
                            "-S",
                            "-c",
                            "from pathlib import Path; import sys; "
                            "sys.exit(0 if Path('source.txt').read_text(encoding='utf-8').strip() == 'repaired' else 1)",
                        ],
                        "owner": "task-run-tests",
                        "category": "test",
                        "blocking_level": "required",
                        "repair_playbook": "repair source.txt",
                        "rerun_contract": "dependency_closure_then_downstream",
                        "depends_on": [],
                        "environment": {},
                        "timeout_seconds": 20,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return workspace, policy, target


def _install_orchestrator(workspace: Path) -> None:
    for script_name in ("quality_loop.py", "source_paths.py", "repair_loop.py"):
        source = ROOT / "scripts" / script_name
        destination = workspace / "scripts" / script_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copytree(ROOT / "scripts/quality_control", workspace / "scripts/quality_control")
    orchestrator = _load_script("repair_loop.py")
    for controller_name in orchestrator.REPAIR_RUNTIME_CONTROLLER_FILES:
        source = ROOT / "skill-system/controller" / controller_name
        destination = workspace / "skill-system/controller" / controller_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    trusted_judge_path = workspace / "skill-system/controller/trusted_judge.py"
    spec = importlib.util.spec_from_file_location("temporary_trusted_judge", trusted_judge_path)
    assert spec and spec.loader
    trusted_judge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trusted_judge)
    trusted_judge.write_manifest(workspace)


def _baseline(workspace: Path, policy: Path, target: Path) -> Path:
    controller = _load_script("quality_loop.py")
    baseline = workspace / ".quality/baseline"
    summary = controller.run_loop(
        workspace,
        policy,
        mode="static",
        evidence_dir=baseline,
        rerun_from=None,
        target_path=target,
        baseline=True,
        baseline_evidence=None,
        prior_evidence=None,
        state_dir=workspace / ".quality/state",
    )
    assert summary["decision"] == controller.FAIL
    return baseline


def _repair_command(
    workspace: Path,
    policy: Path,
    target: Path,
    baseline: Path,
    fixer: Path,
) -> list[str]:
    return [
        sys.executable,
        "-B",
        str(workspace / "scripts/repair_loop.py"),
        "--workspace-root",
        str(workspace),
        "--policy",
        str(policy),
        "--target",
        str(target),
        "--baseline-evidence",
        str(baseline),
        "--mode",
        "static",
        "--state-dir",
        ".quality/state",
        "--evidence-root",
        ".quality/evidence",
        "--issues-file",
        ".quality/issues.json",
        "--fix-command",
        sys.executable,
        str(fixer),
    ]


def test_repair_loop_resumes_after_failed_fixer_that_changed_source(tmp_path: Path) -> None:
    workspace, policy, target = _temporary_quality_target(
        tmp_path,
        target_id="repair-resume-after-interruption",
    )
    _install_orchestrator(workspace)
    baseline = _baseline(workspace, policy, target)

    interrupted = workspace / ".quality/fixers/interrupted_fixer.py"
    interrupted.parent.mkdir(parents=True, exist_ok=True)
    interrupted.write_text(
        "from pathlib import Path\n"
        "Path('.quality/fixer-count.txt').write_text('1\\n', encoding='utf-8')\n"
        "Path('source.txt').write_text('repaired\\n', encoding='utf-8')\n"
        "raise SystemExit(9)\n",
        encoding="utf-8",
    )
    first = subprocess.run(
        _repair_command(workspace, policy, target, baseline, interrupted),
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    assert first.returncode == 9, first.stderr
    task_run_path = next((workspace / ".quality/task-runs").glob("*.json"))
    state = json.loads(task_run_path.read_text(encoding="utf-8"))
    assert state["status"] == "FAILED_RECOVERABLE"
    assert state["phase"] == "FIXER_FAILED"

    must_not_run = workspace / ".quality/fixers/must_not_rerun_fixer.py"
    must_not_run.write_text(
        "from pathlib import Path\n"
        "Path('.quality/fixer-count.txt').write_text('2\\n', encoding='utf-8')\n"
        "raise SystemExit(99)\n",
        encoding="utf-8",
    )
    resumed = subprocess.run(
        _repair_command(workspace, policy, target, baseline, must_not_run),
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert (workspace / ".quality/fixer-count.txt").read_text(encoding="utf-8") == "1\n"
    completed = json.loads(task_run_path.read_text(encoding="utf-8"))
    assert completed["status"] == "COMPLETED"
    assert all(row["satisfied"] for row in completed["conditions"].values())


def test_repair_loop_reconciles_process_crash_after_fixer_mutation(tmp_path: Path) -> None:
    workspace, policy, target = _temporary_quality_target(
        tmp_path,
        target_id="repair-reconcile-crashed-fixer",
    )
    _install_orchestrator(workspace)
    baseline = _baseline(workspace, policy, target)

    crashing = workspace / ".quality/fixers/crashing_fixer.py"
    crashing.parent.mkdir(parents=True, exist_ok=True)
    crashing.write_text(
        "from pathlib import Path\n"
        "import os, signal\n"
        "Path('.quality/fixer-count.txt').write_text('1\\n', encoding='utf-8')\n"
        "Path('source.txt').write_text('repaired\\n', encoding='utf-8')\n"
        "os.kill(os.getppid(), signal.SIGKILL)\n",
        encoding="utf-8",
    )
    crashed = subprocess.run(
        _repair_command(workspace, policy, target, baseline, crashing),
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    assert crashed.returncode < 0
    task_run_path = next((workspace / ".quality/task-runs").glob("*.json"))
    crashed_state = json.loads(task_run_path.read_text(encoding="utf-8"))
    assert crashed_state["status"] == "REPAIRING"
    assert crashed_state["phase"] == "FIXER_RUNNING"

    must_not_run = workspace / ".quality/fixers/must_not_run_after_crash.py"
    must_not_run.write_text(
        "from pathlib import Path\n"
        "Path('.quality/fixer-count.txt').write_text('2\\n', encoding='utf-8')\n"
        "raise SystemExit(99)\n",
        encoding="utf-8",
    )
    resumed = subprocess.run(
        _repair_command(workspace, policy, target, baseline, must_not_run),
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert (workspace / ".quality/fixer-count.txt").read_text(encoding="utf-8") == "1\n"
    completed = json.loads(task_run_path.read_text(encoding="utf-8"))
    assert completed["status"] == "COMPLETED"
    assert completed["metadata"]["reconciled_interrupted_fixer"] is True
