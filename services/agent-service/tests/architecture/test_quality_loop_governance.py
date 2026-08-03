from __future__ import annotations

import importlib.util
import json
import shutil
import os
import signal
import subprocess
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

from tests.support.paths import workspace_root


def _load_script(name: str):
    root = workspace_root(__file__)
    path = root / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quality_loop_policy_exposes_static_quick_and_release_steps() -> None:
    root = workspace_root(__file__)
    policy = json.loads((root / "governance" / "quality-loop-policy.json").read_text(encoding="utf-8"))
    steps_by_mode = {mode: {step["name"] for step in policy["steps"] if mode in step.get("modes", [])} for mode in ["static", "quick", "release"]}
    assert {"skill-package-verify", "architecture-convergence", "module-vertical-closure", "version-consistency"} <= steps_by_mode["static"]
    assert "python-test-suites" in steps_by_mode["quick"]
    assert {"frontend-vitest", "frontend-build"} <= steps_by_mode["release"]
    steps = {step["id"]: step for step in policy["steps"]}
    assert steps["frontend-vitest"]["argv"] == ["{npm}", "run", "test:ci"]
    assert steps["frontend-vitest"]["env"] == {
        "VITEST_JUNIT_PATH": "{evidence_dir}/junit/frontend-vitest.xml",
        "VITEST_COVERAGE_DIR": "{evidence_dir}/coverage/frontend",
    }
    assert {"python-test-suites", "frontend-vitest"} <= set(steps["coverage-baseline"]["depends_on"])


