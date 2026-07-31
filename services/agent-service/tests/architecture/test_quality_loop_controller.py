from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.support.paths import workspace_root


def _controller():
    root = workspace_root(__file__)
    path = root / "scripts" / "quality_loop.py"
    spec = importlib.util.spec_from_file_location("quality_loop_controller", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _target(
    *, target_id: str, context: str = "ci", current: int = 1, maximum: int = 3, minimum_mode: str = "static",
    claim_path: str = "governance/claims/test-claims.json", target_kind: str = "certification",
) -> str:
    return f"""# 目标

- 目标 ID：{target_id}
- 变更标识：test-ref
- 执行上下文：{context}
- 目标类型：{target_kind}

验证质量控制器的确定性合同。

## 允许范围

仅测试临时 workspace。

- 允许变更路径：services/**
- 新增抽象记录：无

## 禁止范围

不访问网络或真实服务。

## 验收条件

控制器给出可复现的 PASS、FAIL 或 BLOCKED 证据。

- 最低质量模式：{minimum_mode}
- 声明清单：{claim_path}
- 验收 ID：TEST-CLAIM-001

## 基线

baseline evidence 由此临时测试生成。

## 修复轮次

- 最大轮次：{maximum}
- 当前轮次：{current}
- 失败后：使用 repair plan。
"""


def _step(step_id: str, *, depends_on: list[str] | None = None, fail: bool = False) -> dict:
    argv = [sys.executable, "-S", "-c", "import sys; sys.exit(1)" if fail else "pass"]
    return {
        "id": step_id,
        "name": step_id,
        "modes": ["static", "quick", "integration", "release"],
        "kind": "shell",
        "argv": argv,
        "owner": "tests",
        "category": "test",
        "blocking_level": "required",
        "repair_playbook": "repair the temporary test gate",
        "rerun_contract": "dependency_closure_then_downstream",
        "depends_on": depends_on or [],
        "environment": {},
        "timeout_seconds": 20,
    }


def _workspace(tmp_path: Path, *, steps: list[dict], target: str) -> tuple[Path, Path, Path]:
    (tmp_path / "governance").mkdir()
    (tmp_path / "VERSION").write_text("test\n", encoding="utf-8")
    policy = tmp_path / "governance" / "policy.json"
    policy.write_text(json.dumps({"version": "test", "steps": steps}), encoding="utf-8")
    target_path = tmp_path / ".quality" / "targets" / "target.md"
    target_path.parent.mkdir(parents=True)
    target_path.write_text(target, encoding="utf-8")
    minimum = "static"
    for candidate in ("release", "integration", "quick", "static"):
        if f"最低质量模式：{candidate}" in target:
            minimum = candidate
            break
    target_id = target.split("目标 ID：", 1)[1].splitlines()[0].strip()
    claim_path = target.split("声明清单：", 1)[1].splitlines()[0].strip().strip("`")
    claim_file = tmp_path / claim_path
    claim_file.parent.mkdir(parents=True, exist_ok=True)
    target_kind = next(
        (kind for kind in ("repair", "migration", "revert") if f"目标类型：{kind}" in target),
        "certification",
    )
    closure_requirement = (
        "regression-transition"
        if target_kind in {"repair", "migration", "revert"}
        else "current-pass"
    )
    claim_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_id": target_id,
                "claims": [
                    {
                        "id": "TEST-CLAIM-001",
                        "statement": "The selected temporary quality gates pass for this controller fixture.",
                        "risk": "P2",
                        "required_mode": minimum,
                        "evidence_kind": "static-contract",
                        "required_gates": [step["id"] for step in steps],
                        "evidence_refs": [f"gate-log:{steps[0]['id']}"],
                        "owner": "tests",
                        "closure_requirement": closure_requirement,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path, policy, target_path


def test_policy_rejects_dependency_cycle() -> None:
    controller = _controller()
    with pytest.raises(ValueError, match="cycle"):
        controller._validate_policy({"steps": [_step("a", depends_on=["b"]), _step("b", depends_on=["a"])]})


def test_local_loop_requires_baseline_and_enforces_repair_round(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("failing", fail=True)],
        target=_target(target_id="local-loop-test", context="local-change", maximum=2, target_kind="repair"),
    )
    state_dir = workspace / ".quality" / "loop-state"
    baseline_dir = workspace / ".quality" / "evidence" / "baseline"
    baseline = controller.run_loop(
        workspace,
        policy,
        mode="static",
        evidence_dir=baseline_dir,
        rerun_from=None,
        target_path=target,
        baseline=True,
        baseline_evidence=None,
        prior_evidence=None,
        state_dir=state_dir,
    )
    assert baseline["loop_status"] == "BASELINE_RECORDED"
    assert (baseline_dir / "baseline-record.json").is_file()

    with pytest.raises(ValueError, match="baseline-evidence"):
        controller.run_loop(
            workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "missing-baseline", rerun_from=None,
            target_path=target, baseline=False, baseline_evidence=None, prior_evidence=None, state_dir=state_dir,
        )

    candidate = workspace / "services" / "candidate.txt"
    candidate.parent.mkdir()
    candidate.write_text("round-1 actual candidate repair\n", encoding="utf-8")

    first = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "first", rerun_from=None,
        target_path=target, baseline=False, baseline_evidence=baseline_dir, prior_evidence=None, state_dir=state_dir,
    )
    assert first["decision"] == controller.FAIL
    assert first["loop_status"] == "REPAIR_REQUIRED"
    with pytest.raises(ValueError, match="当前轮次 must be 2"):
        controller.run_loop(
            workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "wrong-round", rerun_from=None,
            target_path=target, baseline=False, baseline_evidence=baseline_dir, prior_evidence=None, state_dir=state_dir,
        )

    target.write_text(_target(target_id="local-loop-test", context="local-change", current=2, maximum=2, target_kind="repair"), encoding="utf-8")
    repair = workspace / "services" / "repair.txt"
    repair.parent.mkdir(exist_ok=True)
    repair.write_text("round-2 actual repair\n", encoding="utf-8")
    final = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "last", rerun_from=None,
        target_path=target, baseline=False, baseline_evidence=baseline_dir, prior_evidence=None, state_dir=state_dir,
    )
    assert final["loop_status"] == "STOPPED_MAX_REPAIRS"


