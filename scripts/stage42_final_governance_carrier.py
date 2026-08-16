from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

CHANGE_ID = "repair-stage4-2-dependency-obligation-evidence-pipeline"
CASE_REL = Path("governance/repair-cases") / CHANGE_ID
QUALITY_REL = CASE_REL / "quality-input"
BASELINE_REL = CASE_REL / "evidence" / "quality-baseline"
REPRO_REL = CASE_REL / "evidence" / "reproduction.json"
BRIDGE = "services/agent-service/src/agent_core/goal_graph/dependency_alignment.py"
PLANNER = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
BRIDGE_TEST = "services/agent-service/tests/runtime/test_dependency_alignment_authority.py"
PIPELINE_TEST = "services/agent-service/tests/runtime/test_dependency_obligation_evidence_pipeline.py"
ARCHITECTURE = "docs/architecture/dependency-proof-authority.md"
ALLOWED = [BRIDGE, PLANNER, BRIDGE_TEST, PIPELINE_TEST]
CLAIM_ID = "STAGE4_2.DEPENDENCY_OBLIGATION_PIPELINE"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(workspace: Path, *argv: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(argv),
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    print("$", *argv)
    if completed.stdout:
        print(completed.stdout)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr)
    if check and completed.returncode:
        raise SystemExit(completed.returncode)
    return completed


def gate_program(*, expect_red: bool) -> str:
    tail = r'''
print(json.dumps(observed, sort_keys=True))
'''
    if expect_red:
        tail += r'''
assert graph["complete"] is True, "Stage 4.2 source no longer reproduces the false-authority baseline"
assert state["maturity"] == proof.AUTHORITATIVE, state
assert state["obligations"]["target_compatibility"] == proof.PASS, state
assert state["obligations"]["counterfactual"] == proof.PASS, state
'''
    else:
        tail += r'''
assert graph["complete"] is False, "pair decision + closure phase minted dependency authority without obligation-specific evidence"
assert state["obligations"]["target_compatibility"] == proof.UNKNOWN_RESULT, "target compatibility was manufactured instead of remaining UNKNOWN"
assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT, "counterfactual was manufactured instead of remaining UNKNOWN"
'''
    return (r'''
import json
import sys
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root / "services/agent-service/src"))
sys.path.insert(0, str(root / "services/agent-service"))
from agent_core.goal_graph import dependency_alignment as bridge
from agent_core.goal_graph import dependency_proof as proof

goals = [
    {
        "goal_id": "g1",
        "evidence_span": "Inspect A",
        "requested_effect": {"domain": "open", "operation": "inspect", "object_type": "record"},
        "depends_on": [],
    },
    {
        "goal_id": "g2",
        "evidence_span": "use that result",
        "requested_effect": {"domain": "open", "operation": "use", "object_type": "record"},
        "depends_on": ["g1"],
    },
]
details = {
    "dependency_authority": "independent_goal_alignment",
    "dependency_proof_complete": True,
    "dependency_graph_match": True,
    "dependency_pair_decisions": [
        {
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "that result",
        }
    ],
}
ledger, graph = bridge.apply_alignment_dependency_proof(
    None,
    user_text="Inspect A, use that result",
    goals=goals,
    details=details,
    phase="candidate_blind_dependency_positive_edge_adjudication",
)
state = ledger["states"]["g1::g2"]
observed = {
    "graph_complete": graph["complete"],
    "edges": graph["edges"],
    "maturity": state["maturity"],
    "target_compatibility": state["obligations"]["target_compatibility"],
    "counterfactual": state["obligations"]["counterfactual"],
    "adversarial_closure": state["obligations"]["adversarial_closure"],
}
''' + tail).strip()


