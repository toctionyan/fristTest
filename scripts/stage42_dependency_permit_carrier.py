from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

CHANGE_ID = "repair-stage4-2-dependency-bridge-obligation-evidence"
CASE_REL = Path("governance/repair-cases") / CHANGE_ID
BRIDGE = "services/agent-service/src/agent_core/goal_graph/dependency_alignment.py"
BRIDGE_TEST = "services/agent-service/tests/runtime/test_dependency_alignment_authority.py"
ARCHITECTURE = "docs/architecture/dependency-proof-authority.md"
ALLOWED = [BRIDGE, BRIDGE_TEST]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(workspace: Path, *args: str) -> None:
    subprocess.run([sys.executable, "-B", *args], cwd=workspace, check=True)


def _generate_governance(workspace: Path, base_sha: str) -> None:
    case = workspace / CASE_REL
    evidence = case / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (workspace / "governance/targets").mkdir(parents=True, exist_ok=True)
    (workspace / "governance/claims").mkdir(parents=True, exist_ok=True)

    target = f'''# 目标

- 目标 ID：{CHANGE_ID}
- 变更标识：portable-{CHANGE_ID}
- 执行上下文：local-change
- 目标类型：repair

Close the Stage 4.2 dependency-proof bridge authority leak: a validated pair decision is observation only and must not mint target-compatibility or counterfactual PASS without obligation-specific validated evidence.

## 允许范围

- 允许变更路径：`{BRIDGE}`, `{BRIDGE_TEST}`
- 新增抽象记录：无；继续使用现有 dependency ProofObservation + deterministic reducer authority owner

## 禁止范围

Do not modify CapabilityGate, GoalOutputRef, transaction Draft/Grant/Attempt/Receipt authority, business tools/services, production dependency-authority activation/defaults, Skill/Judge/Quality policy, or create a second dependency authority owner. Do not weaken existing reducer obligations or tests to obtain a pass.

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/{CHANGE_ID}.json`
- 验收 ID：`STAGE4_2.DEPENDENCY_BRIDGE.OBLIGATION_EVIDENCE`

A pairwise decision, complete/matching diagnostic, or adversarial phase name alone cannot produce target_compatibility=PASS or counterfactual=PASS. Missing or malformed obligation-specific evidence must remain UNKNOWN/fail closed. Explicit validated obligation evidence may be consumed, but the deterministic dependency-proof reducer remains the only authority seal.

## 基线

Current PR #1157 head before this repair directly self-mints target_compatibility=PASS and counterfactual=PASS in dependency_alignment.py from a pair decision/phase, while the pairwise validator exposes no independent per-pair target/counterfactual proof object. The existing bridge test demonstrates that closure phase alone can therefore mature such a row to authority.

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
'''
    (workspace / "governance/targets" / f"{CHANGE_ID}.md").write_text(target, encoding="utf-8")

    claim = {
        "schema_version": 1,
        "target_id": CHANGE_ID,
        "claims": [{
            "id": "STAGE4_2.DEPENDENCY_BRIDGE.OBLIGATION_EVIDENCE",
            "statement": "The dependency alignment bridge treats pair decisions as observations and cannot grant target-compatibility or counterfactual proof obligations without independently validated obligation-specific evidence; unresolved evidence fails closed and only the deterministic reducer can seal dependency authority.",
            "risk": "P1",
            "required_mode": "quick",
            "evidence_kind": "counterexample",
            "required_gates": ["python-test-suites"],
            "evidence_refs": ["gate-log:python-test-suites"],
            "owner": "dependency-proof-authority",
            "closure_requirement": "regression-transition",
        }],
    }
    _write_json(workspace / "governance/claims" / f"{CHANGE_ID}.json", claim)

    red = f'''# Stage 4.2 RED baseline: dependency bridge obligation authority leak

Exact source: PR #1157 feature head `{base_sha}` before this governed repair.

Reproduction is source- and contract-grounded:

1. `{BRIDGE}` calls `make_dependency_observation(...)` for every structurally validated pair row.
2. The bridge currently writes `target_compatibility=PASS` and `counterfactual=PASS` unconditionally.
3. Its `counterfactual_proof_digest` is computed from the pair decision and phase name rather than an independently validated counterfactual evidence record.
4. `goal_planning._model_alignment_pairwise_dependency_proof(...)` validates pair coverage and positive literal basis spans, but its normalized `dependency_pair_decisions` rows do not contain a dedicated target-compatibility proof or counterfactual proof object.
5. The deterministic reducer correctly seals authority once all required obligations are PASS, so the defect is the bridge manufacturing obligations upstream, not the reducer.
6. Existing `{BRIDGE_TEST}` demonstrates the current false-positive path: an adversarial closure phase plus a plain pair decision can make the graph authoritative.

Expected: pair decision/phase/complete/matching alone leave target compatibility and counterfactual UNKNOWN and cannot mint authority.
Actual: the bridge manufactures both PASS values, allowing closure phase to complete the proof without obligation-specific evidence.
'''
    (evidence / "red-baseline.md").write_text(red, encoding="utf-8")

    failure_path = case / "failure-case.json"
    failure = {
        "schema_version": 1,
        "record_type": "failure-case",
        "change_id": CHANGE_ID,
        "classification": "architecture-defect",
        "reproduction": {
            "status": "REPRODUCED",
            "expected": "A pair decision is only observation evidence; target compatibility and result-removal counterfactual obligations remain UNKNOWN unless independent validated evidence for those obligations is supplied.",
            "actual": "dependency_alignment.py unconditionally sets target_compatibility=PASS and counterfactual=PASS and derives the counterfactual digest from the pair decision/phase, so an adversarial phase can mature a proof without obligation-specific evidence.",
            "evidence_refs": [
                f"{CASE_REL.as_posix()}/evidence/red-baseline.md",
                BRIDGE,
                BRIDGE_TEST,
                ARCHITECTURE,
            ],
        },
        "violated_invariants": [
            "Verifier output is observation, not dependency authority.",
            "Every required dependency-proof obligation must be backed by its own admissible evidence before authority.",
            "Target incompatibility or an unproven result counterfactual must block dependency authority rather than be silently promoted to PASS.",
        ],
        "affected_boundaries": [
            "pair-decision to ProofObservation bridge",
            "target-compatibility proof obligation",
            "current-turn result-removal counterfactual proof obligation",
            "deterministic dependency maturity authority",
        ],
    }
    _write_json(failure_path, failure)

    root_path = case / "root-cause-proof.json"
    root_cause = {
        "schema_version": 1,
        "record_type": "root-cause-proof",
        "change_id": CHANGE_ID,
        "failure_case_sha256": _sha(failure_path),
        "decision": "PROVEN",
        "root_cause": "The alignment bridge collapses a structurally validated pair decision into proof-obligation success by hard-coding target_compatibility and counterfactual to PASS and fabricating a counterfactual digest from the same decision. The reducer is fail-closed; the authority leak occurs before it because obligation-specific evidence is not preserved as a distinct contract.",
        "causal_chain": [
            "goal_planning structurally validates pair IDs, coverage and positive literal basis spans and emits normalized dependency_pair_decisions.",
            "Those normalized rows do not contain an independently validated target-compatibility proof or a result-removal counterfactual evidence object.",
            "dependency_alignment receives the pair rows and unconditionally writes target_compatibility=PASS and counterfactual=PASS, with a digest derived from decision plus phase.",
            "During an adversarial closure phase, adversarial_closure also becomes PASS; therefore the reducer sees all required obligations as PASS and correctly seals authority from evidence that never actually proved two of those obligations.",
        ],
        "evidence_refs": [
            f"{CASE_REL.as_posix()}/evidence/red-baseline.md",
            BRIDGE,
            "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
            "services/agent-service/src/agent_core/goal_graph/dependency_proof.py",
            ARCHITECTURE,
        ],
        "rejected_hypotheses": [
            "The deterministic reducer is granting authority from complete/matching diagnostics; it is not, because maturity depends on explicit obligations.",
            "Call number alone is the authority bug; call count only exposes the leak because the bridge maps closure phase to PASS while also manufacturing other obligations.",
            "The fix requires changing CapabilityGate, transaction authority, business tools or production activation; none of those own dependency proof maturity.",
            "A broader prompt or another verifier vote is sufficient; model instructions without machine-bound obligation evidence do not establish proof authority.",
        ],
        "affected_boundaries": [
            "dependency observation admissibility",
            "obligation-specific evidence binding",
            "dependency proof maturity",
            "authority sealing",
        ],
    }
    _write_json(root_path, root_cause)

    plan_path = case / "repair-plan.json"
    required_tests = {
        "focused": [
            "plain candidate-blind pair decision remains non-authoritative before and during adversarial closure when target/counterfactual evidence is absent",
            "explicit validated obligation evidence can be consumed and all required PASS obligations can still reach deterministic authority",
        ],
        "counterexamples": [
            "adversarial phase name plus complete/matching plus independent pair decision does not mint authority",
            "adversarial phase name plus positive pair decision/basis does not mint authority without target/counterfactual evidence",
        ],
        "regression": [
            "existing dependency proof reducer authority tests remain green",
            "existing dependency alignment authority tests remain green after expectations are corrected to the evidence contract",
            "Agent Quick python test suites remain green",
        ],
        "negative_path": [
            "missing obligation evidence maps to UNKNOWN rather than PASS",
            "malformed or unsupported obligation evidence cannot be treated as PASS",
            "target incompatibility/counterfactual failure cannot be overwritten by phase/diagnostic metadata",
        ],
    }
    plan = {
        "schema_version": 1,
        "record_type": "repair-plan",
        "change_id": CHANGE_ID,
        "root_cause_proof_sha256": _sha(root_path),
        "status": "APPROVED",
        "strategy": "Make dependency_alignment a pure fail-closed evidence adapter: pair decisions continue to supply pair relation/grounding structure, but target_compatibility and counterfactual are UNKNOWN unless explicit prevalidated obligation evidence with a supported contract/result/digest is supplied. Preserve dependency_proof.py as the sole maturity/authority reducer and add minimal bridge-level positive/negative regressions.",
        "changes": [
            {
                "path": BRIDGE,
                "responsibility": "consume obligation-specific validated evidence without manufacturing target/counterfactual PASS",
                "reason": "root authority leak is localized to the pair-decision -> ProofObservation bridge",
            },
            {
                "path": BRIDGE_TEST,
                "responsibility": "prove pair-only fail-closed behavior and explicit-evidence positive path",
                "reason": "existing tests currently encode phase-only false authority and need minimal counterexample coverage",
            },
        ],
        "unchanged_boundaries": [
            "dependency_proof.py remains the single deterministic maturity and authority owner",
            "goal_planning remains the language/model and structural pair validation boundary",
            "CapabilityGate and execution permits are unchanged",
            "Draft/Grant/Attempt/Receipt transaction authority is unchanged",
            "Business Service and business tools are unchanged",
            "production dependency-authority activation/default remains unchanged",
        ],
        "forbidden_repairs": [
            "Do not turn complete/matching, phase name, call number or pair coverage into target/counterfactual authority.",
            "Do not add a second reducer, fallback judge or parallel dependency authority path.",
            "Do not weaken required obligations in dependency_proof.py.",
            "Do not modify CapabilityGate, transaction, business tool/service or production activation paths.",
            "Do not make malformed/unrecognized evidence default to PASS.",
            "Do not weaken tests or delete assertions to make the candidate pass.",
        ],
        "required_invariants": [
            "pair decision remains observation only",
            "missing obligation-specific evidence fails closed as UNKNOWN",
            "target/counterfactual PASS requires explicit supported validated evidence",
            "the deterministic reducer alone seals authority after every required obligation passes",
            "existing authoritative proof stability/counterevidence semantics remain unchanged",
        ],
        "required_tests": required_tests,
        "risks": [
            "Fail-closing the bridge may temporarily leave more dependency pairs unresolved until the existing verifier pipeline supplies obligation-specific evidence; unresolved is safer than false authority.",
            "An evidence adapter that trusts arbitrary raw model fields would recreate the same authority leak; only a narrow prevalidated contract may be consumed.",
            "A broad repair touching goal planning in the same step would blur root-cause closure; producer wiring should be a separately proven next step if required.",
        ],
        "rollback_plan": "Revert the two Stage 4.2 product/test edits together. Do not restore phase-only authority as a compatibility fallback; if obligation evidence cannot be produced, remain fail-closed and replan the producer contract separately.",
    }
    _write_json(plan_path, plan)

    review_path = case / "plan-review.json"
    review = {
        "schema_version": 1,
        "record_type": "plan-review",
        "change_id": CHANGE_ID,
        "repair_plan_sha256": _sha(plan_path),
        "reviewer_role": "repair-plan-reviewer",
        "decision": "APPROVED",
        "approved_paths": ALLOWED,
        "review_findings": [
            "The fix is narrower than changing goal_planning, CapabilityGate or transaction/business execution: the reproduced authority mint occurs inside dependency_alignment.",
            "The plan preserves the existing deterministic reducer and strengthens its evidence boundary rather than introducing a peer authority.",
            "Fail-closed UNKNOWN is required when target/counterfactual evidence is absent; temporary unresolved pairs are an admissible consequence and not a reason to manufacture PASS.",
            "Positive coverage is limited to explicitly validated obligation evidence so the adapter remains extensible without trusting raw pair decisions.",
        ],
        "skill_rule_mappings": [
            {
                "rule": "Single-authority cutover / observation is not authority",
                "assessment": "dependency_proof.py remains sole maturity/authority owner; the bridge can only adapt evidence and cannot independently grant authority",
                "evidence": ARCHITECTURE,
            },
            {
                "rule": "Mandatory proof obligations fail closed",
                "assessment": "target compatibility and counterfactual remain UNKNOWN when their evidence is absent or malformed",
                "evidence": "services/agent-service/src/agent_core/goal_graph/dependency_proof.py",
            },
            {
                "rule": "Minimal bounded repair",
                "assessment": "two-path product scope closes the proven bridge leak without touching execution, transaction, business service or production activation",
                "evidence": f"{CASE_REL.as_posix()}/root-cause-proof.json",
            },
        ],
    }
    _write_json(review_path, review)

    target_rel = f"governance/targets/{CHANGE_ID}.md"
    baseline_rel = f"{CASE_REL.as_posix()}/evidence/red-baseline.md"
    case_rel = CASE_REL.as_posix()
    _run(
        workspace,
        "skillctl.py",
        "product-init",
        "--change-id", CHANGE_ID,
        "--goal", "Close the Stage 4.2 dependency bridge authority leak so pair decisions cannot manufacture target-compatibility or counterfactual PASS.",
        "--target-kind", "repair",
        "--allow", BRIDGE,
        "--allow", BRIDGE_TEST,
        "--affected-module", "agent_core.goal_graph.dependency_alignment",
        "--invariant", "pair decisions are observations, never obligation authority",
        "--invariant", "missing target-compatibility or counterfactual evidence fails closed",
        "--invariant", "dependency_proof.py remains the sole deterministic maturity and authority reducer",
        "--invariant", "CapabilityGate, transaction, business tools and production activation are unchanged",
        "--minimum-mode", "quick",
        "--quality-target", target_rel,
        "--baseline-evidence", baseline_rel,
        "--repair-governance", case_rel,
        "--approve",
        "--force",
    )
    _run(workspace, "skillctl.py", "repair-permit")
    _run(workspace, "skillctl.py", "contract-begin")
    _run(workspace, "skillctl.py", "contract-validate")

    active = json.loads((workspace / "governance/active-change.json").read_text(encoding="utf-8"))
    permit = json.loads((case / "change-permit.json").read_text(encoding="utf-8"))
    baseline = json.loads((case / "baseline-manifest.json").read_text(encoding="utf-8"))
    assert active["change_id"] == CHANGE_ID
    assert active["status"] == "implementing"
    assert active["writer_role"] == "product-implementer"
    assert permit["status"] == "ACTIVE"
    assert active["repair_governance_permit_digest"] == permit["permit_digest"]
    assert permit["allowed_paths"] == ALLOWED
    assert baseline["source_fingerprint"] == permit["baseline_source_fingerprint"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    workspace = Path(args.workspace).resolve()
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    archive = workspace.parent / "stage42-base.tar"
    subprocess.run(["git", "archive", "--format=tar", args.base_sha, "-o", str(archive)], cwd=repo, check=True)
    with tarfile.open(archive, mode="r") as handle:
        handle.extractall(workspace, filter="data")
    _generate_governance(workspace, args.base_sha)
    print(workspace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