def test_targeted_rerun_executes_dependency_closure_without_prior_evidence(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("upstream"), _step("downstream", depends_on=["upstream"])],
        target=_target(target_id="rerun-loop-test"),
    )
    state_dir = workspace / "state"
    rerun = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / "blocked", rerun_from="downstream",
        target_path=target, baseline=False, baseline_evidence=None, prior_evidence=None, state_dir=state_dir,
    )
    assert rerun["decision"] == controller.PASS
    assert rerun["selected_gate_ids"] == ["upstream", "downstream"]
    assert rerun["reused_prerequisites"] == []
    assert rerun["missing_prerequisites"] == []


def test_targeted_rerun_rebuilds_coverage_evidence_instead_of_copying_history(tmp_path: Path) -> None:
    controller = _controller()
    producer = _step("coverage-producer")
    producer["argv"] = [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; p=Path(sys.argv[1]); p.mkdir(parents=True, exist_ok=True); (p/'marker.json').write_text('pass')",
        "{evidence_dir}/coverage/frontend",
    ]
    consumer = _step("coverage-consumer", depends_on=["coverage-producer"])
    consumer["argv"] = [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; assert (Path(sys.argv[1])/'coverage/frontend/marker.json').is_file()",
        "{evidence_dir}",
    ]
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[producer, consumer],
        target=_target(target_id="reused-artifact-test"),
    )
    rerun_dir = workspace / "rerun"
    rerun = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=rerun_dir, rerun_from="coverage-consumer",
        target_path=target, baseline=False, baseline_evidence=None, prior_evidence=None,
        state_dir=workspace / "state",
    )

    assert rerun["decision"] == controller.PASS
    assert rerun["selected_gate_ids"] == ["coverage-producer", "coverage-consumer"]
    assert "materialized_reused_evidence" not in rerun
    assert (rerun_dir / "coverage" / "frontend" / "marker.json").read_text() == "pass"


def test_release_policy_declares_one_production_certification_authority() -> None:
    root = workspace_root(__file__)
    policy = json.loads((root / "governance" / "quality-loop-policy.json").read_text(encoding="utf-8"))
    release = {step["id"]: step for step in policy["steps"] if "release" in step["modes"]}
    assert "production-certification-bundle" in release
    assert "preproduction-real-model-certification-bundle" not in release
    assert not {"preproduction-model-base-smoke", "preproduction-conversation-prototypes", "preproduction-full-lifecycle-model"} & set(release)
    assert {"product-http-smoke", "python-integration-tests", "full-lifecycle-canary", "frontend-build"} <= set(
        release["production-certification-bundle"]["depends_on"]
    )


def test_release_mode_requires_explicit_ci_evidence_signing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    controller = _controller()
    step = _step("release-gate")
    step["modes"] = ["release"]
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[step],
        target=_target(target_id="release-signing-key", minimum_mode="release"),
    )
    monkeypatch.delenv("QUALITY_EVIDENCE_SIGNING_KEY", raising=False)
    with pytest.raises(ValueError, match="requires QUALITY_EVIDENCE_SIGNING_KEY"):
        controller.run_loop(
            workspace, policy, mode="release", evidence_dir=workspace / "release-evidence",
            rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
            prior_evidence=None, state_dir=workspace / "state",
        )

    monkeypatch.setenv("QUALITY_EVIDENCE_SIGNING_KEY", "release-test-signing-key-1234567890-strong")
    result = controller.run_loop(
        workspace, policy, mode="release", evidence_dir=workspace / "signed-release-evidence",
        rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
        prior_evidence=None, state_dir=workspace / "state",
    )
    assert result["decision"] == controller.PASS
    assert result["completion_eligible"] is True


def test_target_rejects_template_identity_and_change_reference(tmp_path: Path) -> None:
    controller = _controller()
    target = tmp_path / "target.md"
    target.write_text(_target(target_id="change-YYYYMMDD-short-name"), encoding="utf-8")
    with pytest.raises(ValueError, match="template placeholder"):
        controller._parse_target(target, workspace=tmp_path)

    target.write_text(
        _target(target_id="valid-target-id").replace("变更标识：test-ref", "变更标识：commit-SHA"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-placeholder"):
        controller._parse_target(target, workspace=tmp_path)


def test_local_verification_rejects_changes_outside_frozen_target_scope(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("passing")],
        target=_target(target_id="scope-loop-test", context="local-change"),
    )
    state_dir = workspace / ".quality" / "loop-state"
    baseline_dir = workspace / ".quality" / "evidence" / "baseline"
    controller.run_loop(
        workspace, policy, mode="static", evidence_dir=baseline_dir, rerun_from=None,
        target_path=target, baseline=True, baseline_evidence=None, prior_evidence=None, state_dir=state_dir,
    )
    (workspace / "unapproved-change.txt").write_text("outside the declared services scope\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the frozen target"):
        controller.run_loop(
            workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "verification",
            rerun_from=None, target_path=target, baseline=False, baseline_evidence=baseline_dir,
            prior_evidence=None, state_dir=state_dir,
        )


def test_local_verification_requires_abstraction_record_for_new_production_source(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("passing")],
        target=_target(target_id="abstraction-loop-test", context="local-change"),
    )
    state_dir = workspace / ".quality" / "loop-state"
    baseline_dir = workspace / ".quality" / "evidence" / "baseline"
    controller.run_loop(
        workspace, policy, mode="static", evidence_dir=baseline_dir, rerun_from=None,
        target_path=target, baseline=True, baseline_evidence=None, prior_evidence=None, state_dir=state_dir,
    )
    source = workspace / "services" / "agent-service" / "src" / "new_owner.py"
    source.parent.mkdir(parents=True)
    source.write_text("class NewOwner:\n    pass\n", encoding="utf-8")

    with pytest.raises(ValueError, match="new production source files require"):
        controller.run_loop(
            workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "verification",
            rerun_from=None, target_path=target, baseline=False, baseline_evidence=baseline_dir,
            prior_evidence=None, state_dir=state_dir,
        )


def test_verification_rejects_a_mode_below_the_target_acceptance_floor(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("passing")],
        target=_target(target_id="minimum-mode-test", minimum_mode="quick"),
    )

    with pytest.raises(ValueError, match="最低质量模式：quick"):
        controller.run_loop(
            workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "run",
            rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
            prior_evidence=None, state_dir=workspace / ".quality" / "loop-state",
        )