def write_quality_contract(workspace: Path) -> tuple[Path, Path, Path]:
    case = workspace / CASE_REL
    quality = workspace / QUALITY_REL
    quality.mkdir(parents=True, exist_ok=True)
    target = quality / "target.md"
    claim = quality / "claim.json"
    policy = quality / "focused-policy.json"

    target.write_text(
        "# 目标\n\n"
        f"- 目标 ID：{CHANGE_ID}\n"
        f"- 变更标识：portable-{CHANGE_ID}\n"
        "- 执行上下文：local-change\n"
        "- 目标类型：repair\n\n"
        "Close the Stage 4.2 dependency-obligation evidence pipeline: pair relation evidence is observation only, target compatibility and current-turn result-removal counterfactual require separately validated premise-bound evidence, and the existing deterministic dependency reducer remains the sole authority seal.\n\n"
        "## 允许范围\n\n"
        "- 允许变更路径：" + ", ".join(f"`{value}`" for value in ALLOWED) + "\n"
        "- 新增抽象记录：无；复用现有 GoalAlignment semantic verifier boundary、ProofObservation 与 deterministic dependency reducer\n\n"
        "## 禁止范围\n\n"
        "Do not modify dependency_proof.py, CapabilityGate, GoalOutputRef, transaction Draft/Grant/Attempt/Receipt authority, business tools/services, production dependency-authority activation/defaults, Skill/Judge/Quality policy, or create a peer dependency authority owner. Do not change this Target, Claim, focused acceptance policy, baseline, Judge, or evidence to make a candidate pass.\n\n"
        "## 验收条件\n\n"
        "- 最低质量模式：quick\n"
        f"- 声明清单：`{(QUALITY_REL / 'claim.json').as_posix()}`\n"
        f"- 验收 ID：`{CLAIM_ID}`\n\n"
        "A structurally valid pair decision, complete/matching diagnostic, adversarial phase name, or call count alone must leave target_compatibility and counterfactual unresolved. A separate semantic-verifier-produced, frozen-premise-bound obligation evidence envelope may satisfy those obligations; malformed, missing, spoofed, relation-mismatched or premise-mismatched evidence fails closed. dependency_proof.py remains the only maturity/authority reducer.\n\n"
        "## 基线\n\n"
        "The exact pre-repair feature source reproduces false authority: dependency_alignment.py hard-codes target_compatibility=PASS and counterfactual=PASS from a normalized pair decision, while goal_planning.py emits no distinct obligation evidence contract. The pre-change Quality Loop baseline is recorded after the repair governance inputs and ChangePermit are frozen and before any allowed product source is changed; the focused counterexample must be RED.\n\n"
        "## 修复轮次\n\n"
        "- 最大轮次：8\n"
        "- 当前轮次：1\n"
        "- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。\n",
        encoding="utf-8",
    )
    write_json(
        claim,
        {
            "schema_version": 1,
            "target_id": CHANGE_ID,
            "claims": [
                {
                    "id": CLAIM_ID,
                    "statement": "Pair decisions are observations only; target compatibility and current-turn result-removal counterfactual require separately validated frozen-premise-bound evidence before deterministic dependency authority can seal.",
                    "risk": "P1",
                    "required_mode": "quick",
                    "evidence_kind": "counterexample",
                    "required_gates": ["stage42-red-proof"],
                    "evidence_refs": ["gate-log:stage42-red-proof"],
                    "owner": "dependency-proof-authority",
                    "closure_requirement": "regression-transition",
                }
            ],
        },
    )
    encoded = base64.b64encode(gate_program(expect_red=False).encode("utf-8")).decode("ascii")
    command = "import base64;exec(base64.b64decode('" + encoded + "'))"
    write_json(
        policy,
        {
            "version": "stage42-dependency-obligation-evidence-v1",
            "steps": [
                {
                    "id": "stage42-red-proof",
                    "name": "Stage 4.2 pair-only authority counterexample",
                    "modes": ["quick"],
                    "kind": "shell",
                    "argv": [sys.executable, "-B", "-c", command],
                    "owner": "quality-controller",
                    "category": "counterexample-regression",
                    "blocking_level": "required",
                    "repair_playbook": "repair the product evidence boundary; never weaken the focused counterexample",
                    "rerun_contract": "dependency_closure_then_downstream",
                    "depends_on": [],
                    "environment": {
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONPATH": "services/agent-service/src:services/agent-service",
                    },
                    "timeout_seconds": 120,
                }
            ],
        },
    )
    return target, claim, policy


