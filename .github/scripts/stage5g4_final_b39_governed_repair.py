from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import traceback
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = "toctionyan/fristTest"
BASE = "18d1795ebfd1a1ecb827ce12385b945bac1f74df"
CONTROLLER = "4e3beb4a6a4a8f2f86b60b95eb8166045b411615"
CHANGE = "repair-v20.17-b39-planrun-runtime-field-isolation"
PLAN_PATH = "services/agent-service/src/agent_core/lifecycle/plan_execution.py"
TEST_PATH = "services/agent-service/tests/runtime/test_plan_run_runtime_field_isolation.py"
PATCH_REL = Path("docs/governance/ci-probes/stage5g4-final-b39-governed/runtime-field-isolation.patch")
ORACLE_REL = Path("docs/governance/ci-probes/stage5g4-final-b39-governed/test_plan_run_runtime_field_isolation.py")
POLICY_RUNNER_REL = Path("docs/governance/ci-probes/stage5g4-final-b39-governed/b39_policy_runner.py")
PROBE_ARTIFACT_ID = 9020005978
PROBE_ARTIFACT_SHA = "498399fe586a0f0b29c8170c1f97df071ec8c3fdb3f1b6d7034698c8cfbf9a34"
EXPECTED_PLAN_SHA = "86d2b2adcf2770c7caa7502864dd2d2fdd31341c0054e2105a1f80a1e4b316d4"
EXPECTED_TEST_SHA = "d5e3a015767842d73f82da3ebaaf4122a64b5fc33a41f77a0226c5bb471074b4"