def test_controller_input_repair_plan_does_not_rerun_a_nonexistent_gate(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("passing")],
        target=_target(target_id="controller-input-repair-test"),
    )
    evidence_dir = workspace / ".quality" / "evidence" / "invalid-input"

    summary = controller._controller_failure(
        workspace=workspace,
        policy_path=policy,
        mode="static",
        evidence_dir=evidence_dir,
        target_path=target,
        baseline_evidence=None,
        prior_evidence=None,
        error=ValueError("invalid target"),
    )

    assert set(controller.EVIDENCE_REQUIRED_FIELDS) <= set(summary)
    assert summary["decision"] == controller.FAIL
    assert summary["completion_eligible"] is False
    assert summary["selected_gate_ids"] == ["quality-controller-input"]
    controller.verify_evidence_attestation(workspace, evidence_dir)
    repair = json.loads((evidence_dir / "repair-plan.json").read_text(encoding="utf-8"))
    item = repair["repairs"][0]
    assert "--rerun-from" not in item["rerun"]
    assert "--prior-evidence" not in item["rerun"]
    assert "create a new --baseline" in item["next_action"]


def test_declared_missing_environment_is_blocked_not_treated_as_a_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    controller = _controller()
    monkeypatch.delenv("QUALITY_LOOP_TEST_REQUIRED_VALUE", raising=False)
    step = _step("requires-environment")
    step["environment"] = {"variables": ["QUALITY_LOOP_TEST_REQUIRED_VALUE"]}
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[step],
        target=_target(target_id="environment-boundary-test"),
    )

    summary = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "run",
        rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
        prior_evidence=None, state_dir=workspace / ".quality" / "loop-state",
    )

    assert summary["decision"] == controller.BLOCKED
    assert summary["results"][0]["status"] == controller.BLOCKED
    assert "environment:QUALITY_LOOP_TEST_REQUIRED_VALUE" in summary["results"][0]["metadata"]["missing_environment"]


def test_runtime_environment_block_requires_structured_exit_78_evidence(tmp_path: Path) -> None:
    controller = _controller()
    structured = _step("dynamic-environment-block")
    structured["argv"] = [
        sys.executable,
        "-S",
        "-c",
        'import json,sys; print(json.dumps({{"status":"BLOCKED_BY_ENVIRONMENT","reason":"provider_unavailable"}})); sys.exit(78)',
    ]
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[structured],
        target=_target(target_id="dynamic-environment-block-test"),
    )

    summary = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "run",
        rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
        prior_evidence=None, state_dir=workspace / ".quality" / "loop-state",
    )

    assert summary["decision"] == controller.BLOCKED
    assert summary["results"][0]["status"] == controller.BLOCKED
    assert summary["results"][0]["metadata"]["runtime_environment_block"] == {
        "status": controller.BLOCKED,
        "reason": "provider_unavailable",
    }


def test_bare_exit_78_without_contract_remains_a_failure(tmp_path: Path) -> None:
    controller = _controller()
    undeclared = _step("bare-exit-78")
    undeclared["argv"] = [sys.executable, "-S", "-c", "import sys; sys.exit(78)"]
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[undeclared],
        target=_target(target_id="bare-exit-78-test"),
    )

    summary = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "run",
        rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
        prior_evidence=None, state_dir=workspace / ".quality" / "loop-state",
    )

    assert summary["decision"] == controller.FAIL
    assert summary["results"][0]["status"] == controller.FAIL


def test_local_managed_node_runtime_satisfies_npm_requirement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    controller = _controller()
    bin_dir = tmp_path / ".quality" / "tools" / "node-v20.19.4-darwin-x64" / "bin"
    bin_dir.mkdir(parents=True)
    node = bin_dir / "node"
    node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    node.chmod(0o755)
    npm_target = bin_dir.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    npm_target.parent.mkdir(parents=True)
    npm_target.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    npm_target.chmod(0o755)
    npm = bin_dir / "npm"
    npm.symlink_to("../lib/node_modules/npm/bin/npm-cli.js")
    monkeypatch.setattr(controller.shutil, "which", lambda _name: None)

    resolved_npm = controller._npm_executable(tmp_path)

    assert resolved_npm == npm.absolute()
    assert resolved_npm.parent == bin_dir
    assert controller._environment_problem(tmp_path, {"environment": {"commands": ["npm"]}}) == []
    assert controller._interpolate("{npm}", workspace=tmp_path, evidence_dir=tmp_path / "evidence", mode="quick") == str(resolved_npm)


def test_target_accepts_eight_round_budget_and_rejects_nine(tmp_path: Path) -> None:
    controller = _controller()
    (tmp_path / "eight").mkdir()
    workspace_eight, _, target_eight = _workspace(
        tmp_path / "eight",
        steps=[_step("passing")],
        target=_target(target_id="eight-round-budget", maximum=8),
    )

    parsed = controller._parse_target(target_eight, workspace=workspace_eight)

    assert parsed["max_rounds"] == 8

    (tmp_path / "nine").mkdir()
    workspace_nine, _, target_nine = _workspace(
        tmp_path / "nine",
        steps=[_step("passing")],
        target=_target(target_id="nine-round-budget", maximum=9),
    )
    with pytest.raises(ValueError, match="最大轮次 <= 8"):
        controller._parse_target(target_nine, workspace=workspace_nine)


