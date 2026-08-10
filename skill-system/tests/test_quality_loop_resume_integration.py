from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
QUALITY_LOOP = ROOT / "scripts" / "quality_loop.py"


def _controller():
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        "quality_loop_resume_integration_test_controller", QUALITY_LOOP
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _target(target_id: str) -> str:
    return f"""# 目标

- 目标 ID：{target_id}
- 变更标识：resume-test-ref
- 执行上下文：ci
- 目标类型：certification

验证 Quality Loop 同一次中断运行的精确恢复合同。

## 允许范围

仅测试临时 workspace。

- 允许变更路径：services/**
- 新增抽象记录：无

## 禁止范围

不访问网络或真实服务。

## 验收条件

中断只能恢复同一目标、策略、源码快照和 Gate closure。

- 最低质量模式：static
- 声明清单：governance/claims/resume-test-claims.json
- 验收 ID：RESUME-TEST-001

## 基线

CI 临时测试不使用历史 baseline。

## 修复轮次

- 最大轮次：3
- 当前轮次：1
- 失败后：使用 repair plan。
"""


def _step(step_id: str, *, depends_on: list[str] | None = None) -> dict:
    return {
        "id": step_id,
        "name": step_id,
        "modes": ["static", "quick", "integration", "release"],
        "kind": "shell",
        "argv": [sys.executable, "-S", "-c", "pass"],
        "owner": "tests",
        "category": "test",
        "blocking_level": "required",
        "repair_playbook": "repair temporary resume gate",
        "rerun_contract": "dependency_closure_then_downstream",
        "depends_on": depends_on or [],
        "environment": {},
        "timeout_seconds": 20,
    }


