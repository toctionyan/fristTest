#!/usr/bin/env python3
"""Bounded repair orchestrator with ``quality_loop.py`` as the independent judge.

The orchestrator never edits product source itself.  It publishes stable issue
records to an explicitly configured fixer, then asks the read-only quality
controller for targeted and complete verification evidence.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import quality_loop  # noqa: E402

CONTROL_PLANE_DIR = SCRIPTS.parent / "skill-system" / "controller"
if str(CONTROL_PLANE_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_PLANE_DIR))
from fixer_env import build_fixer_environment  # type: ignore  # noqa: E402
from issue_state import merge_issue_state  # type: ignore  # noqa: E402
from trusted_judge import resolve as resolve_trusted_judge  # type: ignore  # noqa: E402


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _normalized_failure(value: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:4000]


def _failure_kind(result: dict[str, Any]) -> str:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    declared = str(metadata.get("failure_kind") or "").strip()
    if declared:
        return declared
    if result.get("status") == quality_loop.BLOCKED:
        return "environment"
    if result.get("exit_code") == 124:
        return "timeout"
    category = str(result.get("category") or "verification")
    return "test_or_contract" if "test" in category else "verification"


def issue_records(summary: dict[str, Any], *, evidence_dir: Path) -> list[dict[str, Any]]:
    """Convert direct judge failures into stable, machine-consumable issues."""
    issues: list[dict[str, Any]] = []
    for result in summary.get("results") or []:
        if not isinstance(result, dict) or result.get("status") not in {
            quality_loop.FAIL,
            quality_loop.BLOCKED,
        }:
            continue
        gate_id = str(result.get("id") or "unknown")
        owner = str(result.get("owner") or "unassigned")
        category = str(result.get("category") or "verification")
        failure_kind = _failure_kind(result)
        normalized = _normalized_failure(str(result.get("stderr") or ""))
        signature = json.dumps(
            {
                "gate_id": gate_id,
                "owner": owner,
                "category": category,
                "failure_kind": failure_kind,
                "failure": normalized,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        issue_id = "QI-" + hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20]
        issues.append(
            {
                "schema_version": 1,
                "issue_id": issue_id,
                "status": "BLOCKED" if result.get("status") == quality_loop.BLOCKED else "OPEN",
                "gate_id": gate_id,
                "owner": owner,
                "category": category,
                "failure_kind": failure_kind,
                "failure_summary": normalized,
                "repair_playbook": str(result.get("repair_playbook") or "repair the owning contract"),
                "evidence": {
                    "stdout": str(evidence_dir / "steps" / f"{gate_id}.stdout.txt"),
                    "stderr": str(evidence_dir / "steps" / f"{gate_id}.stderr.txt"),
                },
            }
        )
    return sorted(issues, key=lambda item: (item["status"], item["gate_id"], item["issue_id"]))


def _write_issue_state(
    path: Path,
    current: list[dict[str, Any]],
    *,
    summary: dict[str, Any] | None = None,
) -> None:
    previous: list[dict[str, Any]] = []
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            previous = [item for item in payload.get("issues") or [] if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError):
            previous = []
    merged = merge_issue_state(
        previous,
        current,
        list((summary or {}).get("results") or []),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": 2, "updated_at": _now(), "issues": merged},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _advance_round(target: Path) -> None:
    text = target.read_text(encoding="utf-8")
    current = re.search(r"(当前轮次\s*[:：]\s*)(\d+)", text)
    maximum = re.search(r"最大轮次\s*[:：]\s*(\d+)", text)
    if not current or not maximum:
        raise ValueError("target does not declare 当前轮次 and 最大轮次")
    next_round = int(current.group(2)) + 1
    if next_round > int(maximum.group(1)):
        raise ValueError("repair round budget is exhausted")
    target.write_text(
        text[: current.start(2)] + str(next_round) + text[current.end(2) :],
        encoding="utf-8",
    )


def _run_judge(
    *,
    workspace: Path,
    policy: Path,
    target: Path,
    baseline: Path,
    state_dir: Path,
    evidence_dir: Path,
    mode: str,
    rerun_from: str | None = None,
    judge_root: Path | None = None,
) -> tuple[int, dict[str, Any] | None]:
    judge_workspace = (judge_root or workspace).resolve()
    command = [
        sys.executable,
        "-B",
        str(judge_workspace / "scripts/quality_loop.py"),
        "--workspace-root",
        str(workspace),
        "--policy",
        str(policy),
        "--mode",
        mode,
        "--target",
        str(target),
        "--baseline-evidence",
        str(baseline),
        "--state-dir",
        str(state_dir),
        "--evidence-dir",
        str(evidence_dir),
    ]
    if rerun_from:
        command.extend(["--rerun-from", rerun_from])
    judge_env = os.environ.copy()
    judge_env.update({
        "SKILL_JUDGE_ROOT": str(judge_workspace),
        "SKILL_JUDGE_TRUST_MODE": (
            "external-readonly" if judge_workspace != workspace.resolve() else "workspace-fallback"
        ),
    })
    completed = subprocess.run(command, cwd=workspace, env=judge_env, text=True, check=False)
    summary_path = evidence_dir / "run-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else None
    return completed.returncode, summary


def validate_repair_inputs(
    *,
    workspace: Path,
    policy: Path,
    target: Path,
    baseline: Path,
) -> dict[str, Any]:
    """Fail closed before an external fixer can observe or mutate the workspace."""
    workspace = workspace.resolve()
    policy = policy.resolve()
    target = target.resolve()
    baseline = baseline.resolve()
    for label, path in (("policy", policy), ("target", target), ("baseline", baseline)):
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"repair {label} must stay inside the workspace") from exc
    if not policy.is_file() or not target.is_file() or not baseline.is_dir():
        raise ValueError("repair policy, target and baseline evidence must already exist")

    policy_payload = quality_loop._load_json(policy)
    quality_loop._validate_policy(policy_payload)
    try:
        target_contract = quality_loop._parse_target(target, workspace=workspace)
    except ValueError as exc:
        raise ValueError(f"repair target identity is invalid or changed: {exc}") from exc
    if (
        target_contract["context"] != "local-change"
        or target_contract["kind"] not in quality_loop.TRANSITION_TARGET_KINDS
    ):
        raise ValueError(
            "repair orchestrator requires a local-change repair, migration, or revert target"
        )
    baseline_record = quality_loop._load_baseline(
        baseline,
        workspace=workspace,
        target=target_contract,
        policy_fingerprint=quality_loop._sha256_file(policy),
    )
    summary_path = baseline / "run-summary.json"
    summary = quality_loop._load_json(summary_path)
    if (
        summary.get("run_kind") != "baseline"
        or summary.get("decision") != quality_loop.FAIL
        or summary.get("loop_status") != "BASELINE_RECORDED"
    ):
        raise ValueError(
            "repair orchestrator requires an attested failing BASELINE_RECORDED run"
        )
    failed_claims = [
        str(item.get("id"))
        for item in summary.get("claim_results") or []
        if isinstance(item, dict) and item.get("status") == "FAILED"
    ]
    if not failed_claims:
        raise ValueError("repair baseline does not contain a failing acceptance claim")
    return {
        "target": target_contract,
        "baseline": baseline_record,
        "summary": summary,
        "failed_claim_ids": failed_claims,
    }


def _protected_input_fingerprints(
    *, policy: Path, target: Path, baseline: Path
) -> dict[str, str]:
    protected = {
        "policy": policy,
        "target": target,
        "baseline-record": baseline / "baseline-record.json",
        "baseline-summary": baseline / "run-summary.json",
        "baseline-attestation": baseline / "evidence-attestation.json",
    }
    return {
        name: quality_loop._sha256_file(path)
        for name, path in protected.items()
        if path.is_file()
    }



def _root_failure_gates(summary: dict[str, Any], policy_payload: dict[str, Any]) -> list[str]:
    failed = {
        str(item.get("id"))
        for item in summary.get("results") or []
        if isinstance(item, dict) and item.get("status") == quality_loop.FAIL
    }
    dependencies = {
        str(step.get("id")): {str(dep) for dep in step.get("depends_on") or []}
        for step in policy_payload.get("steps") or []
        if isinstance(step, dict) and step.get("id")
    }

    def has_failed_ancestor(gate_id: str, seen: set[str] | None = None) -> bool:
        seen = set(seen or ())
        if gate_id in seen:
            return False
        seen.add(gate_id)
        for parent in dependencies.get(gate_id, set()):
            if parent in failed or has_failed_ancestor(parent, seen):
                return True
        return False

    roots = sorted(gate for gate in failed if not has_failed_ancestor(gate))
    return roots or sorted(failed)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--policy", default="governance/quality-loop-policy.json")
    parser.add_argument("--target", required=True)
    parser.add_argument("--baseline-evidence", required=True)
    parser.add_argument("--mode", choices=quality_loop.MODES, default="quick")
    parser.add_argument("--state-dir", default=".quality/loop-state")
    parser.add_argument("--evidence-root", default=".quality/evidence/repair-loop")
    parser.add_argument("--issues-file", default=".quality/issues/active.json")
    parser.add_argument("--max-cycles", type=int, default=8)
    parser.add_argument("--skip-targeted", action="store_true")
    parser.add_argument("--trusted-judge-root")
    parser.add_argument(
        "--require-external-judge",
        action="store_true",
        help="require the Judge to live outside the writable candidate workspace",
    )
    parser.add_argument("--fix-command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    workspace = Path(args.workspace_root).resolve()
    policy = (workspace / args.policy).resolve()
    target = (workspace / args.target).resolve()
    baseline = (workspace / args.baseline_evidence).resolve()
    state_dir = (workspace / args.state_dir).resolve()
    evidence_root = (workspace / args.evidence_root).resolve()
    issues_file = (workspace / args.issues_file).resolve()
    try:
        judge_root, judge_trust_mode = resolve_trusted_judge(
            workspace,
            args.trusted_judge_root,
            require_external=args.require_external_judge,
        )
    except ValueError as exc:
        parser.error(str(exc))
    try:
        validated = validate_repair_inputs(
            workspace=workspace,
            policy=policy,
            target=target,
            baseline=baseline,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    summary = validated["summary"]

    for cycle in range(1, min(args.max_cycles, quality_loop.MAX_REPAIR_ROUNDS) + 1):
        current_evidence = Path(str(summary.get("evidence_dir") or baseline)).resolve()
        issues = issue_records(summary, evidence_dir=current_evidence)
        _write_issue_state(issues_file, issues, summary=summary)
        if summary.get("loop_status") == "CONVERGED":
            return 0
        if any(item["status"] == "BLOCKED" for item in issues):
            print(f"repair loop blocked; provision environment from {issues_file}", file=sys.stderr)
            return 2
        if not args.fix_command:
            print(f"repair action required; provide --fix-command, issues: {issues_file}", file=sys.stderr)
            return 3

        try:
            validate_repair_inputs(
                workspace=workspace,
                policy=policy,
                target=target,
                baseline=baseline,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"repair preflight failed before fixer: {exc}", file=sys.stderr)
            return 7
        protected_before = _protected_input_fingerprints(
            policy=policy, target=target, baseline=baseline
        )
        before = quality_loop.workspace_snapshot(workspace)
        environment = build_fixer_environment(
            os.environ,
            issue_file=issues_file,
            repair_plan=current_evidence / "repair-plan.json",
            evidence_dir=current_evidence,
            target=target,
            trusted_judge_root=judge_root if judge_trust_mode == "external-readonly" else None,
        )
        fixed = subprocess.run(args.fix_command, cwd=workspace, env=environment, check=False)
        if fixed.returncode:
            print(f"fix command failed with exit code {fixed.returncode}", file=sys.stderr)
            return fixed.returncode
        protected_after = _protected_input_fingerprints(
            policy=policy, target=target, baseline=baseline
        )
        if protected_after != protected_before:
            print(
                "fix command modified protected target, policy, or baseline evidence",
                file=sys.stderr,
            )
            return 7
        after = quality_loop.workspace_snapshot(workspace)
        if before["fingerprint"] == after["fingerprint"]:
            print("fix command produced no governed source change", file=sys.stderr)
            return 4

        policy_payload = quality_loop._load_json(policy)
        failed_gates = _root_failure_gates(summary, policy_payload)
        targeted_failed = False
        if failed_gates and not args.skip_targeted:
            for index, gate_id in enumerate(failed_gates, start=1):
                targeted_returncode, targeted = _run_judge(
                    workspace=workspace,
                    policy=policy,
                    target=target,
                    baseline=baseline,
                    state_dir=state_dir,
                    evidence_dir=evidence_root / f"cycle-{cycle:02d}-targeted-{index:02d}-{gate_id}",
                    mode=args.mode,
                    rerun_from=gate_id,
                    judge_root=judge_root,
                )
                if (
                    targeted_returncode != 0
                    or targeted is None
                    or targeted.get("decision") != quality_loop.PASS
                    or targeted.get("loop_status") != "TARGETED_REGRESSION_PASSED"
                ):
                    if targeted is None:
                        return 5
                    summary = targeted
                    targeted_failed = True
                    break
            if targeted_failed:
                continue
        _returncode, verified = _run_judge(
            workspace=workspace,
            policy=policy,
            target=target,
            baseline=baseline,
            state_dir=state_dir,
            evidence_dir=evidence_root / f"cycle-{cycle:02d}-full",
            mode=args.mode,
            judge_root=judge_root,
        )
        if verified is None:
            return 5
        summary = verified
        if summary.get("loop_status") == "CONVERGED":
            _write_issue_state(issues_file, [], summary=summary)
            return 0
        if summary.get("loop_status") in {
            "ARCHITECTURE_REPLAN_REQUIRED",
            "STOPPED_MAX_REPAIRS",
        }:
            return 6
        if summary.get("loop_status") == "REPAIR_REQUIRED":
            _advance_round(target)
            continue
        if summary.get("decision") == quality_loop.BLOCKED:
            return 2
        return 1
    return 6


if __name__ == "__main__":
    raise SystemExit(main())