def test_loop_requires_architecture_replan_after_two_non_improving_rounds(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("persistent-failure", fail=True)],
        target=_target(target_id="stagnation-loop-test", context="local-change", maximum=8),
    )
    state_dir = workspace / ".quality" / "loop-state"
    baseline_dir = workspace / ".quality" / "evidence" / "baseline"
    controller.run_loop(
        workspace, policy, mode="static", evidence_dir=baseline_dir, rerun_from=None,
        target_path=target, baseline=True, baseline_evidence=None, prior_evidence=None, state_dir=state_dir,
    )

    first = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "round-1",
        rerun_from=None, target_path=target, baseline=False, baseline_evidence=baseline_dir,
        prior_evidence=None, state_dir=state_dir,
    )
    assert first["loop_status"] == "REPAIR_REQUIRED"
    assert first["convergence"]["stagnant_rounds"] == 0

    target.write_text(
        _target(target_id="stagnation-loop-test", context="local-change", current=2, maximum=8),
        encoding="utf-8",
    )
    repair = workspace / "services" / "repair.txt"
    repair.parent.mkdir()
    repair.write_text("round-2 attempted repair\n", encoding="utf-8")
    second = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "round-2",
        rerun_from=None, target_path=target, baseline=False, baseline_evidence=baseline_dir,
        prior_evidence=None, state_dir=state_dir,
    )
    assert second["loop_status"] == "REPAIR_REQUIRED"
    assert second["convergence"]["stagnant_rounds"] == 1

    target.write_text(
        _target(target_id="stagnation-loop-test", context="local-change", current=3, maximum=8),
        encoding="utf-8",
    )
    repair.write_text("round-3 different attempted repair\n", encoding="utf-8")
    third = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "round-3",
        rerun_from=None, target_path=target, baseline=False, baseline_evidence=baseline_dir,
        prior_evidence=None, state_dir=state_dir,
    )
    assert third["loop_status"] == "ARCHITECTURE_REPLAN_REQUIRED"
    assert third["convergence"]["stagnant_rounds"] == 2
    repair = json.loads((workspace / ".quality" / "evidence" / "round-3" / "repair-plan.json").read_text(encoding="utf-8"))
    assert "create a new architecture target" in repair["repairs"][0]["next_action"]

    target.write_text(
        _target(target_id="stagnation-loop-test", context="local-change", current=4, maximum=8),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="two consecutive rounds without measurable improvement"):
        controller.run_loop(
            workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "round-4",
            rerun_from=None, target_path=target, baseline=False, baseline_evidence=baseline_dir,
            prior_evidence=None, state_dir=state_dir,
        )


