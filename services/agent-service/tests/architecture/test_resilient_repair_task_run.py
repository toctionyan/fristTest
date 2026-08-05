from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from tests.support.paths import workspace_root


def _load_script(name: str):
    root = workspace_root(__file__)
    path = root / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepare_workspace(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict]:
    root = workspace_root(__file__)
    workspace = tmp_path
    (workspace / "governance/claims").mkdir(parents=True)
    (workspace / "VERSION").write_text("test\n", encoding="utf-8")
    (workspace / "source.txt").write_text("baseline\n", encoding="utf-8")
    claims = {
        "schema_version": 1,
        "target_id": "resilient-repair-test",
        "claims": [
            {
                "id": "RESILIENT-REPAIR-001",
                "statement": "Interrupted repair resumes without replaying the fixer.",
                "risk": "P2",
                "required_mode": "static",
                "evidence_kind": "static-contract",
                "required_gates": ["proof"],
                "evidence_refs": ["gate-log:proof"],
                "owner": "repair-harness",
                "closure_requirement": "regression-transition",
            }
        ],
    }
    (workspace / "governance/claims/resilient.json").write_text(
        json.dumps(claims), encoding="utf-8"
    )
    target = workspace / "target.md"
    target.write_text(
        """# 目标
- 目标 ID：resilient-repair-test
- 变更标识：resilient-repair-change
- 执行上下文：local-change
- 目标类型：repair

验证可恢复修复任务。

## 允许范围
- 允许变更路径：source.txt
- 新增抽象记录：无

## 禁止范围
不修改其他产品源码。

## 验收条件
- 最低质量模式：static
- 声明清单：governance/claims/resilient.json
- 验收 ID：RESILIENT-REPAIR-001

## 基线
source.txt 在基线中不是 repaired。

## 修复轮次
- 最大轮次：2
- 当前轮次：1
- 失败后：修改唯一 Owner。
""",
        encoding="utf-8",
    )
    policy = workspace / "policy.json"
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
                        "owner": "repair-harness",
                        "category": "counterexample-regression",
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
    for script_name in ("quality_loop.py", "source_paths.py", "repair_loop.py"):
        source = root / "scripts" / script_name
        destination = workspace / "scripts" / script_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copytree(root / "scripts/quality_control", workspace / "scripts/quality_control")
    for controller_name in (
        "progress.py",
        "trusted_judge.py",
        "fixer_env.py",
        "issue_state.py",
        "task_run.py",
    ):
        source = root / "skill-system/controller" / controller_name
        destination = workspace / "skill-system/controller" / controller_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    trusted_path = workspace / "skill-system/controller/trusted_judge.py"
    spec = importlib.util.spec_from_file_location("temporary_trusted_judge", trusted_path)
    assert spec and spec.loader
    trusted = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trusted)
    trusted.write_manifest(workspace)

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
    return workspace, policy, target, baseline, summary


def _repair_command(
    workspace: Path,
    policy: Path,
    target: Path,
    baseline: Path,
    *fix_command: str,
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
        *fix_command,
    ]


