from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path.cwd().resolve()
CHANGE_ID = "repair-stage4-2-dependency-obligation-evidence-pipeline"
INPUT_REL = Path(".quality/stage42-dependency-obligation-red-input")
ALLOWED = [
    "services/agent-service/src/agent_core/goal_graph/dependency_alignment.py",
    "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
    "services/agent-service/tests/runtime/test_dependency_alignment_authority.py",
    "services/agent-service/tests/runtime/test_dependency_obligation_evidence_pipeline.py",
]


def main() -> int:
    input_dir = ROOT / INPUT_REL
    input_dir.mkdir(parents=True, exist_ok=True)
    target = input_dir / "target.md"
    claim = input_dir / "claim.json"
    policy = input_dir / "focused-policy.json"
    evidence = ROOT / ".quality" / "product-code" / CHANGE_ID / "baseline"
    state = ROOT / ".quality" / "product-code" / CHANGE_ID / "state"

    target.write_text(
        "# 目标\n\n"
        f"- 目标 ID：{CHANGE_ID}\n"
        f"- 变更标识：{CHANGE_ID}\n"
        "- 执行上下文：local-change\n"
        "- 目标类型：repair\n\n"
        "Close the Stage 4.2 dependency-obligation evidence pipeline without allowing a pair relation, phase name, complete/matching diagnostic, or call count to substitute for target-compatibility or result-removal counterfactual proof.\n\n"
        "## 允许范围\n\n"
        "- 允许变更路径：" + "，".join(ALLOWED) + "\n"
        "- 新增抽象记录：无\n\n"
        "## 禁止范围\n\n"
        "Do not modify dependency_proof.py, CapabilityGate, GoalOutputRef, transaction Draft/Grant/Attempt/Receipt authority, business tools/services, production dependency activation/defaults, Skill/Judge/Quality policy, or Target/Claim/evidence to obtain a product pass. Baseline execution does not modify product source.\n\n"
        "## 验收条件\n\n"
        "- 最低质量模式：quick\n"
        f"- 声明清单：{(INPUT_REL / 'claim.json').as_posix()}\n"
        "- 验收 ID：STAGE4_2.DEPENDENCY_OBLIGATION_PIPELINE\n\n"
        "Pair relation evidence alone must leave target compatibility and result-removal counterfactual unresolved. Separately validated premise-bound obligation evidence may satisfy those obligations; dependency_proof.py remains the sole deterministic maturity/authority reducer.\n\n"
        "## 基线\n\n"
        "Baseline = untouched exact restored PR #1157 feature source plus a focused read-only acceptance policy. The acceptance gate invokes the existing bridge API without replacing or editing any product/test file and asserts the required fail-closed semantics. Current source is expected to fail because dependency_alignment.py hard-codes target_compatibility=PASS and counterfactual=PASS from a pair decision.\n\n"
        "## 修复轮次\n\n"
        "- 最大轮次：8\n"
        "- 当前轮次：1\n"
        "- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。\n",
        encoding="utf-8",
    )

    claim.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_id": CHANGE_ID,
                "claims": [
                    {
                        "id": "STAGE4_2.DEPENDENCY_OBLIGATION_PIPELINE",
                        "statement": "Pair decisions are observations only; target compatibility and current-turn result-removal counterfactual remain unresolved unless separately validated premise-bound evidence is present before deterministic dependency authority can seal.",
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
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    program = r'''
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
print(json.dumps(observed, sort_keys=True))
assert graph["complete"] is False, "pair decision + closure phase minted dependency authority without obligation-specific evidence"
assert state["obligations"]["target_compatibility"] == proof.UNKNOWN_RESULT, "target compatibility was manufactured instead of remaining UNKNOWN"
assert state["obligations"]["counterfactual"] == proof.UNKNOWN_RESULT, "counterfactual was manufactured instead of remaining UNKNOWN"
'''.strip()
    encoded_program = base64.b64encode(program.encode("utf-8")).decode("ascii")
    gate_command = "import base64;exec(base64.b64decode('" + encoded_program + "'))"

    policy.write_text(
        json.dumps(
            {
                "version": "stage42-dependency-obligation-red-v1",
                "steps": [
                    {
                        "id": "stage42-red-proof",
                        "name": "Stage 4.2 pair-only authority counterexample",
                        "modes": ["quick"],
                        "kind": "shell",
                        "argv": [sys.executable, "-B", "-c", gate_command],
                        "owner": "quality-controller",
                        "category": "counterexample-regression",
                        "blocking_level": "required",
                        "repair_playbook": "repair the product evidence boundary; do not weaken this focused acceptance contract",
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
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    if evidence.exists():
        raise SystemExit(f"refusing to reuse baseline evidence directory: {evidence}")

    cmd = [
        sys.executable,
        "-B",
        str(ROOT / "scripts/quality_loop.py"),
        "--workspace-root",
        str(ROOT),
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
    ]
    completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    print("$", *cmd)
    print(completed.stdout)
    print(completed.stderr, file=sys.stderr)

    record = evidence / "baseline-record.json"
    summary_path = evidence / "run-summary.json"
    if not record.is_file() or not summary_path.is_file():
        raise SystemExit("canonical Stage 4.2 baseline was not recorded")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert completed.returncode != 0, completed.returncode
    assert summary["run_kind"] == "baseline", summary
    assert summary["loop_status"] == "BASELINE_RECORDED", summary
    assert summary["decision"] == "FAIL", summary
    assert summary["target_identity"]["id"] == CHANGE_ID, summary
    assert summary["selected_gate_ids"] == ["stage42-red-proof"], summary["selected_gate_ids"]
    assert len(summary["results"]) == 1, summary["results"]
    result = summary["results"][0]
    assert result["id"] == "stage42-red-proof", summary["results"]
    assert result["status"] == "FAIL", summary["results"]
    assert len(summary["claim_results"]) == 1, summary["claim_results"]
    assert summary["claim_results"][0]["id"] == "STAGE4_2.DEPENDENCY_OBLIGATION_PIPELINE"
    assert summary["claim_results"][0]["status"] == "FAILED", summary["claim_results"]
    assert '"graph_complete": true' in str(result.get("stdout") or ""), result
    assert '"target_compatibility": "PASS"' in str(result.get("stdout") or ""), result
    assert '"counterfactual": "PASS"' in str(result.get("stdout") or ""), result
    assert "minted dependency authority" in str(result.get("stderr") or ""), result

    print("=== STAGE 4.2 FORMAL RED BASELINE RECORDED ===")
    print(
        json.dumps(
            {
                "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "process_exit_code": completed.returncode,
                "decision": summary["decision"],
                "loop_status": summary["loop_status"],
                "claim_results": summary["claim_results"],
                "result": result,
                "baseline_record": record.relative_to(ROOT).as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