def test_replanned_target_requires_attested_stopped_predecessor(tmp_path: Path) -> None:
    controller = _controller()
    workspace, _, target = _workspace(
        tmp_path,
        steps=[_step("configured-model-browser-conversation")],
        target=_target(target_id="replanned-successor", context="local-change"),
    )
    predecessor = workspace / ".quality" / "evidence" / "stopped-predecessor"
    predecessor.mkdir(parents=True)
    (predecessor / "run-summary.json").write_text(
        json.dumps(
            {
                "run_kind": "verification",
                "decision": "FAIL",
                "loop_status": "ARCHITECTURE_REPLAN_REQUIRED",
                "target_identity": {"id": "stopped-predecessor"},
                "results": [
                    {"id": "configured-model-browser-conversation", "status": "FAIL"}
                ],
                "claim_results": [{"id": "OLD-CLAIM", "status": "FAILED"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (predecessor / "repair-plan.json").write_text(
        json.dumps(
            {
                "loop_status": "ARCHITECTURE_REPLAN_REQUIRED",
                "repairs": [
                    {
                        "gate_id": "configured-model-browser-conversation",
                        "next_action": "stop local patching and create a new architecture target with revised assumptions",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    controller._write_evidence_attestation(workspace, predecessor)
    target.write_text(
        _target(target_id="replanned-successor", context="local-change").replace(
            "- 目标类型：certification",
            "- 目标类型：certification\n"
            "- 重规划来源证据：`.quality/evidence/stopped-predecessor`\n"
            "- 重规划失败 Gate：`configured-model-browser-conversation`",
        ),
        encoding="utf-8",
    )

    parsed = controller._parse_target(target, workspace=workspace)
    lineage = controller._validate_replan_predecessor(workspace, target=parsed)

    assert lineage["target_identity"]["id"] == "stopped-predecessor"
    assert lineage["failed_gate_id"] == "configured-model-browser-conversation"
    assert lineage["loop_status"] == "ARCHITECTURE_REPLAN_REQUIRED"

    summary = json.loads((predecessor / "run-summary.json").read_text(encoding="utf-8"))
    summary["decision"] = "PASS"
    (predecessor / "run-summary.json").write_text(json.dumps(summary) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="attestation"):
        controller._validate_replan_predecessor(workspace, target=parsed)

    controller._write_evidence_attestation(workspace, predecessor)
    with pytest.raises(ValueError, match="failed verification"):
        controller._validate_replan_predecessor(workspace, target=parsed)

    summary["decision"] = "FAIL"
    summary["loop_status"] = "REPAIR_REQUIRED"
    (predecessor / "run-summary.json").write_text(json.dumps(summary) + "\n", encoding="utf-8")
    controller._write_evidence_attestation(workspace, predecessor)
    with pytest.raises(ValueError, match="not stopped"):
        controller._validate_replan_predecessor(workspace, target=parsed)

    summary["loop_status"] = "ARCHITECTURE_REPLAN_REQUIRED"
    summary["results"][0]["status"] = "PASS"
    (predecessor / "run-summary.json").write_text(json.dumps(summary) + "\n", encoding="utf-8")
    controller._write_evidence_attestation(workspace, predecessor)
    with pytest.raises(ValueError, match="does not prove failed Gate"):
        controller._validate_replan_predecessor(workspace, target=parsed)

    summary["results"][0]["status"] = "FAIL"
    summary["target_identity"]["id"] = "replanned-successor"
    (predecessor / "run-summary.json").write_text(json.dumps(summary) + "\n", encoding="utf-8")
    controller._write_evidence_attestation(workspace, predecessor)
    with pytest.raises(ValueError, match="cannot reference"):
        controller._validate_replan_predecessor(workspace, target=parsed)


def test_python_test_gates_use_locked_project_interpreters_by_default() -> None:
    root = workspace_root(__file__)
    policy = json.loads((root / "governance" / "quality-loop-policy.json").read_text(encoding="utf-8"))
    by_id = {step["id"]: step for step in policy["steps"]}
    for step_id in ("python-test-suites", "python-integration-tests"):
        env = by_id[step_id].get("env") or {}
        assert "QUALITY_AGENT_PYTHON" not in env
        assert "QUALITY_BUSINESS_PYTHON" not in env


def test_gate_subprocess_does_not_inherit_outer_pytest_cov_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller()
    monkeypatch.setenv("COV_CORE_SOURCE", "outer-suite")
    monkeypatch.setenv("COV_CORE_CONFIG", "outer-config")
    monkeypatch.setenv("COVERAGE_PROCESS_START", "outer-start")
    step = _step("coverage-isolation")
    step["argv"] = [
        sys.executable,
        "-c",
        (
            "import os; "
            "assert os.getenv('COV_CORE_SOURCE') is None; "
            "assert os.getenv('COV_CORE_CONFIG') is None; "
            "assert os.getenv('COVERAGE_PROCESS_START') is None"
        ),
    ]
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[step],
        target=_target(target_id="coverage-bootstrap-isolation"),
    )

    summary = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / "evidence",
        rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
        prior_evidence=None, state_dir=workspace / "state",
    )

    assert summary["decision"] == controller.PASS


def test_convergence_counts_only_direct_failures_not_upstream_skips() -> None:
    controller = _controller()
    metrics = controller._failure_metrics(
        [
            {"id": "root", "status": controller.FAIL, "category": "test", "exit_code": 1},
            {
                "id": "coverage",
                "status": controller.UPSTREAM_SKIPPED,
                "category": "coverage",
                "exit_code": None,
            },
        ]
    )

    assert metrics["failure_count"] == 1
    assert metrics["failed_gate_ids"] == ["root"]
    assert metrics["upstream_skipped_gate_ids"] == ["coverage"]


def test_environment_block_does_not_consume_a_local_repair_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller()
    monkeypatch.delenv("QUALITY_LOOP_LOCAL_ENV", raising=False)
    step = _step("environment-gate")
    step["environment"] = {"variables": ["QUALITY_LOOP_LOCAL_ENV"]}
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[step],
        target=_target(target_id="local-environment-block", context="local-change", maximum=8),
    )
    state_dir = workspace / ".quality" / "loop-state"
    baseline_dir = workspace / ".quality" / "evidence" / "baseline"
    controller.run_loop(
        workspace, policy, mode="static", evidence_dir=baseline_dir, rerun_from=None,
        target_path=target, baseline=True, baseline_evidence=None, prior_evidence=None, state_dir=state_dir,
    )

    blocked = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "blocked",
        rerun_from=None, target_path=target, baseline=False, baseline_evidence=baseline_dir,
        prior_evidence=None, state_dir=state_dir,
    )
    assert blocked["loop_status"] == "BLOCKED_BY_ENVIRONMENT"
    assert blocked["convergence"]["current_round"] == 1

    monkeypatch.setenv("QUALITY_LOOP_LOCAL_ENV", "available")
    recovered = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / ".quality" / "evidence" / "recovered",
        rerun_from=None, target_path=target, baseline=False, baseline_evidence=baseline_dir,
        prior_evidence=None, state_dir=state_dir,
    )
    assert recovered["loop_status"] == "CONVERGED"


def test_targeted_rerun_never_marks_the_whole_target_verified(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("upstream"), _step("downstream", depends_on=["upstream"])],
        target=_target(target_id="partial-is-not-converged"),
    )
    full_dir = workspace / "full"
    full = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=full_dir, rerun_from=None,
        target_path=target, baseline=False, baseline_evidence=None, prior_evidence=None,
        state_dir=workspace / "state",
    )
    assert full["loop_status"] == "CI_VERIFIED"
    assert full["completion_eligible"] is True

    partial = controller.run_loop(
        workspace, policy, mode="static", evidence_dir=workspace / "partial", rerun_from="downstream",
        target_path=target, baseline=False, baseline_evidence=None, prior_evidence=None,
        state_dir=workspace / "state",
    )

    assert partial["decision"] == controller.PASS
    assert partial["loop_status"] == "TARGETED_REGRESSION_PASSED"
    assert partial["completion_eligible"] is False
    assert partial["selected_gate_ids"] == ["upstream", "downstream"]
    assert partial["required_gate_ids"] == ["upstream", "downstream"]


def test_prior_evidence_compatibility_input_is_rejected(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("upstream"), _step("downstream", depends_on=["upstream"])],
        target=_target(target_id="historical-pass-is-not-input"),
    )
    full_dir = workspace / "full"
    controller.run_loop(
        workspace, policy, mode="static", evidence_dir=full_dir, rerun_from=None,
        target_path=target, baseline=False, baseline_evidence=None, prior_evidence=None,
        state_dir=workspace / "state",
    )

    with pytest.raises(ValueError, match="no longer supported"):
        controller.run_loop(
            workspace, policy, mode="static", evidence_dir=workspace / "partial", rerun_from="downstream",
            target_path=target, baseline=False, baseline_evidence=None, prior_evidence=full_dir,
            state_dir=workspace / "state",
        )


def test_tampered_evidence_fails_attestation_and_cannot_be_reused(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("upstream"), _step("downstream", depends_on=["upstream"])],
        target=_target(target_id="tampered-evidence"),
    )
    full_dir = workspace / "full"
    controller.run_loop(
        workspace, policy, mode="static", evidence_dir=full_dir, rerun_from=None,
        target_path=target, baseline=False, baseline_evidence=None, prior_evidence=None,
        state_dir=workspace / "state",
    )
    (full_dir / "steps" / "upstream.stdout.txt").write_text("forged pass\n", encoding="utf-8")

    with pytest.raises(ValueError, match="modified after attestation"):
        controller.verify_evidence_attestation(workspace, full_dir)
    with pytest.raises(ValueError, match="no longer supported"):
        controller.run_loop(
            workspace, policy, mode="static", evidence_dir=workspace / "partial", rerun_from="downstream",
            target_path=target, baseline=False, baseline_evidence=None, prior_evidence=full_dir,
            state_dir=workspace / "state",
        )


def test_gate_that_mutates_source_cannot_produce_a_green_run(tmp_path: Path) -> None:
    controller = _controller()
    mutator = _step("mutates-source")
    mutator["argv"] = [
        sys.executable,
        "-c",
        "from pathlib import Path; p=Path('services/mutated.py'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text('changed\\n')",
    ]
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[mutator],
        target=_target(target_id="gate-must-not-change-source"),
    )

    result = controller.run_loop(
        workspace,
        policy,
        mode="static",
        evidence_dir=workspace / "evidence",
        rerun_from=None,
        target_path=target,
        baseline=False,
        baseline_evidence=None,
        prior_evidence=None,
        state_dir=workspace / "state",
    )

    assert result["decision"] == controller.FAIL
    assert result["completion_eligible"] is False
    assert result["loop_status"] == "CI_FAILED"
    immutability = next(row for row in result["results"] if row["id"] == "controller-workspace-immutability")
    assert immutability["status"] == controller.FAIL
    assert result["workspace_snapshot_start_fingerprint"] != result["workspace_snapshot_fingerprint"]


