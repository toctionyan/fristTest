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
from task_run import (  # type: ignore  # noqa: E402
    PrematureCompletionError,
    TaskRunDriftError,
    TaskRunStore,
    fingerprint,
    stable_task_id,
)
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


def _workspace_fingerprint(workspace: Path) -> str:
    return str(quality_loop.workspace_snapshot(workspace).get("fingerprint") or "")


def _evidence_ref(workspace: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(workspace.resolve()))
    except ValueError:
        return str(resolved)


def _summary_path(evidence_dir: Path) -> Path:
    return evidence_dir / "run-summary.json"


def _load_compatible_summary(evidence_dir: Path, *, workspace_fingerprint: str) -> dict[str, Any] | None:
    path = _summary_path(evidence_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    recorded = str(payload.get("workspace_snapshot_fingerprint") or "")
    # Legacy or partial summaries without an exact source fingerprint are not
    # resumable evidence.  Reusing one could certify a different workspace.
    if not recorded or recorded != workspace_fingerprint:
        return None
    return payload


def _run_judge_resilient(
    *,
    task_run: TaskRunStore,
    workspace: Path,
    policy: Path,
    target: Path,
    baseline: Path,
    state_dir: Path,
    evidence_dir: Path,
    mode: str,
    rerun_from: str | None,
    judge_root: Path,
) -> tuple[int, dict[str, Any] | None, tuple[str, ...], Path | None]:
    """Acquire judge evidence through bounded, durable fallback strategies."""
    workspace_fingerprint = _workspace_fingerprint(workspace)
    arguments = {
        "policy": _evidence_ref(workspace, policy),
        "target": _evidence_ref(workspace, target),
        "baseline": _evidence_ref(workspace, baseline),
        "evidence_dir": _evidence_ref(workspace, evidence_dir),
        "mode": mode,
        "rerun_from": rerun_from,
        "judge_root": str(judge_root),
    }
    strategies = (
        "recover-existing-summary",
        "execute-readonly-judge",
        "execute-readonly-judge-retry",
    )
    attempted: list[str] = []
    while True:
        plan = task_run.plan_action(
            action_name="quality-judge",
            arguments=arguments,
            state_fingerprint=workspace_fingerprint,
            strategies=strategies,
            max_attempts_per_strategy=1,
        )
        if plan.decision == "BLOCKED" or plan.strategy is None:
            return 78, None, tuple(attempted), None
        attempted.append(plan.strategy)
        strategy_evidence = evidence_dir
        returncode = 78
        summary: dict[str, Any] | None = None
        if plan.strategy == "recover-existing-summary":
            summary = _load_compatible_summary(
                evidence_dir,
                workspace_fingerprint=workspace_fingerprint,
            )
            returncode = 0 if summary is not None else 78
        else:
            strategy_evidence = (
                evidence_dir
                if plan.strategy == "execute-readonly-judge"
                else evidence_dir.with_name(evidence_dir.name + "-retry")
            )
            returncode, summary = _run_judge(
                workspace=workspace,
                policy=policy,
                target=target,
                baseline=baseline,
                state_dir=state_dir,
                evidence_dir=strategy_evidence,
                mode=mode,
                rerun_from=rerun_from,
                judge_root=judge_root,
            )
        evidence_refs = (
            [_evidence_ref(workspace, _summary_path(strategy_evidence))]
            if summary is not None
            else []
        )
        task_run.record_action_result(
            plan,
            result={
                "returncode": returncode,
                "summary_decision": summary.get("decision") if summary else None,
                "loop_status": summary.get("loop_status") if summary else None,
                "evidence_dir": str(strategy_evidence),
            },
            produced_new_evidence=summary is not None,
            evidence_refs=evidence_refs,
        )
        if summary is not None:
            task_run.set_metadata(
                current_summary_path=_evidence_ref(workspace, _summary_path(strategy_evidence)),
                current_evidence_dir=_evidence_ref(workspace, strategy_evidence),
            )
            return returncode, summary, tuple(attempted), _summary_path(strategy_evidence)


def _load_resume_summary(
    task_run: TaskRunStore,
    *,
    workspace: Path,
    fallback_summary: dict[str, Any],
) -> dict[str, Any]:
    raw = str((task_run.payload.get("metadata") or {}).get("current_summary_path") or "")
    if not raw:
        return fallback_summary
    path = Path(raw)
    if not path.is_absolute():
        path = workspace / path
    if not path.is_file():
        return fallback_summary
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback_summary
    return payload if isinstance(payload, dict) else fallback_summary


def _mark_existing_source_change(
    task_run: TaskRunStore,
    *,
    workspace: Path,
    target: Path,
    validated: dict[str, Any],
) -> None:
    condition = (task_run.payload.get("conditions") or {}).get("source_changed") or {}
    if condition.get("satisfied") is True:
        return
    try:
        repair_fingerprint, changed_paths = quality_loop._repair_change_fingerprint(
            workspace,
            baseline=validated["baseline"],
            allowed_paths=tuple(validated["target"].get("allowed_paths") or ()),
            target_path=target,
        )
    except (OSError, ValueError, KeyError):
        return
    if changed_paths:
        task_run.mark_condition(
            "source_changed",
            evidence_refs=[f"workspace-repair:{repair_fingerprint}", *changed_paths[:20]],
        )


def _block_and_return(
    task_run: TaskRunStore,
    *,
    workspace: Path,
    code: str,
    reason: str,
    attempted_strategies: tuple[str, ...] | list[str],
    next_action: str,
    evidence_refs: tuple[str, ...] | list[str] = (),
    exit_code: int,
) -> int:
    task_run.block(
        code=code,
        reason=reason,
        attempted_strategies=attempted_strategies,
        next_action=next_action,
        workspace_fingerprint=_workspace_fingerprint(workspace),
        evidence_refs=evidence_refs,
    )
    return exit_code


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
    parser.add_argument("--task-run-file")
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

    target_identity = quality_loop._target_identity(validated["target"])
    task_id = stable_task_id("repair-loop", target_identity)
    task_run_path = (
        (workspace / args.task_run_file).resolve()
        if args.task_run_file
        else (workspace / ".quality" / "task-runs" / f"{task_id}.json").resolve()
    )
    binding = {
        "workspace": str(workspace),
        "target_identity": target_identity,
        "policy_fingerprint": quality_loop._sha256_file(policy),
        "baseline_attestation_fingerprint": quality_loop._sha256_file(
            baseline / "evidence-attestation.json"
        ),
    }
    required_conditions = (
        "inputs_validated",
        "source_changed",
        "targeted_validation_resolved",
        "full_regression_passed",
        "issues_closed",
    )
    try:
        task_run = TaskRunStore.open_or_create(
            task_run_path,
            task_id=task_id,
            task_kind="repair-loop",
            binding=binding,
            required_conditions=required_conditions,
            current_workspace_fingerprint=_workspace_fingerprint(workspace),
        )
    except TaskRunDriftError as exc:
        print(f"repair task resume rejected: {exc}", file=sys.stderr)
        return 8

    if task_run.payload.get("status") == "COMPLETED":
        return 0
    resume_phase = str(task_run.payload.get("phase") or "")
    task_run.checkpoint(
        status="RUNNING",
        phase="INPUTS_VALIDATED",
        workspace_fingerprint=_workspace_fingerprint(workspace),
        evidence_refs=[
            _evidence_ref(workspace, policy),
            _evidence_ref(workspace, target),
            _evidence_ref(workspace, baseline / "run-summary.json"),
        ],
        metadata={"judge_trust_mode": judge_trust_mode},
    )
    task_run.mark_condition(
        "inputs_validated",
        evidence_refs=[
            _evidence_ref(workspace, baseline / "baseline-record.json"),
            _evidence_ref(workspace, baseline / "evidence-attestation.json"),
        ],
    )
    task_run.set_metadata(
        task_run_file=_evidence_ref(workspace, task_run_path),
        current_summary_path=(task_run.payload.get("metadata") or {}).get(
            "current_summary_path",
            _evidence_ref(workspace, baseline / "run-summary.json"),
        ),
    )
    _mark_existing_source_change(
        task_run,
        workspace=workspace,
        target=target,
        validated=validated,
    )
    summary = _load_resume_summary(
        task_run,
        workspace=workspace,
        fallback_summary=validated["summary"],
    )

    for cycle in range(1, min(args.max_cycles, quality_loop.MAX_REPAIR_ROUNDS) + 1):
        task_run.set_metadata(cycle=cycle)
        current_evidence = Path(str(summary.get("evidence_dir") or baseline)).resolve()
        issues = issue_records(summary, evidence_dir=current_evidence)
        issue_signature = fingerprint(
            {
                "summary_decision": summary.get("decision"),
                "loop_status": summary.get("loop_status"),
                "failure_signature": summary.get("failure_signature"),
                "issue_ids": [item["issue_id"] for item in issues],
            }
        )
        _write_issue_state(issues_file, issues, summary=summary)
        source_changed = bool(
            ((task_run.payload.get("conditions") or {}).get("source_changed") or {}).get("satisfied")
        )
        metadata = task_run.payload.get("metadata") or {}
        if (
            resume_phase == "FIXER_RUNNING"
            and source_changed
            and not metadata.get("fix_pending_validation")
        ):
            task_run.set_metadata(
                last_fixed_issue_signature=issue_signature,
                fix_pending_validation=True,
                reconciled_interrupted_fixer=True,
            )
            metadata = task_run.payload.get("metadata") or {}
        fix_already_applied = bool(
            source_changed
            and metadata.get("fix_pending_validation") is True
            and metadata.get("last_fixed_issue_signature") == issue_signature
        )
        resume_phase = ""
        task_run.checkpoint(
            status="RUNNING",
            phase="ISSUES_CAPTURED",
            workspace_fingerprint=_workspace_fingerprint(workspace),
            evidence_refs=[_evidence_ref(workspace, issues_file)],
            metadata={"cycle": cycle, "issue_ids": [item["issue_id"] for item in issues]},
        )

        if summary.get("loop_status") == "CONVERGED":
            task_run.mark_condition(
                "targeted_validation_resolved",
                evidence_refs=[_evidence_ref(workspace, current_evidence / "run-summary.json")],
            )
            task_run.mark_condition(
                "full_regression_passed",
                evidence_refs=[_evidence_ref(workspace, current_evidence / "run-summary.json")],
            )
            _write_issue_state(issues_file, [], summary=summary)
            task_run.mark_condition("issues_closed", evidence_refs=[_evidence_ref(workspace, issues_file)])
            try:
                task_run.complete(
                    workspace_fingerprint=_workspace_fingerprint(workspace),
                    evidence_refs=[_evidence_ref(workspace, current_evidence / "run-summary.json")],
                )
            except PrematureCompletionError as exc:
                print(str(exc), file=sys.stderr)
                return 9
            return 0

        if any(item["status"] == "BLOCKED" for item in issues):
            print(f"repair loop blocked; provision environment from {issues_file}", file=sys.stderr)
            return _block_and_return(
                task_run,
                workspace=workspace,
                code="ENVIRONMENT_BLOCKED",
                reason="quality evidence reports an environment blocker",
                attempted_strategies=("quality-judge",),
                next_action="provision declared environment and rerun the same task-run",
                evidence_refs=(_evidence_ref(workspace, issues_file),),
                exit_code=2,
            )

        if not fix_already_applied:
            if not args.fix_command:
                print(f"repair action required; provide --fix-command, issues: {issues_file}", file=sys.stderr)
                task_run.checkpoint(
                    status="WAITING_EXTERNAL_RESULT",
                    phase="FIX_COMMAND_REQUIRED",
                    workspace_fingerprint=_workspace_fingerprint(workspace),
                    evidence_refs=[_evidence_ref(workspace, issues_file)],
                    metadata={"next_action": "provide --fix-command"},
                )
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
                return _block_and_return(
                    task_run,
                    workspace=workspace,
                    code="REPAIR_PREFLIGHT_REJECTED",
                    reason=str(exc),
                    attempted_strategies=("validate-repair-inputs",),
                    next_action="restore the immutable target, policy and baseline binding",
                    exit_code=7,
                )
            protected_before = _protected_input_fingerprints(
                policy=policy, target=target, baseline=baseline
            )
            before = quality_loop.workspace_snapshot(workspace)
            fixer_plan = task_run.plan_action(
                action_name="fix-command",
                arguments={"argv": list(args.fix_command), "issue_ids": [item["issue_id"] for item in issues]},
                state_fingerprint=str(before["fingerprint"]),
                strategies=("configured-fixer",),
                max_attempts_per_strategy=2,
            )
            if fixer_plan.decision == "BLOCKED":
                return _block_and_return(
                    task_run,
                    workspace=workspace,
                    code="FIXER_NO_PROGRESS",
                    reason="the same fixer exhausted its retry budget without a governed source change",
                    attempted_strategies=fixer_plan.attempted_strategies,
                    next_action="change the repair strategy or repair plan before resuming",
                    evidence_refs=(_evidence_ref(workspace, issues_file),),
                    exit_code=8,
                )
            task_run.checkpoint(
                status="REPAIRING",
                phase="FIXER_RUNNING",
                workspace_fingerprint=str(before["fingerprint"]),
                evidence_refs=[_evidence_ref(workspace, issues_file)],
                metadata={"cycle": cycle, "strategy": fixer_plan.strategy},
            )
            environment = build_fixer_environment(
                os.environ,
                issue_file=issues_file,
                repair_plan=current_evidence / "repair-plan.json",
                evidence_dir=current_evidence,
                target=target,
                trusted_judge_root=judge_root if judge_trust_mode == "external-readonly" else None,
            )
            fixed = subprocess.run(args.fix_command, cwd=workspace, env=environment, check=False)
            protected_after = _protected_input_fingerprints(
                policy=policy, target=target, baseline=baseline
            )
            after = quality_loop.workspace_snapshot(workspace)
            changed = before["fingerprint"] != after["fingerprint"]
            task_run.record_action_result(
                fixer_plan,
                result={
                    "returncode": fixed.returncode,
                    "before": before["fingerprint"],
                    "after": after["fingerprint"],
                    "protected_inputs_unchanged": protected_after == protected_before,
                },
                produced_new_evidence=changed,
                evidence_refs=[_evidence_ref(workspace, issues_file)],
            )
            if protected_after != protected_before:
                return _block_and_return(
                    task_run,
                    workspace=workspace,
                    code="PROTECTED_INPUT_MUTATION",
                    reason="fix command modified protected target, policy, or baseline evidence",
                    attempted_strategies=(str(fixer_plan.strategy),),
                    next_action="restore protected inputs and create a new governed target if scope changed",
                    exit_code=7,
                )
            if changed:
                task_run.mark_condition(
                    "source_changed",
                    evidence_refs=[f"workspace-snapshot:{after['fingerprint']}"],
                )
                task_run.set_metadata(
                    last_fixed_issue_signature=issue_signature,
                    fix_pending_validation=True,
                    last_fixer_returncode=fixed.returncode,
                )
            if fixed.returncode:
                task_run.checkpoint(
                    status="FAILED_RECOVERABLE",
                    phase="FIXER_FAILED",
                    workspace_fingerprint=str(after["fingerprint"]),
                    evidence_refs=[_evidence_ref(workspace, issues_file)],
                    metadata={"returncode": fixed.returncode, "source_changed": changed},
                )
                print(f"fix command failed with exit code {fixed.returncode}", file=sys.stderr)
                return fixed.returncode
            if not changed:
                task_run.checkpoint(
                    status="FAILED_RECOVERABLE",
                    phase="FIXER_NO_SOURCE_CHANGE",
                    workspace_fingerprint=str(after["fingerprint"]),
                    evidence_refs=[_evidence_ref(workspace, issues_file)],
                    metadata={"cycle": cycle},
                )
                print("fix command produced no governed source change", file=sys.stderr)
                return 4
            task_run.checkpoint(
                status="VALIDATING",
                phase="FIXER_APPLIED",
                workspace_fingerprint=str(after["fingerprint"]),
                evidence_refs=[_evidence_ref(workspace, issues_file)],
                metadata={"cycle": cycle, "issue_signature": issue_signature},
            )

        policy_payload = quality_loop._load_json(policy)
        failed_gates = _root_failure_gates(summary, policy_payload)
        targeted_failed = False
        if failed_gates and not args.skip_targeted:
            for index, gate_id in enumerate(failed_gates, start=1):
                targeted_dir = evidence_root / f"cycle-{cycle:02d}-targeted-{index:02d}-{gate_id}"
                task_run.checkpoint(
                    status="VALIDATING",
                    phase="TARGETED_VALIDATION",
                    workspace_fingerprint=_workspace_fingerprint(workspace),
                    evidence_refs=[],
                    metadata={"cycle": cycle, "gate_id": gate_id},
                )
                targeted_returncode, targeted, attempts, targeted_summary_path = _run_judge_resilient(
                    task_run=task_run,
                    workspace=workspace,
                    policy=policy,
                    target=target,
                    baseline=baseline,
                    state_dir=state_dir,
                    evidence_dir=targeted_dir,
                    mode=args.mode,
                    rerun_from=gate_id,
                    judge_root=judge_root,
                )
                if targeted is None:
                    return _block_and_return(
                        task_run,
                        workspace=workspace,
                        code="TARGETED_EVIDENCE_UNAVAILABLE",
                        reason=f"no parseable targeted evidence for gate {gate_id}",
                        attempted_strategies=attempts,
                        next_action="inspect the judge process or reproduce the gate directly",
                        exit_code=5,
                    )
                if (
                    targeted_returncode != 0
                    or targeted.get("decision") != quality_loop.PASS
                    or targeted.get("loop_status") != "TARGETED_REGRESSION_PASSED"
                ):
                    summary = targeted
                    targeted_failed = True
                    break
            if targeted_failed:
                task_run.set_metadata(
                    current_summary_path=_evidence_ref(
                        workspace,
                        Path(str(summary.get("evidence_dir") or targeted_dir)) / "run-summary.json",
                    ),
                    fix_pending_validation=False,
                    last_validation_outcome="targeted-failed",
                )
                continue
            if targeted_summary_path is None:
                raise RuntimeError("targeted validation passed without a summary path")
            task_run.mark_condition(
                "targeted_validation_resolved",
                evidence_refs=[_evidence_ref(workspace, targeted_summary_path)],
            )
        else:
            task_run.mark_condition(
                "targeted_validation_resolved",
                evidence_refs=[
                    _evidence_ref(workspace, target),
                    "targeted-validation:explicitly-skipped-or-no-root-failures",
                ],
            )

        full_dir = evidence_root / f"cycle-{cycle:02d}-full"
        task_run.checkpoint(
            status="VALIDATING",
            phase="FULL_VALIDATION",
            workspace_fingerprint=_workspace_fingerprint(workspace),
            evidence_refs=[],
            metadata={"cycle": cycle},
        )
        _returncode, verified, attempts, full_summary_path = _run_judge_resilient(
            task_run=task_run,
            workspace=workspace,
            policy=policy,
            target=target,
            baseline=baseline,
            state_dir=state_dir,
            evidence_dir=full_dir,
            mode=args.mode,
            rerun_from=None,
            judge_root=judge_root,
        )
        if verified is None:
            return _block_and_return(
                task_run,
                workspace=workspace,
                code="FULL_EVIDENCE_UNAVAILABLE",
                reason="no parseable full regression evidence was produced",
                attempted_strategies=attempts,
                next_action="inspect judge execution or reproduce the complete quality command",
                exit_code=5,
            )
        summary = verified
        if full_summary_path is None:
            raise RuntimeError("full validation returned a summary without its evidence path")
        task_run.set_metadata(
            current_summary_path=_evidence_ref(workspace, full_summary_path),
            fix_pending_validation=False,
            last_validation_outcome=str(summary.get("loop_status") or summary.get("decision") or "unknown"),
        )
        if summary.get("loop_status") == "CONVERGED":
            task_run.mark_condition(
                "full_regression_passed",
                evidence_refs=[_evidence_ref(workspace, full_summary_path)],
            )
            _write_issue_state(issues_file, [], summary=summary)
            task_run.mark_condition("issues_closed", evidence_refs=[_evidence_ref(workspace, issues_file)])
            try:
                task_run.complete(
                    workspace_fingerprint=_workspace_fingerprint(workspace),
                    evidence_refs=[_evidence_ref(workspace, full_summary_path)],
                )
            except PrematureCompletionError as exc:
                print(str(exc), file=sys.stderr)
                return 9
            return 0
        if summary.get("loop_status") in {
            "ARCHITECTURE_REPLAN_REQUIRED",
            "STOPPED_MAX_REPAIRS",
        }:
            return _block_and_return(
                task_run,
                workspace=workspace,
                code=str(summary.get("loop_status")),
                reason="the governed repair contract requires replan or exhausted its repair budget",
                attempted_strategies=("full-quality-judge",),
                next_action="create a new governed repair plan and baseline",
                evidence_refs=(_evidence_ref(workspace, full_summary_path),),
                exit_code=6,
            )
        if summary.get("loop_status") == "REPAIR_REQUIRED":
            _advance_round(target)
            task_run.checkpoint(
                status="FAILED_RECOVERABLE",
                phase="NEXT_REPAIR_ROUND_REQUIRED",
                workspace_fingerprint=_workspace_fingerprint(workspace),
                evidence_refs=[_evidence_ref(workspace, full_summary_path)],
                metadata={"next_cycle": cycle + 1},
            )
            continue
        if summary.get("decision") == quality_loop.BLOCKED:
            return _block_and_return(
                task_run,
                workspace=workspace,
                code="ENVIRONMENT_BLOCKED",
                reason="full quality verification is blocked by the declared environment",
                attempted_strategies=attempts,
                next_action="provision the environment and resume the same task-run",
                evidence_refs=(_evidence_ref(workspace, full_summary_path),),
                exit_code=2,
            )
        return _block_and_return(
            task_run,
            workspace=workspace,
            code="UNCLASSIFIED_VERIFICATION_FAILURE",
            reason="full quality verification failed without a recognized recoverable loop status",
            attempted_strategies=attempts,
            next_action="inspect the full run-summary and update the repair plan",
            evidence_refs=(_evidence_ref(workspace, full_summary_path),),
            exit_code=1,
        )

    return _block_and_return(
        task_run,
        workspace=workspace,
        code="ORCHESTRATOR_MAX_CYCLES",
        reason="repair orchestrator reached its cycle budget without completion evidence",
        attempted_strategies=("bounded-repair-loop",),
        next_action="replan the repair; max cycles are never success",
        evidence_refs=(_evidence_ref(workspace, issues_file),),
        exit_code=6,
    )


if __name__ == "__main__":
    raise SystemExit(main())
