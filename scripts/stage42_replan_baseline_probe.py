from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CHANGE_ID = "probe-stage4-2-dependency-obligation-evidence-pipeline"
ALLOWED = [
    "services/agent-service/src/agent_core/goal_graph/dependency_alignment.py",
    "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
    "services/agent-service/tests/runtime/test_dependency_alignment_authority.py",
    "services/agent-service/tests/runtime/test_dependency_obligation_evidence_pipeline.py",
]


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run([sys.executable, "-B", *args], cwd=ROOT, text=True, capture_output=True)
    print("$", sys.executable, "-B", *args)
    print(completed.stdout)
    print(completed.stderr, file=sys.stderr)
    if check and completed.returncode:
        raise SystemExit(completed.returncode)
    return completed


def main() -> int:
    target = ROOT / "governance" / "targets" / f"{CHANGE_ID}.md"
    claim = ROOT / "governance" / "claims" / f"{CHANGE_ID}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    claim.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# 目标\n\n"
        f"- 目标 ID：{CHANGE_ID}\n"
        f"- 变更标识：portable-{CHANGE_ID}\n"
        "- 执行上下文：local-change\n"
        "- 目标类型：repair\n\n"
        "Close the dependency obligation evidence pipeline so pair decisions cannot manufacture target-compatibility/counterfactual PASS, while legitimate validated semantic/counterfactual evidence can still reach the deterministic reducer.\n\n"
        "## 允许范围\n\n"
        "- 允许变更路径：" + ", ".join(f"`{p}`" for p in ALLOWED) + "\n"
        "- 新增抽象记录：无\n\n"
        "## 禁止范围\n\n"
        "Do not modify dependency_proof.py, CapabilityGate, transaction authority, business tools/services, production activation, Skill/Judge/Quality policy, Target/Claim/Judge/evidence to obtain a pass.\n\n"
        "## 验收条件\n\n"
        "- 最低质量模式：quick\n"
        f"- 声明清单：`governance/claims/{CHANGE_ID}.json`\n"
        "- 验收 ID：`STAGE4_2.DEPENDENCY_OBLIGATION_PIPELINE`\n\n"
        "Pair relation evidence alone must not satisfy target compatibility or result-removal counterfactual obligations. A separately validated, premise-bound obligation record can satisfy those obligations and the deterministic dependency reducer remains the only authority seal.\n\n"
        "## 基线\n\n"
        "Current bridge hard-codes target_compatibility=PASS and counterfactual=PASS from a pair decision; current goal-planning normalization drops distinct obligation evidence.\n\n"
        "## 修复轮次\n\n- 最大轮次：8\n- 当前轮次：1\n- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。\n",
        encoding="utf-8",
    )
    claim.write_text(json.dumps({
        "schema_version": 1,
        "target_id": CHANGE_ID,
        "claims": [{
            "id": "STAGE4_2.DEPENDENCY_OBLIGATION_PIPELINE",
            "statement": "Pair decisions are observations only; target compatibility and result-removal counterfactual require separately validated premise-bound evidence before the deterministic dependency reducer can seal authority.",
            "risk": "P1",
            "required_mode": "quick",
            "evidence_kind": "counterexample",
            "required_gates": ["python-test-suites"],
            "evidence_refs": ["gate-log:python-test-suites"],
            "owner": "dependency-proof-authority",
            "closure_requirement": "regression-transition",
        }],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    args = [
        "skillctl.py", "product-init",
        "--change-id", CHANGE_ID,
        "--goal", "Close the Stage 4.2 dependency obligation evidence pipeline without manufacturing proof obligations.",
        "--target-kind", "repair",
    ]
    for path in ALLOWED:
        args += ["--allow", path]
    args += [
        "--affected-module", "agent_core.goal_graph.dependency_alignment",
        "--affected-module", "agent_core.lifecycle.goal_planning",
        "--invariant", "pair decision is observation, not target/counterfactual authority",
        "--invariant", "missing obligation evidence fails closed",
        "--invariant", "dependency_proof.py remains the sole deterministic maturity authority",
        "--minimum-mode", "quick",
        "--quality-target", f"governance/targets/{CHANGE_ID}.md",
        "--approve",
        "--force",
    ]
    run(*args)
    baseline = run("skillctl.py", "product-baseline", check=False)
    evidence = ROOT / ".quality" / "product-code" / CHANGE_ID / "baseline"
    summary_path = evidence / "run-summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        print("=== BASELINE SUMMARY ===")
        print(json.dumps({
            "decision": summary.get("decision"),
            "loop_status": summary.get("loop_status"),
            "claim_results": summary.get("claim_results"),
            "results": [
                {
                    "id": row.get("id"),
                    "status": row.get("status"),
                    "exit_code": row.get("exit_code"),
                    "stderr_tail": str(row.get("stderr") or "")[-3000:],
                    "stdout_tail": str(row.get("stdout") or "")[-3000:],
                }
                for row in summary.get("results") or []
                if row.get("status") != "PASS"
            ],
        }, ensure_ascii=False, indent=2))
    print(f"BASELINE_EXIT={baseline.returncode}")
    return baseline.returncode


if __name__ == "__main__":
    raise SystemExit(main())