def test_summary_records_exact_gate_contract_fingerprints(tmp_path: Path) -> None:
    controller = _controller()
    steps = [_step("one"), _step("two", depends_on=["one"])]
    workspace, policy, target = _workspace(
        tmp_path,
        steps=steps,
        target=_target(target_id="gate-contract-fingerprints"),
    )
    result = controller.run_loop(
        workspace,
        policy,
        mode="static",
        evidence_dir=workspace / "evidence",
        rerun_from=None,
        target_path=target,
        baseline=False,
        baseline_evidence=None,
        prior_evidence=None,
        state_dir=workspace / "state",
    )

    assert set(result["gate_contract_fingerprints"]) == {"one", "two"}
    assert all(len(value) == 64 for value in result["gate_contract_fingerprints"].values())


def test_frontend_dist_is_part_of_workspace_identity(tmp_path: Path) -> None:
    controller = _controller()
    dist = tmp_path / "services" / "agent-service" / "frontend" / "dist"
    dist.mkdir(parents=True)
    bundle = dist / "app.js"
    bundle.write_text("one\n", encoding="utf-8")
    before = controller._workspace_snapshot(tmp_path)
    bundle.write_text("two\n", encoding="utf-8")
    after = controller._workspace_snapshot(tmp_path)
    assert before["fingerprint"] != after["fingerprint"]


def test_gate_parent_exit_cannot_be_held_open_by_background_descendant(tmp_path: Path) -> None:
    controller = _controller()
    workspace = tmp_path / "workspace"
    evidence = tmp_path / "evidence"
    workspace.mkdir()
    evidence.mkdir()
    step = _step("descendant-fd-leak")
    step["timeout_seconds"] = 2
    step["argv"] = [
        sys.executable,
        "-S",
        "-c",
        (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-S','-c','import time; time.sleep(30)']); "
            "print('parent-complete')"
        ),
    ]

    result = controller._run_shell(workspace, evidence, "static", step)

    assert result["exit_code"] == 0, result
    assert "parent-complete" in result["stdout"]
    assert result["duration_ms"] < 2000


def test_shell_gate_receives_controller_owned_evidence_boundary(tmp_path: Path) -> None:
    controller = _controller()
    workspace = tmp_path / "workspace"
    evidence = tmp_path / "evidence"
    workspace.mkdir()
    evidence.mkdir()
    step = _step("artifact-producer")
    step["argv"] = [
        sys.executable,
        "-S",
        "-c",
        (
            "import json,os; print(json.dumps(dict("
            "evidence=os.environ.get('QUALITY_EVIDENCE_DIR'),"
            "mode=os.environ.get('QUALITY_LOOP_MODE'),"
            "gate=os.environ.get('QUALITY_GATE_ID'))))"
        ),
    ]

    result = controller._run_shell(workspace, evidence, "integration", step)
    payload = json.loads(result["stdout"])

    assert result["exit_code"] == 0
    assert payload == {
        "evidence": str(evidence),
        "mode": "integration",
        "gate": "artifact-producer",
    }


def test_claim_manifest_derives_and_enforces_the_minimum_mode(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("release-proof")],
        target=_target(target_id="claim-mode-test", minimum_mode="quick"),
    )
    claims = workspace / "governance" / "claims" / "test-claims.json"
    payload = json.loads(claims.read_text(encoding="utf-8"))
    payload["claims"][0]["risk"] = "P1"
    payload["claims"][0]["required_mode"] = "release"
    payload["claims"][0]["evidence_kind"] = "release-provenance"
    claims.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="claim-derived mode"):
        controller.run_loop(
            workspace,
            policy,
            mode="quick",
            evidence_dir=workspace / "evidence",
            rerun_from=None,
            target_path=target,
            baseline=False,
            baseline_evidence=None,
            prior_evidence=None,
            state_dir=workspace / "state",
        )


def test_claim_manifest_rejects_unknown_evidence_gate(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("known-gate")],
        target=_target(target_id="claim-gate-test"),
    )
    claims = workspace / "governance" / "claims" / "test-claims.json"
    payload = json.loads(claims.read_text(encoding="utf-8"))
    payload["claims"][0]["required_gates"] = ["missing-gate"]
    claims.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown required_gates"):
        controller.run_loop(
            workspace,
            policy,
            mode="static",
            evidence_dir=workspace / "evidence",
            rerun_from=None,
            target_path=target,
            baseline=False,
            baseline_evidence=None,
            prior_evidence=None,
            state_dir=workspace / "state",
        )



def test_claim_manifest_rejects_missing_file_evidence_ref(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("known-gate")],
        target=_target(target_id="claim-missing-evidence-test"),
    )
    claims = workspace / "governance" / "claims" / "test-claims.json"
    payload = json.loads(claims.read_text(encoding="utf-8"))
    payload["claims"][0]["evidence_refs"] = ["tests/does-not-exist.py"]
    claims.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="evidence ref does not exist"):
        controller.run_loop(
            workspace, policy, mode="static", evidence_dir=workspace / "evidence",
            rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
            prior_evidence=None, state_dir=workspace / "state",
        )



def test_claim_manifest_rejects_unknown_test_selector(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("known-gate")],
        target=_target(target_id="claim-missing-selector-test"),
    )
    test_file = workspace / "tests" / "test_claim_proof.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_real_proof():\n    assert True\n", encoding="utf-8")
    claims = workspace / "governance" / "claims" / "test-claims.json"
    payload = json.loads(claims.read_text(encoding="utf-8"))
    payload["claims"][0]["evidence_refs"] = [
        "tests/test_claim_proof.py::test_invented_proof"
    ]
    claims.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="test selector does not exist"):
        controller.run_loop(
            workspace, policy, mode="static", evidence_dir=workspace / "evidence",
            rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
            prior_evidence=None, state_dir=workspace / "state",
        )