def reproduce(workspace: Path) -> Path:
    completed = run(workspace, sys.executable, "-B", "-c", gate_program(expect_red=True), check=False)
    if completed.returncode != 0:
        raise SystemExit("pre-permit Stage 4.2 source did not reproduce the expected false-authority state")
    path = workspace / REPRO_REL
    write_json(
        path,
        {
            "schema_version": 1,
            "record_type": "stage4-2-red-reproduction",
            "change_id": CHANGE_ID,
            "source_head": run(workspace, "git", "rev-parse", "HEAD").stdout.strip(),
            "process_exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "expected_defect": {
                "graph_complete": True,
                "maturity": "AUTHORITATIVE",
                "target_compatibility": "PASS",
                "counterfactual": "PASS",
            },
        },
    )
    return path


def write_repair_chain(workspace: Path) -> None:
    case = workspace / CASE_REL
    case.mkdir(parents=True, exist_ok=True)
    failure_path = case / "failure-case.json"
    write_json(
        failure_path,
        {
            "schema_version": 1,
            "record_type": "failure-case",
            "change_id": CHANGE_ID,
            "classification": "architecture-defect",
            "reproduction": {
                "status": "REPRODUCED",
                "expected": "Pair relation evidence alone leaves target compatibility and result-removal counterfactual UNKNOWN, so the pair cannot become authoritative.",
                "actual": "The dependency alignment bridge hard-codes target_compatibility=PASS and counterfactual=PASS and derives a counterfactual digest from the same pair decision/phase, allowing an adversarial phase to seal false authority.",
                "evidence_refs": [REPRO_REL.as_posix(), BRIDGE, PLANNER, ARCHITECTURE],
            },
            "violated_invariants": [
                "Verifier pair output is observation, not proof authority for every obligation.",
                "Target compatibility and result-removal counterfactual require obligation-specific evidence bound to the frozen semantic premise.",
                "Missing proof obligations must remain unresolved rather than being promoted to PASS.",
            ],
            "affected_boundaries": [
                "candidate-blind GoalAlignment semantic verifier evidence producer",
                "pair-decision to ProofObservation bridge",
                "target-compatibility proof obligation",
                "current-turn result-removal counterfactual proof obligation",
            ],
        },
    )
    root_path = case / "root-cause-proof.json"
    write_json(
        root_path,
        {
            "schema_version": 1,
            "record_type": "root-cause-proof",
            "change_id": CHANGE_ID,
            "failure_case_sha256": sha(failure_path),
            "decision": "PROVEN",
            "root_cause": "goal_planning structurally validates a candidate-blind complete pair table but does not materialize distinct target-compatibility/counterfactual proof records; dependency_alignment compensates by hard-coding those required obligations to PASS. The deterministic reducer is correctly fail-closed and merely seals the fabricated upstream obligations.",
            "causal_chain": [
                "The candidate-blind semantic verifier is instructed to audit requested-effect/target-scope fidelity and to apply a result-removal counterfactual for every pair.",
                "_model_alignment_pairwise_dependency_proof validates pair coverage, relation, positive basis kind/span and candidate graph comparison, but normalized dependency_pair_decisions contain only relation/basis data.",
                "dependency_alignment receives those pair rows without an obligation-specific evidence contract and unconditionally writes target_compatibility=PASS and counterfactual=PASS.",
                "An adversarial closure phase supplies the remaining closure PASS; dependency_proof.py correctly sees all required obligations as PASS and seals authority from evidence that never separately represented two obligations.",
            ],
            "evidence_refs": [REPRO_REL.as_posix(), BRIDGE, PLANNER, ARCHITECTURE],
            "rejected_hypotheses": [
                "The deterministic reducer is the authority leak; it is not, because UNKNOWN required obligations remain non-authoritative and FAIL rejects.",
                "complete/matching diagnostics alone are granting authority; they are diagnostic fields and the leak occurs because the bridge maps missing obligations to PASS.",
                "Call count is the owner; phase/call count only exposes the bridge fabrication and must not become semantic proof.",
                "A new model field or second judge is required; the existing candidate-blind semantic verifier contract already audits the relevant semantics and can materialize a machine-bound evidence envelope after structural validation.",
            ],
            "affected_boundaries": [
                "GoalAlignment semantic evidence materialization",
                "dependency observation admissibility",
                "obligation-specific evidence binding",
                "dependency proof maturity",
            ],
        },
    )
    plan_path = case / "repair-plan.json"
    required_tests = {
        "focused": [
            "pair decision plus adversarial phase without obligation envelope remains non-authoritative",
            "validated semantic producer envelope plus adversarial closure can reach deterministic authority",
        ],
        "counterexamples": [
            "raw spoof fields inside dependency_pair_decisions cannot self-elevate target/counterfactual obligations",
            "wrong premise, wrong relation, duplicate/malformed/missing obligation evidence fails closed",
        ],
        "regression": [
            "existing dependency proof reducer authority tests remain green",
            "semantic dependency repair feedback regressions remain green without changing the model JSON response schema",
            "product quick profile remains current and green",
        ],
        "negative_path": [
            "semantic target mismatch leaves target compatibility unresolved and cannot be rescued by phase metadata",
            "missing counterfactual evidence maps to UNKNOWN rather than PASS",
        ],
    }
    write_json(
        plan_path,
        {
            "schema_version": 1,
            "record_type": "repair-plan",
            "change_id": CHANGE_ID,
            "root_cause_proof_sha256": sha(root_path),
            "status": "APPROVED",
            "strategy": "Preserve the existing candidate-blind model response schema and deterministic reducer. At the already-trusted goal_planning semantic validation boundary, materialize a narrow producer-tagged, frozen-premise-bound obligation evidence envelope from the normalized candidate-blind pair proof and semantic verdict. Make dependency_alignment consume only that envelope for target_compatibility/counterfactual; missing or malformed evidence stays UNKNOWN. Keep adversarial_closure as its separate phase obligation and add focused producer/bridge counterexamples.",
            "changes": [
                {"path": BRIDGE, "responsibility": "fail-closed obligation evidence adapter", "reason": "remove the proven pair-decision-to-obligation authority fabrication"},
                {"path": PLANNER, "responsibility": "materialize validated obligation-specific evidence after existing candidate-blind semantic/pair validation", "reason": "this is the existing semantic verifier boundary that actually observes target/effect fidelity and the mandated result counterfactual"},
                {"path": BRIDGE_TEST, "responsibility": "prove pair-only/spoofed/malformed evidence cannot become authority", "reason": "existing test currently encodes phase-only false authority"},
                {"path": PIPELINE_TEST, "responsibility": "prove semantic producer evidence can legitimately reach the unchanged reducer", "reason": "prevents a fail-closed repair from permanently removing the positive authority path"},
            ],
            "unchanged_boundaries": [
                "dependency_proof.py remains the sole deterministic maturity/authority owner",
                "the model JSON response schema remains unchanged",
                "CapabilityGate and execution permits are unchanged",
                "Draft/Grant/Attempt/Receipt transaction authority is unchanged",
                "business tools/services are unchanged",
                "production dependency-authority activation/default remains unchanged",
            ],
            "forbidden_repairs": [
                "Do not derive target/counterfactual PASS from pair relation, phase, call count, complete/matching or Planner depends_on.",
                "Do not trust arbitrary new raw model fields as obligation authority.",
                "Do not add a second reducer, fallback judge or parallel dependency authority path.",
                "Do not weaken required obligations or edit dependency_proof.py.",
                "Do not change CapabilityGate, transaction, business tool/service or production activation paths.",
                "Do not weaken Target/Claim/baseline/focused policy/tests to obtain PASS.",
            ],
            "required_invariants": [
                "pair decision remains observation only",
                "obligation evidence is producer-tagged and bound to the exact frozen premise and pair relation",
                "missing/malformed/spoofed obligation evidence fails closed as UNKNOWN",
                "the deterministic reducer alone seals authority after every required obligation passes",
                "candidate-blind verifier/model response schema remains backward-compatible",
            ],
            "required_tests": required_tests,
            "risks": [
                "Fail-closing the bridge can expose unresolved pairs if the producer is not wired into every pairwise closing phase; the final observation must carry its own required PASS obligations because provisional reducer states do not merge obligations.",
                "Treating a semantic mismatch as target PASS would recreate the leak; only exact or dependency-only-mismatch semantic audits may attest target compatibility.",
                "Treating arbitrary raw pair fields as producer evidence would recreate a peer authority path; the bridge must ignore them.",
            ],
            "rollback_plan": "Revert the four permitted Stage 4.2 product/test paths together. Never restore phase-only fabricated PASS as compatibility fallback; if the producer cannot establish evidence, leave the pair unresolved and replan.",
        },
    )
    review_path = case / "plan-review.json"
    write_json(
        review_path,
        {
            "schema_version": 1,
            "record_type": "plan-review",
            "change_id": CHANGE_ID,
            "repair_plan_sha256": sha(plan_path),
            "reviewer_role": "repair-plan-reviewer",
            "decision": "APPROVED",
            "approved_paths": ALLOWED,
            "review_findings": [
                "The prior two-file scope was insufficient because a fail-closed bridge requires the existing semantic verifier boundary to materialize the obligation evidence it already audits.",
                "The expanded four-file scope is still bounded: two product owners plus focused tests; dependency_proof.py and all execution/business authority remain untouched.",
                "No model response schema change is needed, avoiding historical fixture/protocol churn.",
                "The positive path is explicitly required so UNKNOWN fail-closed behavior cannot become a silent permanent disablement.",
            ],
            "skill_rule_mappings": [
                {"rule": "Observation is not authority", "assessment": "goal_planning may materialize validated evidence; only dependency_proof.py seals maturity/authority", "evidence": ARCHITECTURE},
                {"rule": "Mandatory proof obligations fail closed", "assessment": "bridge maps absent/invalid target/counterfactual evidence to UNKNOWN", "evidence": BRIDGE},
                {"rule": "Minimal governed scope", "assessment": "four explicit paths cover producer, adapter and focused regressions without touching execution authority", "evidence": f"{CASE_REL.as_posix()}/root-cause-proof.json"},
            ],
        },
    )