def test_quality_loop_lists_steps_without_running_nested_tests() -> None:
    root = workspace_root(__file__)
    result = subprocess.run(
        [sys.executable, "-B", str(root / "scripts" / "quality_loop.py"), "--workspace-root", str(root), "--mode", "static", "--list-steps"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert "strong-context-case-catalog" in payload["steps"]


def test_targeted_dependency_closure_includes_prerequisites_of_new_downstream_steps() -> None:
    controller = _load_script("quality_loop.py")
    steps = [
        {"id": "root-prerequisite", "depends_on": []},
        {"id": "downstream-prerequisite", "depends_on": []},
        {"id": "root", "depends_on": ["root-prerequisite"]},
        {
            "id": "downstream",
            "depends_on": ["root", "downstream-prerequisite"],
        },
        {"id": "final", "depends_on": ["downstream"]},
    ]
    selected = controller._downstream_steps(steps, "root")
    assert [step["id"] for step in selected] == [
        "root-prerequisite",
        "downstream-prerequisite",
        "root",
        "downstream",
        "final",
    ]


def test_module_closure_static_checker_passes_current_manifests() -> None:
    root = workspace_root(__file__)
    report = _load_script("verify_module_closure.py").verify(root)
    assert report["status"] == "PASS", report


def test_strong_context_case_catalog_has_required_counterexamples() -> None:
    root = workspace_root(__file__)
    report = _load_script("verify_strong_context_cases.py").verify(root)
    assert report["status"] == "PASS", report
    assert report["case_count"] >= 8


def _first_semantic_turn() -> tuple[object, dict, str]:
    root = workspace_root(__file__)
    verifier = _load_script("verify_strong_context_cases.py")
    payload = json.loads((root / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json").read_text(encoding="utf-8"))
    case = payload["cases"][0]
    return verifier, deepcopy(case["execution_contract"]["turn_contracts"][0]), case["id"]


def _turn_errors(verifier, contract: dict, case_id: str) -> list[str]:
    errors: list[str] = []
    verifier._verify_turn(
        case_id=case_id,
        turn_index=1,
        user_text=contract["user_text"],
        contract=contract,
        semantic=True,
        errors=errors,
        tool_counter=Counter(),
        workflow_levels=set(),
    )
    return errors


def test_strong_context_gate_rejects_missing_runtime_goal_binding() -> None:
    verifier, contract, case_id = _first_semantic_turn()
    contract["model_steps"][1]["tool_calls"][0]["args"].pop("goal_ids")
    errors = _turn_errors(verifier, contract, case_id)
    assert any(error.startswith("runtime_goal_binding_missing_or_unknown:") for error in errors)


def test_strong_context_gate_rejects_semantically_swapped_goal_bindings() -> None:
    verifier, contract, case_id = _first_semantic_turn()
    calls = contract["model_steps"][1]["tool_calls"]
    calls[0]["args"]["goal_ids"], calls[1]["args"]["goal_ids"] = calls[1]["args"]["goal_ids"], calls[0]["args"]["goal_ids"]
    errors = _turn_errors(verifier, contract, case_id)
    assert any(error.startswith("semantic_runtime_goal_binding_mismatch:") for error in errors)


def test_strong_context_gate_rejects_oracle_capability_goal_type_mismatch() -> None:
    root = workspace_root(__file__)
    verifier, contract, case_id = _first_semantic_turn()
    oracle_goal = contract["goal_oracle"][0]
    goal_id = oracle_goal["oracle_id"]
    oracle_goal["goal_type"] = "consult"
    planner_goal = next(
        goal
        for goal in contract["model_steps"][0]["tool_calls"][0]["args"]["goals"]
        if goal["goal_id"] == goal_id
    )
    planner_goal["goal_type"] = "consult"
    errors: list[str] = []
    verifier._verify_turn(
        case_id=case_id,
        turn_index=1,
        user_text=contract["user_text"],
        contract=contract,
        semantic=True,
        errors=errors,
        tool_counter=Counter(),
        workflow_levels=set(),
        goal_completion_types_by_tool=verifier._capability_goal_completion_types(root),
    )
    assert any(error.startswith("semantic_goal_type_incompatible_with_capability:") for error in errors)


def test_strong_context_gate_rejects_runtime_modal_consultation_declared_as_query() -> None:
    root = workspace_root(__file__)
    verifier = _load_script("verify_strong_context_cases.py")
    payload = json.loads((
        root / "services/agent-service/tests/context/strong_context_cases/conversation_runtime_contract_suite_v20_4.json"
    ).read_text(encoding="utf-8"))
    contract = deepcopy(payload["cases"][0]["execution_contract"]["turn_contracts"][0])
    text = "这个订单能退款吗"
    contract["user_text"] = text
    planner_goal = contract["model_steps"][0]["tool_calls"][0]["args"]["goals"][0]
    planner_goal["evidence_span"] = text
    planner_goal["goal_type"] = "query"
    errors: list[str] = []

    verifier._verify_turn(
        case_id="modal-runtime-mutation",
        turn_index=1,
        user_text=text,
        contract=contract,
        semantic=False,
        errors=errors,
        tool_counter=Counter(),
        workflow_levels=set(),
        goal_completion_types_by_tool=verifier._capability_goal_completion_types(root),
        goal_type_patterns=verifier._goal_type_patterns(root),
    )

    assert any(error.startswith("planner_goal_type_conflicts_with_user_text:") for error in errors)


def test_quality_loop_does_not_count_round_number_change_as_a_repair(tmp_path) -> None:
    controller = _load_script("quality_loop.py")
    source = tmp_path / "src" / "runtime.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    target = tmp_path / "governance" / "target.md"
    target.parent.mkdir()
    target.write_text("当前轮次：1\n", encoding="utf-8")
    baseline = {"workspace_snapshot": controller._workspace_snapshot(tmp_path)}

    target.write_text("当前轮次：2\n", encoding="utf-8")
    bookkeeping_only, paths = controller._repair_change_fingerprint(
        tmp_path,
        baseline=baseline,
        allowed_paths=("src/**", "governance/**"),
        target_path=target,
    )
    assert paths == []

    source.write_text("VALUE = 2\n", encoding="utf-8")
    actual_repair, paths = controller._repair_change_fingerprint(
        tmp_path,
        baseline=baseline,
        allowed_paths=("src/**", "governance/**"),
        target_path=target,
    )
    assert paths == ["src/runtime.py"]
    assert actual_repair != bookkeeping_only


def test_version_consistency_static_checker_passes_current_release() -> None:
    root = workspace_root(__file__)
    report = _load_script("verify_version_consistency.py").verify(root)
    assert report["status"] == "PASS", report


def test_release_workflow_reexecutes_full_release_without_prior_pass_reuse() -> None:
    root = workspace_root(__file__)
    workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "--mode release" in workflow
    assert "--prior-evidence" not in workflow
    assert "--rerun-from" not in workflow
    assert "scripts/build_clean_release.py" in workflow
    assert "QUALITY_EVIDENCE_SIGNING_KEY" in workflow
    target_creator = _load_script("create_ci_quality_target.py")
    assert target_creator.WORKFLOW_MINIMUM_MODE["quality-integration"] == "integration"
    assert target_creator.WORKFLOW_MINIMUM_MODE["release-quality"] == "release"
    assert "clean-release-preflight" in target_creator.WORKFLOW_CLAIM_GATES["release-quality"]
    assert "${{ github.workspace }}/.quality/targets/quality-target-release.md" in workflow
    assert "${{ runner.temp }}/quality-target-release.md" not in workflow


def test_protected_release_uses_the_unified_protected_runtime_controller() -> None:
    root = workspace_root(__file__)
    workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    protected = workflow.split("  protected-release:", 1)[1]
    prerequisites = protected.split("- name: Validate protected runtime prerequisites", 1)[1].split(
        "- name: Run every release gate", 1
    )[0]
    release_gate = protected.split("- name: Run every release gate", 1)[1].split(
        "- name: Upload signed production evidence", 1
    )[0]

    assert "APP_PROFILE: local" not in protected
    assert "scripts/run_production_release.py" in release_gate
    assert "services/agent-service/.venv/bin/python" in release_gate
    assert '--target "$QUALITY_TARGET"' in release_gate
    for required in (
        "APP_PROFILE: preprod",
        "BUSINESS_DB_BACKEND: postgres",
        "BUSINESS_DATABASE_URL: postgresql://",
        "BUSINESS_REQUIRE_ACTOR_SIGNATURE: 'true'",
        "AGENT_AUTH_PROVIDER: jwt_hs256",
        "AGENT_DB_BACKEND: postgres",
        "CHECKPOINT_BACKEND: postgres",
        "RAG_BACKEND: pgvector",
        "DOCUMENT_JOB_BACKEND: sqlalchemy",
        "DOCUMENT_OBJECT_STORE_BACKEND: shared_filesystem",
        "STATE_CONTRACT_MODE: strict",
        "CAPABILITY_SEMANTIC_VERIFIER_MODE: model",
        "GOAL_ALIGNMENT_VERIFIER_MODE: model",
        "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE: model",
    ):
        assert required in prerequisites


def test_architecture_policy_requires_business_postgres_configuration() -> None:
    root = workspace_root(__file__)
    policy = json.loads((root / "governance" / "architecture-policy.json").read_text(encoding="utf-8"))
    template = next(
        item
        for item in policy["configuration"]["templates"]
        if item["path"] == "services/business-service/.env.example"
    )
    assert {"BUSINESS_DB_BACKEND", "BUSINESS_DATABASE_URL"} <= set(template["required_variables"])


def _temporary_quality_target(
    tmp_path: Path,
    *,
    target_id: str,
    target_kind: str,
    fail_without_environment: str | None = None,
) -> tuple[Path, Path, Path]:
    workspace = tmp_path
    (workspace / "governance/claims").mkdir(parents=True)
    (workspace / "VERSION").write_text("test\n", encoding="utf-8")
    (workspace / "source.txt").write_text("baseline\n", encoding="utf-8")
    claim_id = "TEST-REPAIR-001"
    claims = {
        "schema_version": 1,
        "target_id": target_id,
        "claims": [
            {
                "id": claim_id,
                "statement": "The temporary regression gate proves the repair transition.",
                "risk": "P2",
                "required_mode": "static",
                "evidence_kind": "static-contract",
                "required_gates": ["proof"],
                "evidence_refs": ["gate-log:proof"],
                "owner": "temporary-test",
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
- 目标类型：{target_kind}

验证临时质量合同。

## 允许范围
- 允许变更路径：source.txt
- 新增抽象记录：无

## 禁止范围
不修改其他文件。

## 验收条件
- 最低质量模式：static
- 声明清单：governance/claims/test.json
- 验收 ID：{claim_id}

## 基线
修复前 baseline。

## 修复轮次
- 最大轮次：2
- 当前轮次：1
- 失败后：修改唯一 Owner。
""",
        encoding="utf-8",
    )
    code = (
        f"import os,sys;sys.exit(0 if os.getenv('{fail_without_environment}') else 1)"
        if fail_without_environment
        else "pass"
    )
    step = {
        "id": "proof",
        "name": "proof",
        "modes": ["static"],
        "kind": "shell",
        "argv": [sys.executable, "-S", "-c", code],
        "owner": "temporary-test",
        "category": "counterexample-regression",
        "blocking_level": "required",
        "repair_playbook": "repair source.txt",
        "rerun_contract": "dependency_closure_then_downstream",
        "depends_on": [],
        "environment": {},
        "timeout_seconds": 20,
    }
    policy = workspace / "policy.json"
    policy.write_text(json.dumps({"version": "test", "steps": [step]}), encoding="utf-8")
    return workspace, policy, target


def test_target_acceptance_ids_must_exactly_match_claim_ids(tmp_path: Path) -> None:
    controller = _load_script("quality_loop.py")
    workspace, _policy, target = _temporary_quality_target(
        tmp_path,
        target_id="acceptance-set-test",
        target_kind="certification",
    )
    text = target.read_text(encoding="utf-8").replace(
        "验收 ID：TEST-REPAIR-001",
        "验收 ID：TEST-REPAIR-001, OMITTED-CLAIM-002",
    )
    target.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="acceptance IDs"):
        controller._parse_target(target, workspace=workspace)


def test_repair_target_rejects_green_baseline_and_no_change_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _load_script("quality_loop.py")
    green = tmp_path / "green"
    green.mkdir()
    workspace, policy, target = _temporary_quality_target(
        green,
        target_id="green-repair-baseline",
        target_kind="repair",
    )
    with pytest.raises(ValueError, match="repair target baseline"):
        controller.run_loop(
            workspace,
            policy,
            mode="static",
            evidence_dir=workspace / "baseline",
            rerun_from=None,
            target_path=target,
            baseline=True,
            baseline_evidence=None,
            prior_evidence=None,
            state_dir=workspace / "state",
        )

    no_change = tmp_path / "no-change"
    no_change.mkdir()
    workspace, policy, target = _temporary_quality_target(
        no_change,
        target_id="no-change-repair",
        target_kind="repair",
        fail_without_environment="QUALITY_TEST_REPAIRED",
    )
    baseline = workspace / "baseline"
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
        state_dir=workspace / "state",
    )
    assert summary["decision"] == controller.FAIL
    monkeypatch.setenv("QUALITY_TEST_REPAIRED", "true")
    with pytest.raises(ValueError, match="actual in-scope candidate change"):
        controller.run_loop(
            workspace,
            policy,
            mode="static",
            evidence_dir=workspace / "verification",
            rerun_from=None,
            target_path=target,
            baseline=False,
            baseline_evidence=baseline,
            prior_evidence=None,
            state_dir=workspace / "state",
        )
    (workspace / "source.txt").write_text("repaired\n", encoding="utf-8")
    converged = controller.run_loop(
        workspace,
        policy,
        mode="static",
        evidence_dir=workspace / "verification-with-repair",
        rerun_from=None,
        target_path=target,
        baseline=False,
        baseline_evidence=baseline,
        prior_evidence=None,
        state_dir=workspace / "state",
    )
    assert converged["loop_status"] == "CONVERGED"
    assert converged["baseline_transition_unverified_claim_ids"] == []


def test_protected_goal_smoke_accepts_schema_compliant_goals_without_expected_tools() -> None:
    smoke = _load_script("../services/agent-service/scripts/verify_preprod_conversation_smoke.py")
    smoke._match_oracle(
        case_id="schema-compliant",
        oracle=[
            {
                "oracle_id": "g1",
                "evidence_span": "查订单",
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "required_tools": ["list_orders"],
            }
        ],
        goals=[
            {
                "goal_id": "g1",
                "description": "查询订单",
                "evidence_span": "查订单",
                "goal_type": "query",
                "required": True,
                "depends_on": [],
            }
        ],
    )


def test_protected_goal_smoke_accepts_literal_span_with_surrounding_user_wording() -> None:
    smoke = _load_script("../services/agent-service/scripts/verify_preprod_conversation_smoke.py")
    smoke._match_oracle(
        case_id="literal-span-extension",
        oracle=[
            {
                "oracle_id": "refund-history",
                "evidence_span": "鼠标订单的退款记录",
                "goal_type": "query",
                "required": True,
                "depends_on": [],
            }
        ],
        goals=[
            {
                "goal_id": "model-goal",
                "evidence_span": "查下鼠标订单的退款记录",
                "goal_type": "query",
                "required": True,
                "depends_on": [],
            }
        ],
    )


def test_protected_goal_smoke_rejects_fuzzy_or_ambiguous_span_matching() -> None:
    smoke = _load_script("../services/agent-service/scripts/verify_preprod_conversation_smoke.py")
    with pytest.raises(RuntimeError, match="no unique model goal"):
        smoke._match_oracle(
            case_id="fuzzy-is-forbidden",
            oracle=[
                {
                    "oracle_id": "refund-history",
                    "evidence_span": "鼠标订单的退款记录",
                    "goal_type": "query",
                    "required": True,
                    "depends_on": [],
                }
            ],
            goals=[
                {
                    "goal_id": "model-goal",
                    "evidence_span": "鼠标订单的物流记录",
                    "goal_type": "query",
                    "required": True,
                    "depends_on": [],
                }
            ],
        )
    with pytest.raises(RuntimeError, match="no unique model goal"):
        smoke._match_oracle(
            case_id="ambiguous-containment",
            oracle=[
                {
                    "oracle_id": "cancel",
                    "evidence_span": "取消",
                    "goal_type": "action",
                    "required": True,
                    "depends_on": [],
                },
                {
                    "oracle_id": "ship",
                    "evidence_span": "继续发货",
                    "goal_type": "action",
                    "required": True,
                    "depends_on": [],
                },
            ],
            goals=[
                {
                    "goal_id": "combined-a",
                    "evidence_span": "取消，然后继续发货",
                    "goal_type": "action",
                    "required": True,
                    "depends_on": [],
                },
                {
                    "goal_id": "combined-b",
                    "evidence_span": "取消并继续发货",
                    "goal_type": "action",
                    "required": True,
                    "depends_on": [],
                },
            ],
        )


def test_process_group_cleanup_handles_permission_error_after_parent_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _load_script("quality_loop.py")

    class CompletedProcess:
        pid = 4242

    def fake_killpg(_pid: int, requested_signal: int) -> None:
        if requested_signal == signal.SIGTERM:
            return
        if requested_signal == 0:
            raise PermissionError("process group is no longer inspectable")
        raise AssertionError(requested_signal)

    monkeypatch.setattr(os, "killpg", fake_killpg)
    controller._terminate_process_group(CompletedProcess(), grace_seconds=0.01)


def test_module_closure_rejects_capability_without_executable_test_contract(
    tmp_path: Path,
) -> None:
    verifier = _load_script("verify_module_closure.py")
    module = tmp_path / "services/agent-service/src/agent_modules/demo"
    (module / "capabilities").mkdir(parents=True)
    (module / "capabilities/list_demo.py").write_text(
        "CONTRACT='demo.list'\nSCHEMA={}\nPRESENTATION_CONTRACT='runtime.resource_list@1'\n"
        "def execute(): return None\nTOOL='list_demo'\n",
        encoding="utf-8",
    )
    contracts = tmp_path / "services/agent-service/src/agent_core/presentation/contracts"
    contracts.mkdir(parents=True)
    (contracts / "runtime_resource_list_v1.json").write_text("{}", encoding="utf-8")
    tests = tmp_path / "services/agent-service/tests/test_demo.py"
    tests.parent.mkdir(parents=True)
    tests.write_text("def test_demo(): pass\n", encoding="utf-8")
    (module / "module_manifest.json").write_text(
        json.dumps(
            {
                "module_id": "demo",
                "ownership": {"presentation_contracts": ["runtime.resource_list@1"]},
                "unsupported_behavior": "never substitute another capability",
                "tests": ["tests/test_demo.py"],
                "capabilities": [
                    {
                        "key": "demo.list",
                        "tool_name": "list_demo",
                        "executor": "capabilities/list_demo.py",
                        "presentation_contract": "runtime.resource_list@1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = verifier.verify(tmp_path)
    assert report["status"] == "FAIL"
    assert any("test_contract" in error for error in report["errors"])


def test_ci_target_preserves_source_claim_identity(tmp_path: Path) -> None:
    root = workspace_root(__file__)
    source = tmp_path / "governance/claims/source.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_id": "repair-source",
                "claims": [
                    {
                        "id": "SOURCE-CLAIM-001",
                        "statement": "The original repair claim remains visible in CI.",
                        "risk": "P1",
                        "required_mode": "quick",
                        "evidence_kind": "counterexample",
                        "required_gates": ["python-test-suites"],
                        "evidence_refs": ["gate-log:python-test-suites"],
                        "owner": "tests",
                        "closure_requirement": "current-pass",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    target = tmp_path / ".quality/target.md"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(root / "scripts/create_ci_quality_target.py"),
            "--output",
            str(target),
            "--ref",
            "abc123",
            "--workflow",
            "quality-quick",
            "--claims-source",
            "governance/claims/source.json",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    generated = json.loads(target.with_suffix(".claims.json").read_text(encoding="utf-8"))
    assert [claim["id"] for claim in generated["claims"]] == ["SOURCE-CLAIM-001"]
    assert generated["source_claim_manifest"]["target_id"] == "repair-source"
    assert generated["source_claim_manifest"]["fingerprint"]
    assert "验收 ID：SOURCE-CLAIM-001" in target.read_text(encoding="utf-8")


def test_project_requirement_profile_rejects_omitted_requirement(tmp_path: Path) -> None:
    controller = _load_script("quality_loop.py")
    workspace, _policy, target = _temporary_quality_target(
        tmp_path,
        target_id="project-requirement-coverage",
        target_kind="certification",
    )
    catalog = workspace / "governance/requirements/project.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": {
                    "project-quick": ["REQ-ONE", "REQ-TWO"],
                },
                "requirements": [
                    {"id": "REQ-ONE", "statement": "first", "risk": "P1", "owner": "one", "required_mode": "quick"},
                    {"id": "REQ-TWO", "statement": "second", "risk": "P1", "owner": "two", "required_mode": "quick"},
                ],
            }
        ),
        encoding="utf-8",
    )
    claims_path = workspace / "governance/claims/test.json"
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    claims["requirement_catalog"] = "governance/requirements/project.json"
    claims["requirement_profile"] = "project-quick"
    claims["claims"][0]["requirement_ids"] = ["REQ-ONE"]
    claims_path.write_text(json.dumps(claims), encoding="utf-8")

    with pytest.raises(ValueError, match="REQ-TWO"):
        controller._parse_target(target, workspace=workspace)


def test_integration_runner_includes_business_postgres_suite() -> None:
    runner = _load_script("run_python_test_suites.py")
    specs = runner._suite_specs("integration")
    assert [(item["name"], item["cwd"]) for item in specs] == [
        ("agent-service-pytest", "services/agent-service"),
        ("business-service-pytest", "services/business-service"),
    ]


def test_ci_certification_rejects_unbound_regression_transition_claims(tmp_path: Path) -> None:
    creator = _load_script("create_ci_quality_target.py")
    source = tmp_path / "governance/claims/repair.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_id": "repair-source",
                "claims": [
                    {
                        "id": "REPAIR-TRANSITION-001",
                        "statement": "must retain red to green provenance",
                        "risk": "P1",
                        "required_mode": "quick",
                        "evidence_kind": "counterexample",
                        "required_gates": ["python-test-suites"],
                        "evidence_refs": ["gate-log:python-test-suites"],
                        "owner": "tests",
                        "closure_requirement": "regression-transition",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="regression-transition"):
        creator._source_claims(tmp_path, "governance/claims/repair.json", maximum_mode="quick")


def test_repair_orchestrator_rejects_mismatched_target_before_fixer(tmp_path: Path) -> None:
    controller = _load_script("quality_loop.py")
    orchestrator = _load_script("repair_loop.py")
    workspace, policy, target = _temporary_quality_target(
        tmp_path,
        target_id="repair-preflight-original",
        target_kind="repair",
        fail_without_environment="QUALITY_TEST_REPAIR_PREFLIGHT_READY",
    )
    baseline = workspace / ".quality/baseline"
    baseline_summary = controller.run_loop(
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
    assert baseline_summary["decision"] == controller.FAIL
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "repair-preflight-original", "repair-preflight-changed"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="target identity"):
        orchestrator.validate_repair_inputs(
            workspace=workspace,
            policy=policy,
            target=target,
            baseline=baseline,
        )


def test_protected_model_smoke_rejects_duplicate_goal_ids_and_wrong_base_response() -> None:
    conversation = _load_script(
        "../services/agent-service/scripts/verify_preprod_conversation_smoke.py"
    )
    with pytest.raises(RuntimeError, match="duplicate goal_id"):
        conversation._match_oracle(
            case_id="duplicate-goal-id",
            oracle=[
                {"oracle_id": "o1", "evidence_span": "查订单", "goal_type": "query", "required": True, "depends_on": []},
                {"oracle_id": "o2", "evidence_span": "申请退款", "goal_type": "action", "required": True, "depends_on": []},
            ],
            goals=[
                {"goal_id": "same", "evidence_span": "查订单", "goal_type": "query", "required": True, "depends_on": []},
                {"goal_id": "same", "evidence_span": "申请退款", "goal_type": "action", "required": True, "depends_on": []},
            ],
        )
    base = _load_script("../services/agent-service/scripts/verify_model_smoke.py")
    assert base._base_smoke_response_is_valid("model-smoke-ok") is True
    assert base._base_smoke_response_is_valid("some non-empty response") is False
    assert base.is_environmental_model_failure_category("http_402") is True
    assert base.is_environmental_model_failure_category("http_400") is False


def test_workspace_doctor_checks_locked_environment_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = workspace_root(__file__)
    doctor = _load_script("workspace_doctor.py")
    monkeypatch.setattr(
        doctor,
        "_uv_lock_check",
        lambda _workspace, project: (
            (False, {"project": project, "detail": "simulated lock drift"})
            if project.endswith("agent-service")
            else (True, {"project": project})
        ),
    )
    monkeypatch.setattr(
        doctor,
        "_frontend_lock_check",
        lambda _workspace, _npm: (True, {"mismatches": {}}),
    )
    report = doctor.inspect_workspace(root)
    assert {"agent_lock", "business_lock", "frontend_lock"} <= set(report["checks"])
    assert report["status"] == "FAIL"
    assert report["checks"]["agent_lock"]["status"] == "FAIL"


def test_architecture_guard_rejects_core_reverse_composition_dependencies() -> None:
    root = workspace_root(__file__)
    verifier = _load_script("../architecture-skill/scripts/verify_convergence.py")
    policy = json.loads((root / "governance/architecture-policy.json").read_text(encoding="utf-8"))
    report = verifier.verify(root, policy)
    assert report["checks"]["reverse_composition_imports"] == []
    assert report["checks"]["package_dependency_cycles"] == []


def test_repair_orchestrator_builds_stable_issue_records(tmp_path: Path) -> None:
    orchestrator = _load_script("repair_loop.py")
    result = {
        "id": "broken-gate",
        "status": "FAIL",
        "owner": "runtime-owner",
        "category": "unit-contract",
        "stderr": "AssertionError: expected true",
        "stdout": "",
        "repair_playbook": "repair the owner",
        "started_at": "2026-01-01T00:00:00Z",
    }
    first = orchestrator.issue_records({"results": [result]}, evidence_dir=Path("/tmp/evidence-a"))
    result["started_at"] = "2026-02-02T00:00:00Z"
    second = orchestrator.issue_records({"results": [result]}, evidence_dir=Path("/tmp/evidence-b"))
    assert len(first) == 1
    assert first[0]["issue_id"] == second[0]["issue_id"]
    assert first[0]["status"] == "OPEN"
    assert first[0]["gate_id"] == "broken-gate"

    controller = _load_script("quality_loop.py")
    workspace, policy, target = _temporary_quality_target(
        tmp_path,
        target_id="repair-orchestrator-e2e",
        target_kind="repair",
    )
    root = workspace_root(__file__)
    for script_name in ("quality_loop.py", "source_paths.py", "repair_loop.py"):
        source = root / "scripts" / script_name
        destination = workspace / "scripts" / script_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copytree(
        root / "scripts" / "quality_control",
        workspace / "scripts" / "quality_control",
    )
    for controller_name in ("progress.py", "trusted_judge.py", "fixer_env.py", "issue_state.py"):
        source = root / "skill-system/controller" / controller_name
        destination = workspace / "skill-system/controller" / controller_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    trusted_judge_path = workspace / "skill-system/controller/trusted_judge.py"
    spec = importlib.util.spec_from_file_location("temporary_trusted_judge", trusted_judge_path)
    assert spec and spec.loader
    temporary_trusted_judge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(temporary_trusted_judge)
    temporary_trusted_judge.write_manifest(workspace)
    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload["steps"][0]["argv"] = [
        sys.executable,
        "-S",
        "-c",
        "from pathlib import Path; import sys; "
        "sys.exit(0 if Path('source.txt').read_text(encoding='utf-8').strip() == 'repaired' else 1)",
    ]
    policy.write_text(json.dumps(payload), encoding="utf-8")
    fixer = workspace / "fixer.py"
    fixer.write_text(
        "from pathlib import Path\nPath('source.txt').write_text('repaired\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    baseline = workspace / ".quality/baseline"
    baseline_summary = controller.run_loop(
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
    assert baseline_summary["decision"] == controller.FAIL
    completed = subprocess.run(
        [
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
        ],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    final_summary = json.loads(
        (workspace / ".quality/evidence/cycle-01-full/run-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert final_summary["loop_status"] == "CONVERGED"
    assert (workspace / "source.txt").read_text(encoding="utf-8") == "repaired\n"
    assert controller._verify_evidence_attestation(workspace, baseline) is None


def test_workspace_doctor_reports_readiness_without_mutating_workspace() -> None:
    root = workspace_root(__file__)
    controller = _load_script("quality_loop.py")
    doctor = _load_script("workspace_doctor.py")
    before = controller._workspace_snapshot(root)
    report = doctor.inspect_workspace(root)
    after = controller._workspace_snapshot(root)
    assert before["fingerprint"] == after["fingerprint"]
    assert {"python", "agent_test_runtime", "business_test_runtime", "npm", "frontend_dependencies", "required_templates"} <= set(report["checks"])
    assert report["status"] in {"PASS", "FAIL"}


def test_quality_controller_lock_rejection_is_machine_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    controller = _load_script("quality_loop.py")
    (tmp_path / "VERSION").write_text("test-version\n", encoding="utf-8")
    target = tmp_path / "target.md"
    target.write_text("# target\n", encoding="utf-8")
    evidence = tmp_path / "rejected-evidence"

    def reject_concurrent_run(*_args, **_kwargs):
        raise controller.QualityRunConflictError("another quality controller already owns the run lock")

    monkeypatch.setattr(controller, "run_loop", reject_concurrent_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "quality_loop.py",
            "--workspace-root",
            str(tmp_path),
            "--target",
            str(target),
            "--evidence-dir",
            str(evidence),
        ],
    )

    assert controller.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == controller.FAIL
    assert payload["loop_status"] == "CONCURRENT_RUN_REJECTED"
    assert payload["workspace_version"] == "test-version"
    assert payload["results"] == [
        {
            "id": "quality-controller-lock",
            "status": controller.FAIL,
            "stderr": "another quality controller already owns the run lock",
        }
    ]
    assert not evidence.exists()