ROOT = Path.cwd().resolve()
RUNNER = ROOT / "runner"
SOURCE = ROOT / "source"
BASELINE = ROOT / "baseline-source"
CANDIDATE = ROOT / "candidate-source"
CONTROL = ROOT / "control"
INPUT_REL = Path(".quality/stage5g4-final-b39-input")
EVIDENCE = ROOT / "b39-governed-evidence"
PY = CONTROL / "services/agent-service/.venv/bin/python"
CLI = CONTROL / "skill-system/controller/change_contract_cli.py"
BRIDGE = CONTROL / "skill-system/controller/product_quality_bridge.py"
QUALITY_LOOP = CONTROL / "scripts/quality_loop.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(argv: list[str], *, cwd: Path | None = None, expect: int | None = 0) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, env=os.environ.copy())
    if expect is not None and cp.returncode != expect:
        raise RuntimeError(
            f"command failed rc={cp.returncode}: {argv}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    return cp


def git_head(path: Path) -> str:
    return run(["git", "-C", str(path), "rev-parse", "HEAD"]).stdout.strip()


def write_inputs(workspace: Path) -> dict[str, str]:
    oracle_sha = sha(RUNNER / ORACLE_REL)
    policy_runner_sha = sha(RUNNER / POLICY_RUNNER_REL)
    assert oracle_sha == EXPECTED_TEST_SHA
    inp = workspace / INPUT_REL
    inp.mkdir(parents=True, exist_ok=True)
    target = f"""# 目标
- 目标 ID：{CHANGE}
- 变更标识：{CHANGE}
- 执行上下文：local-change
- 目标类型：repair

修复 canonical B38 中 PlanRun revision 的结构身份污染：运行期 per-goal verification 结果不得进入 immutable PlanDefinition digest，已成功 effect 在语义结构未变时必须继续继承 SUCCEEDED 状态。本轮只允许修改既有 PlanRun runtime-field stripping Owner，并新增一个回归测试；不改变 B38 的 Context/Goal/Capability/Transaction 权威边界。

## 允许范围
- 允许变更路径：{PLAN_PATH}，{TEST_PATH}
- 新增抽象记录：无

## 禁止范围
不修改 skill-system/**、architecture-skill/**、governance/quality-loop-policy.json、governance/evidence/**、B38 其它产品源码、业务服务、事务协议或测试 Oracle 来制造 PASS。

## 验收条件
- 最低质量模式：quick
- 声明清单：{INPUT_REL.as_posix()}/claim.json
- 验收 ID：B39.PLANRUN.RUNTIME.FIELD.ISOLATION

## 基线
baseline = canonical B38 commit {BASE}。回归 Oracle 位于 runner checkout，SHA-256={oracle_sha}；它只作为外部 immutable pytest oracle 执行，不写入 baseline workspace。Claim 通过 gate-log 绑定本次实际执行证据。

## 修复轮次
- 最大轮次：8
- 当前轮次：1
"""
    claim = {
        "schema_version": 1,
        "target_id": CHANGE,
        "claims": [
            {
                "id": "B39.PLANRUN.RUNTIME.FIELD.ISOLATION",
                "statement": "Runtime-only per-goal verification fields cannot change immutable PlanDefinition identity, and a previously SUCCEEDED effect must remain inherited across an equivalent plan revision.",
                "risk": "P1",
                "required_mode": "quick",
                "evidence_kind": "counterexample",
                "required_gates": [
                    "b39-focused-proof",
                    "b39-native-regression",
                    "b39-b38-invariants",
                    "b39-negative-path",
                    "b39-agent-standard",
                ],
                "evidence_refs": [
                    "gate-log:b39-focused-proof",
                    "gate-log:b39-native-regression",
                    "gate-log:b39-b38-invariants",
                    "gate-log:b39-negative-path",
                    "gate-log:b39-agent-standard",
                ],
                "owner": "agent_core.lifecycle.plan_execution",
                "closure_requirement": "regression-transition",
            }
        ],
    }
    runner = (RUNNER / POLICY_RUNNER_REL).resolve()
    def step(step_id: str, arg: str, *, depends: list[str], category: str) -> dict:
        return {
            "id": step_id,
            "name": step_id,
            "modes": ["quick"],
            "kind": "shell",
            "argv": [str(PY.resolve()), "-B", str(runner), arg],
            "owner": "quality-controller",
            "category": category,
            "blocking_level": "required",
            "repair_playbook": "repair PlanRun runtime/definition separation; never weaken the immutable B39 oracle or B38 invariants",
            "rerun_contract": "dependency_closure_then_downstream",
            "depends_on": depends,
            "environment": {
                "APP_PROFILE": "local",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": "services/agent-service/src:services/agent-service",
            },
            "timeout_seconds": 240,
        }
    policy = {
        "version": "stage5g4-final-b39-governed-v1",
        "steps": [
            step("b39-focused-proof", "focused", depends=[], category="unit-contract"),
            step("b39-native-regression", "native", depends=["b39-focused-proof"], category="counterexample-regression"),
            step("b39-b38-invariants", "b38-invariants", depends=["b39-native-regression"], category="counterexample-regression"),
            step("b39-negative-path", "negative", depends=["b39-b38-invariants"], category="counterexample-regression"),
            step("b39-agent-standard", "agent-standard", depends=["b39-negative-path"], category="unit-contract"),
        ],
    }
    (inp / "target.md").write_text(target, encoding="utf-8")
    dump(inp / "claim.json", claim)
    dump(inp / "focused-policy.json", policy)
    dump(inp / "oracle-identity.json", {
        "schema_version": 1,
        "oracle_kind": "external-runner-pytest",
        "runner_head": git_head(RUNNER),
        "path": ORACLE_REL.as_posix(),
        "sha256": oracle_sha,
        "policy_runner_path": POLICY_RUNNER_REL.as_posix(),
        "policy_runner_sha256": policy_runner_sha,
        "baseline_commit": BASE,
        "workspace_mutation": False,
    })
    return {
        "target_sha256": sha(inp / "target.md"),
        "claim_sha256": sha(inp / "claim.json"),
        "policy_sha256": sha(inp / "focused-policy.json"),
        "oracle_sha256": oracle_sha,
        "policy_runner_sha256": policy_runner_sha,
    }


def download_probe() -> dict:
    archive = ROOT / "b39-probe.zip"
    with archive.open("wb") as handle:
        cp = subprocess.run(
            ["gh", "api", f"/repos/{REPO}/actions/artifacts/{PROBE_ARTIFACT_ID}/zip"],
            stdout=handle,
            stderr=subprocess.PIPE,
            env=os.environ.copy(),
        )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.decode("utf-8", "replace"))
    assert sha(archive) == PROBE_ARTIFACT_SHA
    download = ROOT / "b39-probe-download"
    if download.exists():
        shutil.rmtree(download)
    download.mkdir()
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(download)
    report = load(download / "B39_PLANRUN_REPAIR_PROBE_REPORT.json")
    assert report["verdict"] == "B39_PLANRUN_RUNTIME_FIELD_ISOLATION_REPAIR_PROBE_PASS"
    assert report["plan_execution_sha256"] == EXPECTED_PLAN_SHA
    assert report["regression_test_sha256"] == EXPECTED_TEST_SHA
    assert report["focused"] == {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}
    assert report["native_regression"] == {"tests": 43, "failures": 0, "errors": 0, "skipped": 0}
    assert report["agent_standard"] == {"tests": 1542, "failures": 0, "errors": 0, "skipped": 0}
    assert report["b38_invariants"] == {"tests": 2, "failures": 0, "errors": 0, "skipped": 0}
    formal = SOURCE / INPUT_REL / "probe"
    formal.mkdir(parents=True, exist_ok=True)
    shutil.copy2(download / "B39_PLANRUN_REPAIR_PROBE_REPORT.json", formal / "B39_PLANRUN_REPAIR_PROBE_REPORT.json")
    dump(EVIDENCE / "g0/probe-authority.json", {
        "status": "PASS",
        "artifact_id": PROBE_ARTIFACT_ID,
        "artifact_sha256": sha(archive),
        "report": report,
    })
    return report