def make_contract_and_permit(workspace: Path, target: Path) -> None:
    args = [
        sys.executable,
        "-B",
        "skillctl.py",
        "product-init",
        "--change-id",
        CHANGE_ID,
        "--goal",
        "Close the Stage 4.2 dependency obligation evidence pipeline without manufacturing proof obligations.",
        "--target-kind",
        "repair",
    ]
    for path in ALLOWED:
        args += ["--allow", path]
    args += [
        "--affected-module",
        "agent_core.goal_graph.dependency_alignment",
        "--affected-module",
        "agent_core.lifecycle.goal_planning",
        "--invariant",
        "pair decision is observation, not target/counterfactual authority",
        "--invariant",
        "obligation evidence is produced only after existing candidate-blind semantic validation and bound to the frozen premise",
        "--invariant",
        "dependency_proof.py remains the sole deterministic maturity authority",
        "--invariant",
        "CapabilityGate, transaction, business tools and production activation are unchanged",
        "--minimum-mode",
        "quick",
        "--quality-target",
        target.relative_to(workspace).as_posix(),
        "--repair-governance",
        CASE_REL.as_posix(),
        "--approve",
        "--force",
    ]
    run(workspace, *args)
    run(workspace, sys.executable, "-B", "skillctl.py", "repair-permit")
    run(workspace, sys.executable, "-B", "skillctl.py", "repair-governance-validate", "--stage", "permit")