def test_high_risk_counterexample_requires_direct_selector_or_gate_log(tmp_path: Path) -> None:
    controller = _controller()
    step = _step("test-gate")
    step["category"] = "unit-contract"
    workspace, policy, target = _workspace(
        tmp_path, steps=[step],
        target=_target(target_id="claim-direct-proof-test", minimum_mode="quick"),
    )
    test_file = workspace / "tests" / "test_claim_proof.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_real_proof():\n    assert True\n", encoding="utf-8")
    claims = workspace / "governance" / "claims" / "test-claims.json"
    payload = json.loads(claims.read_text(encoding="utf-8"))
    payload["claims"][0].update(
        {
            "risk": "P1",
            "required_mode": "quick",
            "evidence_kind": "counterexample",
            "required_gates": ["test-gate"],
            "evidence_refs": ["tests/test_claim_proof.py"],
        }
    )
    claims.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="direct executable evidence"):
        controller.run_loop(
            workspace, policy, mode="quick", evidence_dir=workspace / "evidence",
            rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
            prior_evidence=None, state_dir=workspace / "state",
        )


def test_claim_selector_must_execute_in_current_run_before_verification(tmp_path: Path) -> None:
    controller = _controller()
    step = _step("test-gate")
    step["category"] = "unit-contract"
    workspace, policy, target = _workspace(
        tmp_path, steps=[step],
        target=_target(target_id="claim-current-run-proof-test", minimum_mode="quick"),
    )
    test_file = workspace / "tests" / "test_claim_proof.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_real_proof():\n    assert True\n", encoding="utf-8")
    claims = workspace / "governance" / "claims" / "test-claims.json"
    payload = json.loads(claims.read_text(encoding="utf-8"))
    payload["claims"][0].update(
        {
            "risk": "P1",
            "required_mode": "quick",
            "evidence_kind": "counterexample",
            "required_gates": ["test-gate"],
            "evidence_refs": ["tests/test_claim_proof.py::test_real_proof"],
        }
    )
    claims.write_text(json.dumps(payload), encoding="utf-8")

    summary = controller.run_loop(
        workspace, policy, mode="quick", evidence_dir=workspace / "evidence",
        rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
        prior_evidence=None, state_dir=workspace / "state",
    )

    assert summary["decision"] == controller.PASS
    assert summary["completion_eligible"] is False
    assert summary["loop_status"] != "CI_VERIFIED"
    assert summary["claim_results"][0]["status"] == "NOT_EXECUTED"
    assert summary["claim_results"][0]["evidence_ref_statuses"] == {
        "tests/test_claim_proof.py::test_real_proof": "NOT_EXECUTED"
    }

def test_claim_manifest_rejects_unbound_gate_log_ref(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("known-gate"), _step("other-gate")],
        target=_target(target_id="claim-unbound-log-test"),
    )
    claims = workspace / "governance" / "claims" / "test-claims.json"
    payload = json.loads(claims.read_text(encoding="utf-8"))
    payload["claims"][0]["required_gates"] = ["known-gate"]
    payload["claims"][0]["evidence_refs"] = ["gate-log:other-gate"]
    claims.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="gate-log refs must name required_gates"):
        controller.run_loop(
            workspace, policy, mode="static", evidence_dir=workspace / "evidence",
            rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
            prior_evidence=None, state_dir=workspace / "state",
        )


def test_counterexample_claim_requires_a_test_or_adversarial_gate(tmp_path: Path) -> None:
    controller = _controller()
    step = _step("architecture-only")
    step["category"] = "architecture"
    workspace, policy, target = _workspace(
        tmp_path, steps=[step],
        target=_target(target_id="claim-counterexample-category-test", minimum_mode="quick"),
    )
    claims = workspace / "governance" / "claims" / "test-claims.json"
    payload = json.loads(claims.read_text(encoding="utf-8"))
    payload["claims"][0]["risk"] = "P1"
    payload["claims"][0]["evidence_kind"] = "counterexample"
    claims.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="no test or adversarial evidence gate"):
        controller.run_loop(
            workspace, policy, mode="quick", evidence_dir=workspace / "evidence",
            rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
            prior_evidence=None, state_dir=workspace / "state",
        )


def test_release_provenance_claim_requires_release_artifact_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller()
    monkeypatch.setenv("QUALITY_EVIDENCE_SIGNING_KEY", "test-release-evidence-signing-key-123456789")
    step = _step("integration-only")
    step["category"] = "integration"
    workspace, policy, target = _workspace(
        tmp_path, steps=[step],
        target=_target(target_id="claim-release-category-test", minimum_mode="release"),
    )
    claims = workspace / "governance" / "claims" / "test-claims.json"
    payload = json.loads(claims.read_text(encoding="utf-8"))
    payload["claims"][0]["risk"] = "P1"
    payload["claims"][0]["evidence_kind"] = "release-provenance"
    claims.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="no release artifact evidence gate"):
        controller.run_loop(
            workspace, policy, mode="release", evidence_dir=workspace / "evidence",
            rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
            prior_evidence=None, state_dir=workspace / "state",
        )

def test_targeted_lower_mode_cannot_verify_a_higher_mode_claim(tmp_path: Path) -> None:
    controller = _controller()
    step = _step("release-proof")
    step["category"] = "release"
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[step],
        target=_target(target_id="claim-targeted-mode-test", minimum_mode="release"),
    )
    claims = workspace / "governance" / "claims" / "test-claims.json"
    payload = json.loads(claims.read_text(encoding="utf-8"))
    payload["claims"][0]["risk"] = "P1"
    payload["claims"][0]["required_mode"] = "release"
    payload["claims"][0]["evidence_kind"] = "release-provenance"
    claims.write_text(json.dumps(payload), encoding="utf-8")

    summary = controller.run_loop(
        workspace,
        policy,
        mode="quick",
        evidence_dir=workspace / "evidence",
        rerun_from="release-proof",
        target_path=target,
        baseline=False,
        baseline_evidence=None,
        prior_evidence=None,
        state_dir=workspace / "state",
    )
    assert summary["decision"] == controller.PASS
    assert summary["completion_eligible"] is False
    assert summary["claim_results"][0]["status"] == "INSUFFICIENT_MODE"
    assert summary["unverified_claim_ids"] == ["TEST-CLAIM-001"]