def init_contract() -> dict:
    assert git_head(SOURCE) == BASE
    assert git_head(BASELINE) == BASE
    assert git_head(CANDIDATE) == BASE
    assert git_head(CONTROL) == CONTROLLER
    assert not (SOURCE / TEST_PATH).exists()
    before_plan_sha = sha(SOURCE / PLAN_PATH)
    cli = str(CLI.resolve())
    cp = run([
        str(PY), "-B", cli, "init",
        "--profile", "product-code",
        "--change-id", CHANGE,
        "--goal", "Isolate PlanRun runtime verification fields from immutable PlanDefinition identity and preserve successful effect inheritance.",
        "--target-kind", "repair",
        "--allow", PLAN_PATH,
        "--allow", TEST_PATH,
        "--affected-module", "agent_core.lifecycle.plan_execution",
        "--minimum-mode", "quick",
        "--quality-target", (INPUT_REL / "target.md").as_posix(),
        "--repair-governance", f"governance/repair-cases/{CHANGE}",
    ], cwd=SOURCE)
    assert cp.returncode == 0
    active = load(SOURCE / "governance/active-change.json")
    assert active["status"] == "draft"
    assert active["result"] == "PENDING"
    assert active["allowed_paths"] == [PLAN_PATH, TEST_PATH]
    assert active["verification"] is None
    dump(EVIDENCE / "g0/contract-draft.json", active)
    return {"before_plan_sha256": before_plan_sha, "initial_source_fingerprint": active["initial_source_fingerprint"]}


def run_baseline() -> dict:
    evidence = BASELINE / f".quality/product-code/{CHANGE}/baseline"
    state = BASELINE / f".quality/product-code/{CHANGE}/state"
    cmd = [
        sys.executable, "-B", str(QUALITY_LOOP.resolve()),
        "--workspace-root", str(BASELINE),
        "--mode", "quick",
        "--target", str(BASELINE / INPUT_REL / "target.md"),
        "--evidence-dir", str(evidence),
        "--state-dir", str(state),
        "--baseline",
        "--policy", str(BASELINE / INPUT_REL / "focused-policy.json"),
    ]
    cp = run(cmd, expect=None)
    summary = load(evidence / "run-summary.json")
    assert cp.returncode != 0
    assert summary["run_kind"] == "baseline"
    assert summary["decision"] == "FAIL"
    assert summary["loop_status"] == "BASELINE_RECORDED"
    assert summary["target_identity"]["id"] == CHANGE
    assert summary["claim_results"][0]["id"] == "B39.PLANRUN.RUNTIME.FIELD.ISOLATION"
    assert summary["claim_results"][0]["status"] == "FAILED"
    focused = (evidence / "steps/b39-focused-proof.stdout.txt").read_text(encoding="utf-8")
    assert "1 failed" in focused
    assert not (BASELINE / TEST_PATH).exists()
    formal = SOURCE / f".quality/product-code/{CHANGE}/baseline"
    formal.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(evidence, formal)
    run([
        str(PY), "-B", str(CLI), "configure",
        "--baseline-evidence", formal.relative_to(SOURCE).as_posix(),
    ], cwd=SOURCE)
    dump(EVIDENCE / "g1/formal-red.json", {
        "status": "PASS",
        "process_exit_code": cp.returncode,
        "summary": summary,
        "focused_stdout_sha256": sha(evidence / "steps/b39-focused-proof.stdout.txt"),
        "baseline_git_head": git_head(BASELINE),
        "baseline_test_path_exists": False,
    })
    return {"evidence": formal, "summary": summary}