def test_interrupted_fixer_is_reconciled_without_replaying_the_fixer(tmp_path: Path) -> None:
    workspace, policy, target, baseline, _summary = _prepare_workspace(tmp_path)
    orchestrator = _load_script("repair_loop.py")
    controller = _load_script("quality_loop.py")
    validated = orchestrator.validate_repair_inputs(
        workspace=workspace,
        policy=policy,
        target=target,
        baseline=baseline,
    )
    target_identity = controller._target_identity(validated["target"])
    task_id = orchestrator.stable_task_id("repair-loop", target_identity)
    task_path = workspace / ".quality/task-runs" / f"{task_id}.json"
    binding = {
        "workspace": str(workspace.resolve()),
        "target_identity": target_identity,
        "policy_fingerprint": controller._sha256_file(policy),
        "baseline_attestation_fingerprint": controller._sha256_file(
            baseline / "evidence-attestation.json"
        ),
    }
    store = orchestrator.TaskRunStore.open_or_create(
        task_path,
        task_id=task_id,
        task_kind="repair-loop",
        binding=binding,
        required_conditions=(
            "inputs_validated",
            "source_changed",
            "targeted_validation_resolved",
            "full_regression_passed",
            "issues_closed",
        ),
        current_workspace_fingerprint=orchestrator._workspace_fingerprint(workspace),
    )
    before = orchestrator._workspace_fingerprint(workspace)
    store.checkpoint(
        status="RUNNING",
        phase="INPUTS_VALIDATED",
        workspace_fingerprint=before,
        evidence_refs=[".quality/baseline/run-summary.json"],
    )
    store.checkpoint(
        status="REPAIRING",
        phase="FIXER_RUNNING",
        workspace_fingerprint=before,
        evidence_refs=[".quality/issues.json"],
    )

    # Simulate: the fixer wrote the governed source and then the host process died
    # before it could record FIXER_APPLIED.
    (workspace / "source.txt").write_text("repaired\n", encoding="utf-8")
    replay_marker = workspace.parent / f"{workspace.name}-fixer-replayed.txt"
    fixer = workspace.parent / f"{workspace.name}-must-not-run.py"
    fixer.write_text(
        "from pathlib import Path\n"
        f"Path({str(replay_marker)!r}).write_text('replayed\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        _repair_command(workspace, policy, target, baseline, sys.executable, str(fixer)),
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not replay_marker.exists(), "recovery replayed an already-applied fixer"
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    assert payload["status"] == "COMPLETED"
    assert payload["phase"] == "COMPLETED"
    assert payload["metadata"]["reconciled_interrupted_fixer"] is True
    assert all(row["strategy"] != "configured-fixer" for row in payload["action_attempts"])
    assert payload["conditions"]["full_regression_passed"]["satisfied"] is True


def test_interrupted_fixer_with_out_of_scope_drift_blocks_without_replay(tmp_path: Path) -> None:
    workspace, policy, target, baseline, _summary = _prepare_workspace(tmp_path)
    orchestrator = _load_script("repair_loop.py")
    controller = _load_script("quality_loop.py")
    validated = orchestrator.validate_repair_inputs(
        workspace=workspace, policy=policy, target=target, baseline=baseline
    )
    target_identity = controller._target_identity(validated["target"])
    task_id = orchestrator.stable_task_id("repair-loop", target_identity)
    task_path = workspace / ".quality/task-runs" / f"{task_id}.json"
    store = orchestrator.TaskRunStore.open_or_create(
        task_path,
        task_id=task_id,
        task_kind="repair-loop",
        binding={
            "workspace": str(workspace.resolve()),
            "target_identity": target_identity,
            "policy_fingerprint": controller._sha256_file(policy),
            "baseline_attestation_fingerprint": controller._sha256_file(
                baseline / "evidence-attestation.json"
            ),
        },
        required_conditions=(
            "inputs_validated",
            "source_changed",
            "targeted_validation_resolved",
            "full_regression_passed",
            "issues_closed",
        ),
        current_workspace_fingerprint=orchestrator._workspace_fingerprint(workspace),
    )
    before = orchestrator._workspace_fingerprint(workspace)
    store.checkpoint(
        status="RUNNING",
        phase="INPUTS_VALIDATED",
        workspace_fingerprint=before,
        evidence_refs=(".quality/baseline/run-summary.json",),
    )
    store.checkpoint(
        status="REPAIRING",
        phase="FIXER_RUNNING",
        workspace_fingerprint=before,
        evidence_refs=(".quality/issues.json",),
    )
    (workspace / "source.txt").write_text("repaired\n", encoding="utf-8")
    (workspace / "unexpected.txt").write_text("out-of-scope\n", encoding="utf-8")
    marker = workspace.parent / f"{workspace.name}-unexpected-replay.txt"
    fixer = workspace.parent / f"{workspace.name}-unexpected-must-not-run.py"
    fixer.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('replayed\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        _repair_command(workspace, policy, target, baseline, sys.executable, str(fixer)),
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 7, completed.stdout + completed.stderr
    assert not marker.exists()
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["blockers"][-1]["code"] == "INTERRUPTED_FIXER_RECONCILIATION_REJECTED"
    assert "unexpected.txt" in payload["blockers"][-1]["reason"]


def test_no_progress_fixer_is_bounded_across_process_restarts(tmp_path: Path) -> None:
    workspace, policy, target, baseline, _summary = _prepare_workspace(tmp_path)
    command = _repair_command(
        workspace,
        policy,
        target,
        baseline,
        sys.executable,
        "-c",
        "pass",
    )
    first = subprocess.run(command, cwd=workspace, text=True, capture_output=True, check=False)
    second = subprocess.run(command, cwd=workspace, text=True, capture_output=True, check=False)
    third = subprocess.run(command, cwd=workspace, text=True, capture_output=True, check=False)
    assert first.returncode == 4, first.stdout + first.stderr
    assert second.returncode == 4, second.stdout + second.stderr
    assert third.returncode == 8, third.stdout + third.stderr

    task_files = list((workspace / ".quality/task-runs").glob("*.json"))
    assert len(task_files) == 1
    payload = json.loads(task_files[0].read_text(encoding="utf-8"))
    fixer_attempts = [
        row for row in payload["action_attempts"] if row["strategy"] == "configured-fixer"
    ]
    assert len(fixer_attempts) == 2
    assert all(row["produced_new_evidence"] is False for row in fixer_attempts)
    assert payload["status"] == "BLOCKED"
    assert payload["blockers"][-1]["code"] == "FIXER_NO_PROGRESS"
    assert payload["blockers"][-1]["next_action"]


def test_existing_judge_summary_is_reused_without_consuming_execution_budget(
    tmp_path: Path, monkeypatch
) -> None:
    orchestrator = _load_script("repair_loop.py")
    workspace = tmp_path
    evidence_dir = workspace / ".quality/evidence/cycle-01-full"
    evidence_dir.mkdir(parents=True)
    summary_path = evidence_dir / "run-summary.json"
    summary = {
        "decision": "PASS",
        "loop_status": "CONVERGED",
        "workspace_snapshot_fingerprint": "workspace-a",
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    task_run = orchestrator.TaskRunStore.open_or_create(
        workspace / ".quality/task-runs/judge-recovery.json",
        task_id="judge-recovery",
        task_kind="repair-loop",
        binding={"target_identity": "target-a"},
        required_conditions=("full_regression_passed",),
        current_workspace_fingerprint="workspace-a",
    )
    monkeypatch.setattr(orchestrator, "_workspace_fingerprint", lambda _workspace: "workspace-a")

    def must_not_execute_judge(**_kwargs):
        raise AssertionError("durable Judge evidence was ignored and the Judge was rerun")

    monkeypatch.setattr(orchestrator, "_run_judge", must_not_execute_judge)
    returncode, recovered, attempted, recovered_path = orchestrator._run_judge_resilient(
        task_run=task_run,
        workspace=workspace,
        policy=workspace / "policy.json",
        target=workspace / "target.md",
        baseline=workspace / ".quality/baseline",
        state_dir=workspace / ".quality/state",
        evidence_dir=evidence_dir,
        mode="static",
        rerun_from=None,
        judge_root=workspace,
    )

    assert returncode == 0
    assert recovered == summary
    assert attempted == ("recover-existing-summary",)
    assert recovered_path == summary_path
    assert task_run.payload["action_attempts"] == []
    assert task_run.payload["metadata"]["recovered_existing_summary"] is True