def _workspace(root: Path, *, target_id: str) -> tuple[Path, Path, Path, Path, Path]:
    governance = root / "governance"
    governance.mkdir(parents=True)
    (root / "VERSION").write_text("test\n", encoding="utf-8")

    steps = [_step("gate-a"), _step("gate-b", depends_on=["gate-a"])]
    policy = governance / "policy.json"
    policy.write_text(
        json.dumps({"version": "test", "steps": steps}), encoding="utf-8"
    )

    target_path = root / ".quality" / "targets" / "target.md"
    target_path.parent.mkdir(parents=True)
    target_path.write_text(_target(target_id), encoding="utf-8")

    claims = governance / "claims" / "resume-test-claims.json"
    claims.parent.mkdir(parents=True)
    claims.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_id": target_id,
                "claims": [
                    {
                        "id": "RESUME-TEST-001",
                        "statement": "Both temporary gates pass under exact same-run resume semantics.",
                        "risk": "P2",
                        "required_mode": "static",
                        "evidence_kind": "static-contract",
                        "required_gates": ["gate-a", "gate-b"],
                        "evidence_refs": ["gate-log:gate-a", "gate-log:gate-b"],
                        "owner": "tests",
                        "closure_requirement": "current-pass",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    evidence = root / ".quality" / "evidence" / "resume"
    state_dir = root / ".quality" / "loop-state"
    return root, policy, target_path, evidence, state_dir


def _step_from_call(args, kwargs) -> dict:
    candidate = kwargs.get("step")
    if isinstance(candidate, dict) and "id" in candidate:
        return candidate
    for value in args:
        if isinstance(value, dict) and "id" in value and "argv" in value:
            return value
    raise AssertionError("could not identify quality gate in _run_step call")


class QualityLoopResumeIntegrationTests(unittest.TestCase):
    def _interrupt_after_gate_a(self, controller, workspace, policy, target, evidence, state_dir):
        original = controller._run_step
        calls: list[str] = []

        def interrupting(*args, **kwargs):
            step = _step_from_call(args, kwargs)
            calls.append(str(step["id"]))
            if step["id"] == "gate-b":
                raise KeyboardInterrupt("simulated process interruption")
            return original(*args, **kwargs)

        controller._run_step = interrupting
        try:
            with self.assertRaises(KeyboardInterrupt):
                controller.run_loop(
                    workspace,
                    policy,
                    mode="static",
                    evidence_dir=evidence,
                    rerun_from=None,
                    target_path=target,
                    baseline=False,
                    baseline_evidence=None,
                    prior_evidence=None,
                    state_dir=state_dir,
                )
        finally:
            controller._run_step = original
        return original, calls

    def test_resume_skips_verified_prefix_and_reruns_only_interrupted_gate(self) -> None:
        controller = _controller()
        with tempfile.TemporaryDirectory() as temporary:
            workspace, policy, target, evidence, state_dir = _workspace(
                Path(temporary), target_id="resume-exact-prefix"
            )
            original, calls = self._interrupt_after_gate_a(
                controller, workspace, policy, target, evidence, state_dir
            )

            checkpoint_path = controller.active_checkpoint_path(
                state_dir, target_id="resume-exact-prefix"
            )
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [record["id"] for record in checkpoint["completed_steps"]], ["gate-a"]
            )
            self.assertEqual(checkpoint["current_gate_id"], "gate-b")

            def observing(*args, **kwargs):
                step = _step_from_call(args, kwargs)
                calls.append(str(step["id"]))
                return original(*args, **kwargs)

            controller._run_step = observing
            try:
                result = controller.run_loop(
                    workspace,
                    policy,
                    mode="static",
                    evidence_dir=evidence,
                    rerun_from=None,
                    target_path=target,
                    baseline=False,
                    baseline_evidence=None,
                    prior_evidence=None,
                    state_dir=state_dir,
                )
            finally:
                controller._run_step = original

            self.assertEqual(result["decision"], controller.PASS)
            self.assertEqual(calls, ["gate-a", "gate-b", "gate-b"])
            self.assertFalse(checkpoint_path.exists())

    def test_tampered_completed_gate_evidence_fails_closed(self) -> None:
        controller = _controller()
        with tempfile.TemporaryDirectory() as temporary:
            workspace, policy, target, evidence, state_dir = _workspace(
                Path(temporary), target_id="resume-tamper"
            )
            self._interrupt_after_gate_a(
                controller, workspace, policy, target, evidence, state_dir
            )
            stdout_path = evidence / "steps" / "gate-a.stdout.txt"
            stdout_path.write_text("tampered\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "stdout"):
                controller.run_loop(
                    workspace,
                    policy,
                    mode="static",
                    evidence_dir=evidence,
                    rerun_from=None,
                    target_path=target,
                    baseline=False,
                    baseline_evidence=None,
                    prior_evidence=None,
                    state_dir=state_dir,
                )

    def test_source_snapshot_change_rejects_old_resume_frontier(self) -> None:
        controller = _controller()
        with tempfile.TemporaryDirectory() as temporary:
            workspace, policy, target, evidence, state_dir = _workspace(
                Path(temporary), target_id="resume-source-drift"
            )
            self._interrupt_after_gate_a(
                controller, workspace, policy, target, evidence, state_dir
            )
            source = workspace / "services" / "changed.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE = 2\n", encoding="utf-8")

            with self.assertRaisesRegex(
                controller.QualityRunConflictError,
                "no compatible interrupted-run checkpoint",
            ):
                controller.run_loop(
                    workspace,
                    policy,
                    mode="static",
                    evidence_dir=evidence,
                    rerun_from=None,
                    target_path=target,
                    baseline=False,
                    baseline_evidence=None,
                    prior_evidence=None,
                    state_dir=state_dir,
                )

    def test_normal_completion_clears_owned_active_checkpoint(self) -> None:
        controller = _controller()
        with tempfile.TemporaryDirectory() as temporary:
            workspace, policy, target, evidence, state_dir = _workspace(
                Path(temporary), target_id="resume-normal-completion"
            )
            result = controller.run_loop(
                workspace,
                policy,
                mode="static",
                evidence_dir=evidence,
                rerun_from=None,
                target_path=target,
                baseline=False,
                baseline_evidence=None,
                prior_evidence=None,
                state_dir=state_dir,
            )
            checkpoint_path = controller.active_checkpoint_path(
                state_dir, target_id="resume-normal-completion"
            )
            self.assertEqual(result["decision"], controller.PASS)
            self.assertFalse(checkpoint_path.exists())


if __name__ == "__main__":
    unittest.main()