def materialize_governance(probe: dict) -> dict:
    case = SOURCE / f"governance/repair-cases/{CHANGE}"
    case.mkdir(parents=True, exist_ok=True)
    baseline_rel = f".quality/product-code/{CHANGE}/baseline"
    failure = {
        "schema_version": 1,
        "record_type": "failure-case",
        "change_id": CHANGE,
        "classification": "implementation-defect",
        "reproduction": {
            "status": "REPRODUCED",
            "expected": "Runtime-only per-goal verification fields do not alter immutable PlanDefinition identity and a previously SUCCEEDED effect remains inherited.",
            "actual": "Canonical B38 fails the immutable B39 regression oracle because runtime verification fields alter the frozen step digest and revision inheritance is lost.",
            "evidence_refs": [
                f"{baseline_rel}/run-summary.json",
                f"{baseline_rel}/steps/b39-focused-proof.stdout.txt",
                f"{INPUT_REL.as_posix()}/probe/B39_PLANRUN_REPAIR_PROBE_REPORT.json",
            ],
        },
        "violated_invariants": [
            "PlanDefinition identity contains only structural planning fields",
            "equivalent plan revision preserves previously SUCCEEDED effect state",
        ],
        "affected_boundaries": [
            "agent_core.lifecycle.plan_execution.freeze_plan_definition",
            "agent_core.lifecycle.plan_execution.revise_plan_run",
        ],
    }
    dump(case / "failure-case.json", failure)
    root_cause = {
        "schema_version": 1,
        "record_type": "root-cause-proof",
        "change_id": CHANGE,
        "failure_case_sha256": sha(case / "failure-case.json"),
        "decision": "PROVEN",
        "root_cause": "B38 strips top-level runtime verification fields but does not strip per-goal runtime cardinality/completion fields or per-goal aggregate maps before freezing the PlanDefinition; the step definition digest therefore changes across an otherwise equivalent revision.",
        "causal_chain": [
            "runtime execution populates verified_result_member_count and per-goal eligibility fields",
            "rebuilt projected plan carries those runtime values into step.verification",
            "freeze_plan_definition retains those runtime-only values",
            "the immutable step definition digest changes",
            "revise_plan_run rejects inheritance for the prior SUCCEEDED effect and recreates it as PLANNED",
        ],
        "evidence_refs": [
            PLAN_PATH,
            f"{baseline_rel}/steps/b39-focused-proof.stdout.txt",
            f"{INPUT_REL.as_posix()}/probe/B39_PLANRUN_REPAIR_PROBE_REPORT.json",
        ],
        "rejected_hypotheses": [
            "The 31 native regressions are unrelated test flakiness: rejected because one two-path patch eliminates all reproduced families with zero skips.",
            "B38 target compatibility semantics are the cause: rejected because the B38 cross-target and same-target invariants remain PASS after the PlanRun-only repair.",
        ],
        "affected_boundaries": [
            "immutable plan-definition projection",
            "PlanRun revision state inheritance",
        ],
    }
    dump(case / "root-cause-proof.json", root_cause)
    required_tests = {
        "focused": ["external immutable B39 PlanRun runtime-field isolation oracle"],
        "counterexamples": ["B38 cross-target rejection and same-target reuse invariants"],
        "regression": ["three native failure-family suites", "full standard Agent suite"],
        "negative_path": ["runtime per-goal values must be absent from frozen verification"],
    }
    plan = {
        "schema_version": 1,
        "record_type": "repair-plan",
        "change_id": CHANGE,
        "root_cause_proof_sha256": sha(case / "root-cause-proof.json"),
        "status": "APPROVED",
        "strategy": "Extend the existing PlanRun runtime-field stripping owner so per-goal runtime verification results and aggregate eligibility maps are removed before PlanDefinition freezing; add one direct regression test. No B38 semantic authority is changed.",
        "changes": [
            {
                "path": PLAN_PATH,
                "responsibility": "single owner for stripping runtime-only step/goal verification state before immutable plan definition hashing",
                "reason": "remove per-goal runtime cardinality/completion fields and aggregate by-goal maps from structural identity",
            },
            {
                "path": TEST_PATH,
                "responsibility": "direct regression proof for digest stability and SUCCEEDED effect inheritance",
                "reason": "prevent runtime verification values from re-entering PlanDefinition identity",
            },
        ],
        "unchanged_boundaries": [
            "B38 reference resolution and target compatibility semantics",
            "CapabilityGate and Pretool execution authority",
            "Business Service and transaction authority",
            "Quality controller and governance schemas",
        ],
        "forbidden_repairs": [
            "do not weaken revise_plan_run identity checks",
            "do not delete or relax B38 cross-target/same-target tests",
            "do not modify quality Target/Claim/Judge to manufacture PASS",
            "do not broaden product scope beyond the two approved paths",
        ],
        "required_invariants": [
            "runtime-only fields never affect immutable PlanDefinition digest",
            "structurally equivalent effect retains SUCCEEDED state across revision",
            "B38 cross-target reuse remains rejected and same-target reuse remains allowed",
        ],
        "risks": [
            "over-stripping a structural verification declaration would make distinct plans appear equal",
            "under-stripping a newly introduced runtime field could reintroduce revision churn",
        ],
        "required_tests": required_tests,
        "rollback_plan": "Revert the two B39 paths together; do not retain the new test without the repair or the repair without the regression proof.",
    }
    dump(case / "repair-plan.json", plan)
    review = {
        "schema_version": 1,
        "record_type": "plan-review",
        "change_id": CHANGE,
        "repair_plan_sha256": sha(case / "repair-plan.json"),
        "reviewer_role": "repair-plan-reviewer",
        "decision": "APPROVED",
        "skill_rule_mappings": [
            "single-owner repair: agent_core.lifecycle.plan_execution remains the only runtime/definition stripping owner",
            "test-first proof: baseline RED is established before product bytes change",
            "minimal scope: one existing runtime owner plus one regression test",
            "fail-closed: PlanRun identity checks are preserved rather than weakened",
        ],
        "approved_paths": [PLAN_PATH, TEST_PATH],
    }
    dump(case / "plan-review.json", review)

    sys.path.insert(0, str((CONTROL / "skill-system/controller").resolve()))
    import repair_governance  # type: ignore

    active = load(SOURCE / "governance/active-change.json")
    permit_path = repair_governance.create_permit(SOURCE, active)
    permit = load(permit_path)
    assert permit["status"] == "ACTIVE"
    assert permit["expires_after"] == "single-verification"
    assert permit["allowed_paths"] == [PLAN_PATH, TEST_PATH]
    run([str(PY), "-B", str(CLI), "approve"], cwd=SOURCE)
    approved = load(SOURCE / "governance/active-change.json")
    assert approved["status"] == "approved"
    dump(EVIDENCE / "g2/governance-ready.json", {
        "status": "PASS",
        "failure_sha256": sha(case / "failure-case.json"),
        "root_cause_sha256": sha(case / "root-cause-proof.json"),
        "repair_plan_sha256": sha(case / "repair-plan.json"),
        "plan_review_sha256": sha(case / "plan-review.json"),
        "baseline_manifest_sha256": sha(case / "baseline-manifest.json"),
        "permit_sha256": sha(case / "change-permit.json"),
        "permit_digest": permit["permit_digest"],
        "probe_plan_sha256": probe["plan_execution_sha256"],
        "probe_test_sha256": probe["regression_test_sha256"],
    })
    return permit