def test_full_run_records_every_claim_as_verified_before_completion(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("proof-a"), _step("proof-b", depends_on=["proof-a"])],
        target=_target(target_id="claim-completion-test", minimum_mode="quick"),
    )
    summary = controller.run_loop(
        workspace,
        policy,
        mode="quick",
        evidence_dir=workspace / "evidence",
        rerun_from=None,
        target_path=target,
        baseline=False,
        baseline_evidence=None,
        prior_evidence=None,
        state_dir=workspace / "state",
    )
    assert summary["completion_eligible"] is True
    assert summary["target_minimum_mode_declared"] == "quick"
    assert summary["target_minimum_mode_derived"] == "quick"
    assert summary["claim_results"][0]["status"] == "VERIFIED"
    assert summary["unverified_claim_ids"] == []


def test_concurrent_controller_is_rejected_without_touching_active_evidence(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("only")],
        target=_target(target_id="exclusive-run-test"),
    )
    evidence_dir = workspace / "active-evidence"
    state_dir = workspace / "state"
    evidence_dir.mkdir()
    sentinel = evidence_dir / "active-owner.txt"
    sentinel.write_text("unchanged\n", encoding="utf-8")

    with controller._exclusive_quality_run(
        evidence_dir=evidence_dir,
        state_dir=state_dir,
        target_path=target,
    ):
        with pytest.raises(controller.QualityRunConflictError, match="already owns"):
            controller.run_loop(
                workspace,
                policy,
                mode="static",
                evidence_dir=evidence_dir,
                rerun_from=None,
                target_path=target,
                baseline=False,
                baseline_evidence=None,
                prior_evidence=None,
                state_dir=state_dir,
            )

    assert sentinel.read_text(encoding="utf-8") == "unchanged\n"
    assert not (evidence_dir / "run-summary.json").exists()


def test_nonempty_evidence_directory_is_rejected_instead_of_overwritten(tmp_path: Path) -> None:
    controller = _controller()
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[_step("only")],
        target=_target(target_id="immutable-evidence-output-test"),
    )
    evidence_dir = workspace / "existing-evidence"
    evidence_dir.mkdir()
    sentinel = evidence_dir / "prior-proof.json"
    sentinel.write_text('{"proof":"keep"}\n', encoding="utf-8")

    with pytest.raises(controller.QualityRunConflictError, match="new and empty"):
        controller.run_loop(
            workspace,
            policy,
            mode="static",
            evidence_dir=evidence_dir,
            rerun_from=None,
            target_path=target,
            baseline=False,
            baseline_evidence=None,
            prior_evidence=None,
            state_dir=workspace / "state",
        )

    assert sentinel.read_text(encoding="utf-8") == '{"proof":"keep"}\n'
    assert list(evidence_dir.iterdir()) == [sentinel]


def test_environment_block_propagates_through_skipped_release_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller()
    monkeypatch.setenv(
        "QUALITY_EVIDENCE_SIGNING_KEY",
        "test-protected-evidence-signing-key-123456789",
    )
    monkeypatch.delenv("QUALITY_LOOP_PROTECTED_SERVICE", raising=False)
    environment_gate = _step("protected-service")
    environment_gate["environment"] = {"variables": ["QUALITY_LOOP_PROTECTED_SERVICE"]}
    release_gate = _step("release-artifact", depends_on=["protected-service"])
    release_gate["category"] = "release"
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[environment_gate, release_gate],
        target=_target(
            target_id="transitive-environment-claim-test",
            minimum_mode="release",
        ),
    )
    claims = workspace / "governance" / "claims" / "test-claims.json"
    payload = json.loads(claims.read_text(encoding="utf-8"))
    payload["claims"][0].update(
        {
            "risk": "P1",
            "required_mode": "release",
            "evidence_kind": "release-provenance",
            "required_gates": ["release-artifact"],
            "evidence_refs": ["gate-log:release-artifact"],
        }
    )
    claims.write_text(json.dumps(payload), encoding="utf-8")

    summary = controller.run_loop(
        workspace,
        policy,
        mode="release",
        evidence_dir=workspace / "evidence",
        rerun_from=None,
        target_path=target,
        baseline=False,
        baseline_evidence=None,
        prior_evidence=None,
        state_dir=workspace / "state",
    )

    assert summary["decision"] == controller.BLOCKED
    assert summary["loop_status"] == "BLOCKED_BY_ENVIRONMENT"
    assert summary["claim_results"][0]["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert summary["claim_results"][0]["environment_blocked_gates"] == ["release-artifact"]


def test_real_failure_dominates_a_mixed_environment_dependency_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller()
    monkeypatch.setenv(
        "QUALITY_EVIDENCE_SIGNING_KEY",
        "test-protected-evidence-signing-key-123456789",
    )
    monkeypatch.delenv("QUALITY_LOOP_PROTECTED_SERVICE", raising=False)
    environment_gate = _step("protected-service")
    environment_gate["environment"] = {"variables": ["QUALITY_LOOP_PROTECTED_SERVICE"]}
    failing_gate = _step("real-code-failure", fail=True)
    release_gate = _step(
        "release-artifact",
        depends_on=["protected-service", "real-code-failure"],
    )
    release_gate["category"] = "release"
    workspace, policy, target = _workspace(
        tmp_path,
        steps=[environment_gate, failing_gate, release_gate],
        target=_target(
            target_id="mixed-environment-failure-claim-test",
            minimum_mode="release",
        ),
    )
    claims = workspace / "governance" / "claims" / "test-claims.json"
    payload = json.loads(claims.read_text(encoding="utf-8"))
    payload["claims"][0].update(
        {
            "risk": "P1",
            "required_mode": "release",
            "evidence_kind": "release-provenance",
            "required_gates": ["release-artifact"],
            "evidence_refs": ["gate-log:release-artifact"],
        }
    )
    claims.write_text(json.dumps(payload), encoding="utf-8")

    summary = controller.run_loop(
        workspace,
        policy,
        mode="release",
        evidence_dir=workspace / "evidence",
        rerun_from=None,
        target_path=target,
        baseline=False,
        baseline_evidence=None,
        prior_evidence=None,
        state_dir=workspace / "state",
    )

    assert summary["decision"] == controller.FAIL
    assert summary["loop_status"] == "CI_FAILED"
    assert summary["claim_results"][0]["status"] == "FAILED"
    assert summary["claim_results"][0]["environment_blocked_gates"] == []