def record_formal_baseline(workspace: Path, target: Path, policy: Path) -> Path:
    evidence = workspace / BASELINE_REL
    state = workspace / ".quality" / "product-code" / CHANGE_ID / "state"
    if evidence.exists():
        raise SystemExit(f"refusing to overwrite baseline evidence: {evidence}")
    completed = run(
        workspace,
        sys.executable,
        "-B",
        "scripts/quality_loop.py",
        "--workspace-root",
        str(workspace),
        "--mode",
        "quick",
        "--target",
        str(target),
        "--policy",
        str(policy),
        "--evidence-dir",
        str(evidence),
        "--state-dir",
        str(state),
        "--baseline",
        check=False,
    )
    summary_path = evidence / "run-summary.json"
    record_path = evidence / "baseline-record.json"
    if not summary_path.is_file() or not record_path.is_file():
        raise SystemExit("formal Stage 4.2 baseline evidence was not recorded")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if completed.returncode == 0:
        raise SystemExit("Stage 4.2 transition baseline unexpectedly passed")
    if summary.get("run_kind") != "baseline" or summary.get("decision") != "FAIL" or summary.get("loop_status") != "BASELINE_RECORDED":
        raise SystemExit("Stage 4.2 formal baseline is not a canonical RED baseline")
    results = summary.get("results") or []
    if len(results) != 1 or results[0].get("id") != "stage42-red-proof" or results[0].get("status") != "FAIL":
        raise SystemExit("Stage 4.2 RED baseline did not fail the intended counterexample gate")
    if '"graph_complete": true' not in str(results[0].get("stdout") or ""):
        raise SystemExit("Stage 4.2 baseline did not capture the false-authority graph")
    return evidence