def begin_and_install() -> None:
    before = load(SOURCE / "governance/active-change.json")
    assert before["status"] == "approved"
    run([str(PY), "-B", str(CLI), "begin"], cwd=SOURCE)
    active = load(SOURCE / "governance/active-change.json")
    assert active["status"] == "implementing"
    assert active["repair_governance_permit_digest"]

    patch = RUNNER / PATCH_REL
    oracle = RUNNER / ORACLE_REL
    for workspace in (SOURCE, CANDIDATE):
        run(["git", "-C", str(workspace), "apply", str(patch.resolve())])
        dst = workspace / TEST_PATH
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(oracle, dst)
        assert sha(workspace / PLAN_PATH) == EXPECTED_PLAN_SHA
        assert sha(dst) == EXPECTED_TEST_SHA
        run(["git", "-C", str(workspace), "diff", "--check"])
    dump(EVIDENCE / "g3/candidate-install.json", {
        "status": "PASS",
        "plan_sha256": sha(SOURCE / PLAN_PATH),
        "test_sha256": sha(SOURCE / TEST_PATH),
        "source_git_head": git_head(SOURCE),
        "candidate_git_head": git_head(CANDIDATE),
    })


def junit_summary(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    return {
        key: sum(int(float(suite.attrib.get(key, 0))) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }


def verify_candidate() -> dict:
    baseline = BASELINE / f".quality/product-code/{CHANGE}/baseline"
    candidate_baseline = CANDIDATE / f".quality/product-code/{CHANGE}/baseline"
    candidate_baseline.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(baseline, candidate_baseline)
    evidence = CANDIDATE / f".quality/product-code/{CHANGE}/verification-quick"
    state = CANDIDATE / f".quality/product-code/{CHANGE}/state"
    cmd = [
        sys.executable, "-B", str(QUALITY_LOOP.resolve()),
        "--workspace-root", str(CANDIDATE),
        "--mode", "quick",
        "--target", str(CANDIDATE / INPUT_REL / "target.md"),
        "--evidence-dir", str(evidence),
        "--state-dir", str(state),
        "--baseline-evidence", str(candidate_baseline),
        "--policy", str(CANDIDATE / INPUT_REL / "focused-policy.json"),
    ]
    cp = run(cmd, expect=None)
    summary = load(evidence / "run-summary.json")
    if cp.returncode != 0:
        raise RuntimeError(cp.stdout + "\n" + cp.stderr + "\n" + json.dumps(summary, indent=2))
    assert summary["decision"] == "PASS"
    assert summary["loop_status"] == "CONVERGED"
    assert summary["completion_eligible"] is True
    assert summary["claim_results"][0]["status"] == "VERIFIED"
    focused = junit_summary(evidence / "junit/b39-focused-proof.xml")
    native = junit_summary(evidence / "junit/b39-native-regression.xml")
    invariants = junit_summary(evidence / "junit/b39-b38-invariants.xml")
    negative = junit_summary(evidence / "junit/b39-negative-path.xml")
    agent = junit_summary(evidence / "junit/b39-agent-standard.xml")
    assert focused == {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}
    assert native == {"tests": 43, "failures": 0, "errors": 0, "skipped": 0}
    assert invariants == {"tests": 2, "failures": 0, "errors": 0, "skipped": 0}
    assert negative == {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}
    assert agent["failures"] == agent["errors"] == agent["skipped"] == 0
    assert agent["tests"] >= 1542
    formal = SOURCE / f".quality/product-code/{CHANGE}/verification-quick"
    if formal.exists():
        shutil.rmtree(formal)
    formal.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(evidence, formal)
    record_cp = run([
        str(PY), "-B", str(BRIDGE), "record",
        "--workspace-root", str(SOURCE),
        "--evidence", formal.relative_to(SOURCE).as_posix(),
        "--mode", "quick",
    ])
    record_result = json.loads(record_cp.stdout)
    assert record_result["status"] == "PASS"
    current_cp = run([
        str(PY), "-B", str(BRIDGE), "current",
        "--workspace-root", str(SOURCE),
        "--mode", "quick",
    ])
    current_result = json.loads(current_cp.stdout)
    assert current_result["status"] == "PASS"
    dump(EVIDENCE / "g4/current-quick.json", {
        "status": "PASS",
        "summary": summary,
        "focused": focused,
        "native": native,
        "b38_invariants": invariants,
        "negative": negative,
        "agent_standard": agent,
        "record_result": record_result,
        "current_result": current_result,
        "evidence_attestation_sha256": sha(formal / "evidence-attestation.json"),
    })
    return {"formal": formal, "summary": summary, "current": current_result}


def review_and_close(verification: dict) -> dict:
    sys.path.insert(0, str((CONTROL / "skill-system/controller").resolve()))
    import repair_governance  # type: ignore

    active_path = SOURCE / "governance/active-change.json"
    active = load(active_path)
    diff_path = repair_governance.write_diff_review(SOURCE, active, requested_decision="PASS")
    diff = load(diff_path)
    assert diff["decision"] == "PASS"
    assert diff["changed_paths"] == sorted([PLAN_PATH, TEST_PATH])
    assert diff["added_paths"] == [TEST_PATH]
    assert diff["modified_paths"] == [PLAN_PATH]
    assert diff["deleted_paths"] == []
    assert diff["out_of_scope_paths"] == []
    assert diff["deterministic_findings"] == []

    reviews = SOURCE / f".quality/product-control-plane/{CHANGE}/reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    scope = {
        "schema_version": 1,
        "reviewer_role": "scope-planner",
        "change_id": CHANGE,
        "decision": "PASS",
        "checks": {
            "diff_review_pass": diff["decision"] == "PASS",
            "exact_two_paths": diff["changed_paths"] == sorted([PLAN_PATH, TEST_PATH]),
            "operations_1_add_1_modify": len(diff["added_paths"]) == 1 and len(diff["modified_paths"]) == 1 and not diff["deleted_paths"],
            "no_out_of_scope": not diff["out_of_scope_paths"],
            "no_findings": not diff["deterministic_findings"],
            "candidate_sha_exact": sha(SOURCE / PLAN_PATH) == EXPECTED_PLAN_SHA and sha(SOURCE / TEST_PATH) == EXPECTED_TEST_SHA,
        },
        "findings": [],
    }
    dump(reviews / "scope-planner.json", scope)
    assert all(scope["checks"].values())

    plan_text = (SOURCE / PLAN_PATH).read_text(encoding="utf-8")
    q = load(EVIDENCE / "g4/current-quick.json")
    adversarial = {
        "schema_version": 1,
        "reviewer_role": "adversarial-reviewer",
        "change_id": CHANGE,
        "decision": "PASS",
        "selected_architecture": "existing-planrun-owner-minimal-repair",
        "checks": {
            "single_existing_owner": "_strip_step_runtime" in plan_text,
            "runtime_per_goal_fields_declared": "_RUNTIME_PER_GOAL_VERIFICATION_FIELDS" in plan_text,
            "aggregate_runtime_fields_stripped": "goal_cardinality_eligible_by_goal" in plan_text and "goal_completion_eligible_by_goal" in plan_text,
            "focused_oracle_pass": q["focused"]["tests"] == 1 and q["focused"]["failures"] == 0,
            "native_regressions_pass": q["native"] == {"tests": 43, "failures": 0, "errors": 0, "skipped": 0},
            "b38_invariants_preserved": q["b38_invariants"] == {"tests": 2, "failures": 0, "errors": 0, "skipped": 0},
            "negative_path_pass": q["negative"] == {"tests": 1, "failures": 0, "errors": 0, "skipped": 0},
            "full_agent_clean": q["agent_standard"]["failures"] == 0 and q["agent_standard"]["errors"] == 0 and q["agent_standard"]["skipped"] == 0,
            "no_controller_change": git_head(CONTROL) == CONTROLLER,
            "no_b38_semantic_scope_expansion": diff["changed_paths"] == sorted([PLAN_PATH, TEST_PATH]),
        },
        "findings": [],
    }
    dump(reviews / "adversarial-reviewer.json", adversarial)
    assert all(adversarial["checks"].values())

    run([str(PY), "-B", str(CLI), "attest-review", "--role", "scope-planner", "--decision", "PASS", "--evidence", str((reviews / "scope-planner.json").resolve())], cwd=SOURCE)
    run([str(PY), "-B", str(CLI), "attest-review", "--role", "adversarial-reviewer", "--decision", "PASS", "--evidence", str((reviews / "adversarial-reviewer.json").resolve())], cwd=SOURCE)
    active = load(active_path)
    assert active["status"] == "review"
    roles = {row["role"]: row for row in active["review_attestations"]}
    assert roles["scope-planner"]["decision"] == "PASS"
    assert roles["adversarial-reviewer"]["decision"] == "PASS"
    assert "release-judge" not in roles

    formal = verification["formal"]
    baseline = SOURCE / f".quality/product-code/{CHANGE}/baseline"
    probe = SOURCE / INPUT_REL / "probe/B39_PLANRUN_REPAIR_PROBE_REPORT.json"
    evidence = {
        "original_failure": (baseline / "steps/b39-focused-proof.stdout.txt").relative_to(SOURCE).as_posix(),
        "focused_tests": (formal / "junit/b39-focused-proof.xml").relative_to(SOURCE).as_posix(),
        "counterexamples": (formal / "junit/b39-b38-invariants.xml").relative_to(SOURCE).as_posix(),
        "regression": (formal / "junit/b39-native-regression.xml").relative_to(SOURCE).as_posix(),
        "negative_paths": (formal / "junit/b39-negative-path.xml").relative_to(SOURCE).as_posix(),
        "runtime_trace": probe.relative_to(SOURCE).as_posix(),
        "authority_boundary": (reviews / "adversarial-reviewer.json").relative_to(SOURCE).as_posix(),
        "diff_review": diff_path.relative_to(SOURCE).as_posix(),
        "scope_review": (reviews / "scope-planner.json").relative_to(SOURCE).as_posix(),
        "architecture_review": (reviews / "adversarial-reviewer.json").relative_to(SOURCE).as_posix(),
        "full_agent": (formal / "junit/b39-agent-standard.xml").relative_to(SOURCE).as_posix(),
    }
    active = load(active_path)
    closure_path = repair_governance.write_closure_matrix(
        SOURCE, active, result="CONVERGED", evidence=evidence, loop_outcome="CONVERGED", residual_risks=[]
    )
    closure = load(closure_path)
    assert closure["result"] == "CONVERGED"
    assert closure["final_decision"] == "CLOSED_VERIFIED"
    assert closure["residual_risks"] == []
    ready = repair_governance.validate_verification_ready(SOURCE, active, expected_result="CONVERGED")
    assert ready["status"] == "PASS"
    dump(EVIDENCE / "g5/review-readiness.json", {
        "status": "PASS",
        "diff_review": diff,
        "scope_review": scope,
        "adversarial_review": adversarial,
        "closure": closure,
        "readiness": ready,
    })

    verify_cp = run([str(PY), "-B", str(CLI), "verify", "--result", "CONVERGED"], cwd=SOURCE)
    (EVIDENCE / "g6/contract-verify.stdout.txt").parent.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "g6/contract-verify.stdout.txt").write_text(verify_cp.stdout, encoding="utf-8")
    verified = load(active_path)
    assert verified["status"] == "verified"
    assert verified["result"] == "CONVERGED"
    verified_roles = {row["role"]: row for row in verified["review_attestations"]}
    assert verified_roles["release-judge"]["decision"] == "PASS"
    assert verified["verification"] is not None
    verification_path = SOURCE / verified["verification"]["path"]
    assert sha(verification_path) == verified["verification"]["sha256"]

    close_cp = run([str(PY), "-B", str(CLI), "close", "--result", "CONVERGED"], cwd=SOURCE)
    (EVIDENCE / "g6/contract-close.stdout.txt").write_text(close_cp.stdout, encoding="utf-8")
    closed = load(active_path)
    assert closed["status"] == "closed"
    assert closed["result"] == "CONVERGED"
    assert closed.get("repair_governance_consumed_at")
    assert closed.get("closed_at")
    assert sha(SOURCE / PLAN_PATH) == EXPECTED_PLAN_SHA
    assert sha(SOURCE / TEST_PATH) == EXPECTED_TEST_SHA
    return {
        "closed": closed,
        "verification_path": verification_path,
        "diff": diff,
        "closure": closure,
        "ready": ready,
    }


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    os.environ["QUALITY_EVIDENCE_SIGNING_KEY"] = secrets.token_hex(32)
    identities = {}
    for workspace in (SOURCE, BASELINE, CANDIDATE):
        identities[workspace.name] = write_inputs(workspace)
    assert identities["source"] == identities["baseline-source"] == identities["candidate-source"]
    probe = download_probe()
    initial = init_contract()
    baseline = run_baseline()
    permit = materialize_governance(probe)
    begin_and_install()
    verification = verify_candidate()
    final = review_and_close(verification)
    assert not (SOURCE / ".quality/quality-evidence.key").exists()
    report = {
        "schema_version": 1,
        "verdict": "B39_GOVERNED_REPAIR_CYCLE_CLOSED_CONVERGED",
        "change_id": CHANGE,
        "baseline_head": BASE,
        "controller_head": CONTROLLER,
        "runner_head": git_head(RUNNER),
        "input_identities": identities["source"],
        "initial": initial,
        "formal_red": {
            "decision": baseline["summary"]["decision"],
            "loop_status": baseline["summary"]["loop_status"],
        },
        "permit_digest": permit["permit_digest"],
        "candidate": {
            "paths": [PLAN_PATH, TEST_PATH],
            "plan_sha256": sha(SOURCE / PLAN_PATH),
            "test_sha256": sha(SOURCE / TEST_PATH),
        },
        "verification": load(EVIDENCE / "g4/current-quick.json"),
        "diff_review": {
            "decision": final["diff"]["decision"],
            "changed_paths": final["diff"]["changed_paths"],
            "added_paths": final["diff"]["added_paths"],
            "modified_paths": final["diff"]["modified_paths"],
            "deleted_paths": final["diff"]["deleted_paths"],
        },
        "closure": {
            "result": final["closure"]["result"],
            "final_decision": final["closure"]["final_decision"],
            "evidence_count": len(final["closure"]["evidence"]),
        },
        "contract": {
            "status": final["closed"]["status"],
            "result": final["closed"]["result"],
            "closed_at": final["closed"]["closed_at"],
            "repair_governance_consumed_at": final["closed"]["repair_governance_consumed_at"],
            "verification": final["closed"]["verification"],
            "review_attestations": final["closed"]["review_attestations"],
            "sha256": sha(SOURCE / "governance/active-change.json"),
        },
        "publication_authorized_by_this_run": False,
        "canonical_b38_branch_modified": False,
        "main_modified": False,
        "production_closed": False,
    }
    dump(ROOT / "B39_GOVERNED_REPAIR_REPORT.json", report)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        dump(ROOT / "B39_GOVERNED_REPAIR_FAILURE.json", {
            "status": "FAIL",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        })
        raise
