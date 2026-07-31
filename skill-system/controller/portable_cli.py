from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
CONTRACT_CLI = CONTROLLER / "change_contract_cli.py"
QUALITY_BRIDGE = CONTROLLER / "product_quality_bridge.py"
PROFILE_RUNNER = CONTROLLER / "profile_runner.py"
REPAIR_LOOP = ROOT / "scripts" / "repair_loop.py"


def _run(argv: Sequence[str]) -> int:
    return subprocess.run(list(argv), cwd=ROOT, check=False).returncode


def _forward(script: Path, command: str, rest: list[str]) -> int:
    return _run([sys.executable, "-B", str(script), command, *rest])


def _safe_id(value: str) -> str:
    if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-" for ch in value):
        raise SystemExit("change-id contains unsupported characters")
    return value


def cmd_product_scaffold(args: argparse.Namespace) -> int:
    change_id = _safe_id(args.change_id)
    target_path = ROOT / "governance" / "targets" / f"{change_id}.md"
    claim_path = ROOT / "governance" / "claims" / f"{change_id}.json"
    if (target_path.exists() or claim_path.exists()) and not args.force:
        raise SystemExit("target or claim scaffold already exists; use --force to replace")
    allowed = list(dict.fromkeys(args.allow))
    gates = list(dict.fromkeys(args.required_gate))
    refs = list(dict.fromkeys(args.evidence_ref))
    if not allowed or not gates or not refs:
        raise SystemExit("product-scaffold requires --allow, --required-gate and --evidence-ref")
    closure = "regression-transition" if args.target_kind in {"repair", "migration", "revert"} else "current-pass"
    claim = {
        "schema_version": 1,
        "target_id": change_id,
        "claims": [{
            "id": args.claim_id,
            "statement": args.claim_statement,
            "risk": args.risk,
            "required_mode": args.minimum_mode,
            "evidence_kind": args.evidence_kind,
            "required_gates": gates,
            "evidence_refs": refs,
            "owner": args.owner,
            "closure_requirement": closure,
        }],
    }
    target = f"""# 目标

- 目标 ID：{change_id}
- 变更标识：portable-{change_id}
- 执行上下文：{'ci' if args.target_kind == 'certification' else 'local-change'}
- 目标类型：{args.target_kind}

{args.goal}

## 允许范围

- 允许变更路径：{', '.join(f'`{value}`' for value in allowed)}
- 新增抽象记录：{args.new_abstraction_record}

## 禁止范围

{args.forbidden_description}

## 验收条件

- 最低质量模式：{args.minimum_mode}
- 声明清单：`governance/claims/{change_id}.json`
- 验收 ID：`{args.claim_id}`

{args.acceptance_description}

## 基线

{args.baseline_description}

## 修复轮次

- 最大轮次：{args.max_rounds}
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
"""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(target, encoding="utf-8")
    claim_path.write_text(json.dumps(claim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "quality_target": target_path.relative_to(ROOT).as_posix(),
        "claim_manifest": claim_path.relative_to(ROOT).as_posix(),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_product_init(args: argparse.Namespace) -> int:
    argv = [
        sys.executable, "-B", str(CONTRACT_CLI), "init",
        "--profile", "product-code",
        "--change-id", args.change_id,
        "--goal", args.goal,
        "--target-kind", args.target_kind,
        "--minimum-mode", args.minimum_mode,
    ]
    for value in args.allow:
        argv.extend(["--allow", value])
    for value in args.forbid:
        argv.extend(["--forbid", value])
    for value in args.affected_module:
        argv.extend(["--affected-module", value])
    for value in args.invariant:
        argv.extend(["--invariant", value])
    for value in args.variance:
        argv.extend(["--variance", value])
    for flag, value in (
        ("--quality-target", args.quality_target),
        ("--baseline-evidence", args.baseline_evidence),
        ("--decision-record", args.decision_record),
        ("--architecture-policy-delta", args.architecture_policy_delta),
        ("--baseline-policy-id", args.baseline_policy_id),
    ):
        if value:
            argv.extend([flag, value])
    if args.approve:
        argv.append("--approve")
    if args.force:
        argv.append("--force")
    return _run(argv)


def cmd_product_repair(args: argparse.Namespace) -> int:
    if not args.fix_command:
        raise SystemExit("product-repair-loop requires a fixer command after --")
    contract = json.loads((ROOT / "governance" / "active-change.json").read_text(encoding="utf-8"))
    target = contract.get("quality_target")
    baseline = contract.get("baseline_evidence")
    if not target or not baseline:
        raise SystemExit("active product contract requires quality_target and product-baseline before repair-loop")
    mode = str(contract.get("minimum_quality_mode") or "quick")
    evidence_root = ROOT / ".quality" / "product-code" / str(contract.get("change_id")) / "repair-loop"
    argv = [
        sys.executable, "-B", str(REPAIR_LOOP),
        "--workspace-root", str(ROOT),
        "--target", str(ROOT / str(target)),
        "--baseline-evidence", str(ROOT / str(baseline)),
        "--mode", mode,
        "--state-dir", str(ROOT / ".quality" / "product-code" / str(contract.get("change_id")) / "state"),
        "--evidence-root", str(evidence_root),
        "--issues-file", str(ROOT / ".quality" / "product-code" / str(contract.get("change_id")) / "issues.json"),
    ]
    if args.trusted_judge_root:
        argv.extend(["--trusted-judge-root", args.trusted_judge_root])
    if args.require_external_judge:
        argv.append("--require-external-judge")
    argv.extend(["--max-cycles", str(args.max_cycles), "--fix-command", *args.fix_command])
    completed = subprocess.run(argv, cwd=ROOT, check=False)
    if completed.returncode != 0:
        return completed.returncode
    converged: Path | None = None
    for candidate in sorted(evidence_root.glob("cycle-*-full"), reverse=True):
        summary_path = candidate / "run-summary.json"
        if not summary_path.is_file():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if summary.get("decision") == "PASS" and summary.get("loop_status") == "CONVERGED":
            converged = candidate
            break
    if converged is None:
        raise SystemExit("repair-loop exited successfully but no CONVERGED full evidence was found")
    return _run([
        sys.executable, "-B", str(QUALITY_BRIDGE), "record",
        "--evidence", converged.relative_to(ROOT).as_posix(),
        "--mode", mode,
    ])


def cmd_profiles(args: argparse.Namespace) -> int:
    profiles = sorted(path.stem for path in (ROOT / "skill-system" / "profiles").glob("*.json"))
    if args.run:
        return _run([sys.executable, "-B", str(PROFILE_RUNNER), args.run])
    print(json.dumps({"status": "PASS", "profiles": profiles}, ensure_ascii=False, indent=2))
    return 0


FORWARD_ALIASES = {
    "contract-validate": (CONTRACT_CLI, "validate"),
    "contract-show": (CONTRACT_CLI, "show"),
    "contract-approve": (CONTRACT_CLI, "approve"),
    "contract-configure": (CONTRACT_CLI, "configure"),
    "contract-begin": (CONTRACT_CLI, "begin"),
    "attest-review": (CONTRACT_CLI, "attest-review"),
    "contract-verify": (CONTRACT_CLI, "verify"),
    "contract-close": (CONTRACT_CLI, "close"),
    "architecture-preview": (CONTRACT_CLI, "architecture-preview"),
    "architecture-promote": (CONTRACT_CLI, "architecture-promote"),
    "product-baseline": (QUALITY_BRIDGE, "baseline"),
    "product-verify": (QUALITY_BRIDGE, "verify"),
    "status": (QUALITY_BRIDGE, "status"),
}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in FORWARD_ALIASES:
        script, command = FORWARD_ALIASES[sys.argv[1]]
        return _forward(script, command, sys.argv[2:])
    parser = argparse.ArgumentParser(prog="skillctl", description="Portable Skill and product-code governance CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    scaffold = sub.add_parser("product-scaffold")
    scaffold.add_argument("--change-id", required=True)
    scaffold.add_argument("--target-kind", choices=["repair", "migration", "revert", "certification"], default="repair")
    scaffold.add_argument("--goal", required=True)
    scaffold.add_argument("--allow", action="append", default=[], required=True)
    scaffold.add_argument("--minimum-mode", choices=["static", "quick", "integration", "release"], default="quick")
    scaffold.add_argument("--claim-id", required=True)
    scaffold.add_argument("--claim-statement", required=True)
    scaffold.add_argument("--risk", choices=["P0", "P1", "P2", "P3"], default="P1")
    scaffold.add_argument("--evidence-kind", choices=["static-contract", "counterexample", "integration", "release-provenance"], default="counterexample")
    scaffold.add_argument("--required-gate", action="append", default=[], required=True)
    scaffold.add_argument("--evidence-ref", action="append", default=[], required=True)
    scaffold.add_argument("--owner", required=True)
    scaffold.add_argument("--new-abstraction-record", default="无")
    scaffold.add_argument("--forbidden-description", default="不得修改 Change Contract、Claim、Baseline、Judge 或 Evidence 来获得通过；不得修改合同范围外路径。")
    scaffold.add_argument("--acceptance-description", default="必须由当前源码的直接测试或 Gate 证据证明，不接受历史日志或模型口头结论。")
    scaffold.add_argument("--baseline-description", default="在候选实现修改前运行 product-baseline；transition claim 必须真实失败。")
    scaffold.add_argument("--max-rounds", type=int, choices=range(1, 9), default=4)
    scaffold.add_argument("--force", action="store_true")
    scaffold.set_defaults(func=cmd_product_scaffold)

    product_init = sub.add_parser("product-init")
    product_init.add_argument("--change-id", required=True)
    product_init.add_argument("--goal", required=True)
    product_init.add_argument("--target-kind", choices=["diagnosis", "design", "oracle-review", "repair", "migration", "revert", "certification"], required=True)
    product_init.add_argument("--allow", action="append", default=[], required=True)
    product_init.add_argument("--forbid", action="append", default=[])
    product_init.add_argument("--affected-module", action="append", default=[])
    product_init.add_argument("--invariant", action="append", default=[])
    product_init.add_argument("--minimum-mode", choices=["static", "quick", "integration", "release"], default="static")
    product_init.add_argument("--quality-target")
    product_init.add_argument("--baseline-evidence")
    product_init.add_argument("--decision-record")
    product_init.add_argument("--variance", action="append", default=[])
    product_init.add_argument("--architecture-policy-delta")
    product_init.add_argument("--baseline-policy-id")
    product_init.add_argument("--approve", action="store_true")
    product_init.add_argument("--force", action="store_true")
    product_init.set_defaults(func=cmd_product_init)

    for name in FORWARD_ALIASES:
        sub.add_parser(name)

    repair = sub.add_parser("product-repair-loop")
    repair.add_argument("--max-cycles", type=int, default=8)
    repair.add_argument("--trusted-judge-root")
    repair.add_argument("--require-external-judge", action="store_true")
    repair.add_argument("fix_command", nargs=argparse.REMAINDER)
    repair.set_defaults(func=cmd_product_repair)

    profiles = sub.add_parser("profiles")
    profiles.add_argument("--run")
    profiles.set_defaults(func=cmd_profiles)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