def begin_contract(workspace: Path, baseline: Path) -> None:
    run(
        workspace,
        sys.executable,
        "-B",
        "skillctl.py",
        "contract-configure",
        "--baseline-evidence",
        baseline.relative_to(workspace).as_posix(),
    )
    run(workspace, sys.executable, "-B", "skillctl.py", "contract-begin")
    run(workspace, sys.executable, "-B", "skillctl.py", "contract-validate")
    active = json.loads((workspace / "governance/active-change.json").read_text(encoding="utf-8"))
    permit = json.loads((workspace / CASE_REL / "change-permit.json").read_text(encoding="utf-8"))
    if active.get("change_id") != CHANGE_ID or active.get("status") != "implementing":
        raise SystemExit("Stage 4.2 contract did not enter implementing state")
    if active.get("writer_role") != "product-implementer":
        raise SystemExit("Stage 4.2 contract writer role is not product-implementer")
    if active.get("repair_governance_permit_digest") != permit.get("permit_digest"):
        raise SystemExit("Stage 4.2 active contract is not bound to the ChangePermit")
    if permit.get("allowed_paths") != ALLOWED or permit.get("status") != "ACTIVE":
        raise SystemExit("Stage 4.2 ChangePermit scope/status mismatch")
    baseline_summary = json.loads((baseline / "run-summary.json").read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "status": "PASS",
                "change_id": CHANGE_ID,
                "permit_digest": permit.get("permit_digest"),
                "baseline_source_fingerprint": permit.get("baseline_source_fingerprint"),
                "quality_baseline": baseline.relative_to(workspace).as_posix(),
                "quality_baseline_decision": baseline_summary.get("decision"),
                "quality_baseline_loop_status": baseline_summary.get("loop_status"),
                "allowed_paths": ALLOWED,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--expected-base", required=True)
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    actual = run(workspace, "git", "rev-parse", "HEAD").stdout.strip()
    if actual != args.expected_base:
        raise SystemExit(f"workspace base mismatch: expected={args.expected_base} actual={actual}")
    target, _claim, policy = write_quality_contract(workspace)
    reproduce(workspace)
    write_repair_chain(workspace)
    make_contract_and_permit(workspace, target)
    baseline = record_formal_baseline(workspace, target, policy)
    begin_contract(workspace, baseline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
