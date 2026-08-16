#!/usr/bin/env python3
"""Bounded Stage-2 repair controller with mandatory RCA/write-grant authority.

Stage 2 may create a local repair candidate and evidence artifact. It never pushes a
branch, opens a pull request, merges protected refs, refreshes protected baselines,
or claims full validation/production closure.

Every write is downstream of an immutable read-only RCA and an exact deterministic
write grant. The grant is revalidated before the seed patch and before every model
repair cycle. Repeating the same deterministic failure twice revokes write authority
and requires architecture re-plan plus a new RCA rather than blind retries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPTS = Path(__file__).resolve().parent
for entry in (str(CONTROL), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from task_run import TaskRunStore  # type: ignore  # noqa: E402
from github_agent_fixer import (  # noqa: E402
    FixerError,
    ModelConfig,
    fingerprint,
    repair_round,
    validate_allowed_paths,
)
from github_repair_authority import (  # noqa: E402
    RepairAuthorityError,
    rca_fingerprint,
    revoke_write_grant,
    validate_write_grant,
    write_grant_fingerprint,
)

MAX_CYCLES = 8
MAX_REPAIR_ROUNDS = 8
MAX_SEED_PATCH_BYTES = 2_000_000


class OrchestratorError(RuntimeError):
    """Fail-closed Stage-2 orchestration error."""


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise OrchestratorError(f"JSON object required: {path}")
    return payload


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )


def _git(workspace: Path, *args: str) -> str:
    completed = _run(["git", *args], workspace)
    if completed.returncode:
        raise OrchestratorError(
            (completed.stderr or completed.stdout or "git command failed").strip()
        )
    return completed.stdout.strip()


def _changed_paths(workspace: Path) -> tuple[str, ...]:
    completed = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        workspace,
    )
    if completed.returncode:
        raise OrchestratorError(
            (completed.stderr or completed.stdout or "git status failed").strip()
        )
    result: list[str] = []
    for row in completed.stdout.splitlines():
        raw = row[3:] if len(row) > 3 else ""
        path = raw.split(" -> ")[-1].strip().replace("\\", "/")
        if path and path not in result:
            result.append(path)
    return tuple(result)


def _workspace_diff_fingerprint(workspace: Path) -> str:
    patch = _git(workspace, "diff", "--no-ext-diff", "--binary")
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()


def _validate_failure_case(report: dict[str, Any], workspace: Path) -> tuple[str, ...]:
    if report.get("schema") != "github-failure-ingest@1" or report.get("status") != "INGESTED":
        raise OrchestratorError("unsupported or incomplete failure-case evidence")
    if report.get("repair_allowed") is not True:
        raise OrchestratorError("Stage 1 did not authorize a repair candidate")
    if report.get("classification") != "code_or_contract":
        raise OrchestratorError("only code_or_contract failures are repairable")
    if report.get("same_repository") is not True:
        raise OrchestratorError("fork or foreign-repository failures are not repairable")
    expected_sha = str(report.get("head_sha") or "")
    actual_sha = _git(workspace, "rev-parse", "HEAD")
    if not expected_sha or expected_sha != actual_sha:
        raise OrchestratorError("candidate checkout does not match the ingested commit SHA")
    initial = _changed_paths(workspace)
    if initial:
        raise OrchestratorError(f"candidate workspace must start clean: {list(initial)}")
    paths = tuple(str(item) for item in report.get("candidate_paths") or [])
    if not paths:
        raise OrchestratorError("failure evidence does not identify a repair path")
    return validate_allowed_paths(workspace, paths)


def _validate_task_binding(task: TaskRunStore, report: dict[str, Any]) -> None:
    binding = task.payload.get("binding") if isinstance(task.payload.get("binding"), dict) else {}
    expected = {
        "repository": report.get("repository"),
        "workflow_name": report.get("workflow_name"),
        "workflow_run_id": str(report.get("workflow_run_id")),
        "workflow_run_attempt": str(report.get("workflow_run_attempt")),
        "head_sha": report.get("head_sha"),
        "failure_signature": report.get("failure_signature"),
    }
    mismatched = [
        key
        for key, value in expected.items()
        if str(binding.get(key)) != str(value)
    ]
    if mismatched:
        raise OrchestratorError(f"Stage-1 TaskRun binding mismatch: {mismatched}")


def _open_task(path: Path) -> TaskRunStore:
    return TaskRunStore(path.resolve(), _load_object(path))


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _block(
    task: TaskRunStore,
    *,
    workspace: Path,
    code: str,
    reason: str,
    evidence_refs: list[str],
    result_path: Path,
    cycles: list[dict[str, Any]],
    repair_round_number: int | None = None,
    governed_state: str = "BLOCKED",
    next_action: str = "inspect Stage-2 evidence; do not publish this candidate",
    authority_evidence: dict[str, Any] | None = None,
) -> int:
    result = {
        "schema": "github-governed-repair-stage2@1",
        "status": "BLOCKED",
        "code": code,
        "reason": reason,
        "governed_repair_state": governed_state,
        "write_authority": False,
        "cycles": cycles,
        "changed_paths": list(_changed_paths(workspace)),
        "repair_round": repair_round_number,
        "max_repair_rounds": MAX_REPAIR_ROUNDS,
        "full_validation_passed": False,
        "draft_pr_published": False,
        "production_closed": False,
    }
    if authority_evidence:
        result["authority_evidence"] = authority_evidence
    _write_result(result_path, result)
    task.block(
        code=code,
        reason=reason,
        attempted_strategies=(
            "read-only-root-cause-analysis",
            "exact-write-grant",
            "restricted-model-fixer",
            "deterministic-file-verifier",
        ),
        next_action=next_action,
        workspace_fingerprint=_workspace_diff_fingerprint(workspace),
        evidence_refs=[*evidence_refs, str(result_path)],
    )
    return 2


def _canonical_diagnostics(row: dict[str, Any]) -> str:
    return json.dumps(
        {"summary": row.get("summary"), "verification": row.get("verification")},
        ensure_ascii=False,
        sort_keys=True,
    )[:16_000]


def _apply_seed_patch(
    *,
    workspace: Path,
    seed_patch_path: Path | None,
    allowed_paths: tuple[str, ...],
) -> tuple[bool, str | None]:
    if seed_patch_path is None:
        return False, None
    if not seed_patch_path.is_file() or seed_patch_path.is_symlink():
        raise OrchestratorError("outer-loop seed patch must be an existing regular file")
    data = seed_patch_path.read_bytes()
    if not data or len(data) > MAX_SEED_PATCH_BYTES or b"\x00" in data:
        raise OrchestratorError("outer-loop seed patch is empty, oversized, or binary")
    digest = hashlib.sha256(data).hexdigest()
    checked = _run(
        ["git", "apply", "--check", "--whitespace=error-all", str(seed_patch_path.resolve())],
        workspace,
    )
    if checked.returncode:
        raise OrchestratorError(
            (checked.stderr or checked.stdout or "seed git apply --check failed").strip()
        )
    applied = _run(
        ["git", "apply", "--whitespace=error-all", str(seed_patch_path.resolve())],
        workspace,
    )
    if applied.returncode:
        raise OrchestratorError(
            (applied.stderr or applied.stdout or "seed git apply failed").strip()
        )
    current_paths = _changed_paths(workspace)
    unexpected = sorted(set(current_paths) - set(allowed_paths))
    if unexpected:
        raise OrchestratorError(
            f"outer-loop seed patch expands immutable repair authority: {unexpected}"
        )
    if not current_paths:
        raise OrchestratorError("outer-loop seed patch produced no governed source diff")
    return True, digest


def _update_repair_loop_metadata(
    task: TaskRunStore,
    *,
    repair_round_number: int,
    max_repair_rounds: int,
) -> dict[str, Any]:
    metadata = task.payload.get("metadata") if isinstance(task.payload.get("metadata"), dict) else {}
    existing = metadata.get("repair_loop") if isinstance(metadata.get("repair_loop"), dict) else {}
    prior_round = int(existing.get("repair_round") or 0)
    if prior_round > repair_round_number:
        raise OrchestratorError(
            f"outer-loop repair round moved backwards: prior={prior_round} requested={repair_round_number}"
        )
    updated = dict(existing)
    updated.update(
        {
            "schema": "github-governed-repair-loop@1",
            "repair_round": repair_round_number,
            "max_repair_rounds": max_repair_rounds,
            "phase": "STAGE2_REPAIRING",
            "governed_repair_state": "WRITE_GRANTED",
            "production_closed": False,
        }
    )
    task.set_metadata(repair_loop=updated)
    return updated


def run_stage2(
    *,
    workspace: Path,
    failure_case_path: Path,
    task_run_path: Path,
    evidence_root: Path,
    max_cycles: int,
    rca_path: Path,
    write_grant_path: Path,
    config: ModelConfig | None = None,
    seed_patch_path: Path | None = None,
    repair_round_number: int = 1,
    max_repair_rounds: int = MAX_REPAIR_ROUNDS,
) -> int:
    workspace = workspace.resolve()
    evidence_root = evidence_root.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    result_path = evidence_root / "repair-result.json"
    patch_path = evidence_root / "repair.patch"
    report = _load_object(failure_case_path)
    task = _open_task(task_run_path)
    cycles: list[dict[str, Any]] = []
    rca: dict[str, Any] = {}
    grant: dict[str, Any] = {}

    if repair_round_number < 1 or repair_round_number > max_repair_rounds:
        return _block(
            task,
            workspace=workspace,
            code="STAGE2_REPAIR_ROUND_REJECTED",
            reason=(
                f"repair_round must be between 1 and {max_repair_rounds}; "
                f"got {repair_round_number}"
            ),
            evidence_refs=[str(failure_case_path), str(task_run_path)],
            result_path=result_path,
            cycles=cycles,
            repair_round_number=repair_round_number,
            governed_state="RCA_READ_ONLY",
            next_action="re-plan the governed repair round before requesting write authority",
        )

    try:
        _validate_task_binding(task, report)
        candidate_paths = _validate_failure_case(report, workspace)
        rca = _load_object(rca_path)
        grant = _load_object(write_grant_path)
        allowed_paths = validate_write_grant(
            grant,
            failure_case=report,
            rca=rca,
            candidate_paths=candidate_paths,
        )
        if validate_allowed_paths(workspace, allowed_paths) != allowed_paths:
            raise OrchestratorError("write grant does not match fixer path authority")
        seed_applied, seed_patch_sha256 = _apply_seed_patch(
            workspace=workspace,
            seed_patch_path=seed_patch_path,
            allowed_paths=allowed_paths,
        )
        loop_metadata = _update_repair_loop_metadata(
            task,
            repair_round_number=repair_round_number,
            max_repair_rounds=max_repair_rounds,
        )
        config = config or ModelConfig.from_environment()
    except (
        OSError,
        json.JSONDecodeError,
        OrchestratorError,
        FixerError,
        RepairAuthorityError,
    ) as exc:
        return _block(
            task,
            workspace=workspace,
            code="STAGE2_PREFLIGHT_REJECTED",
            reason=str(exc),
            evidence_refs=[
                str(failure_case_path),
                str(task_run_path),
                str(rca_path),
                str(write_grant_path),
            ],
            result_path=result_path,
            cycles=cycles,
            repair_round_number=repair_round_number,
            governed_state="RCA_READ_ONLY",
            next_action="correct RCA/write-grant binding; do not patch source",
        )

    rca_sha = rca_fingerprint(rca)
    grant_sha = write_grant_fingerprint(grant)
    authority_evidence = {
        "rca_sha256": rca_sha,
        "write_grant_sha256": grant_sha,
        "violated_invariant": rca.get("violated_invariant"),
        "authority_owner": rca.get("authority_owner"),
        "required_permanent_guard": rca.get("required_permanent_guard"),
        "allowed_paths": list(allowed_paths),
    }

    task.checkpoint(
        status="RUNNING",
        phase="STAGE2_WRITE_GRANTED",
        workspace_fingerprint=_workspace_diff_fingerprint(workspace),
        evidence_refs=[
            str(failure_case_path),
            str(rca_path),
            str(write_grant_path),
            *([str(seed_patch_path)] if seed_patch_path else []),
        ],
        metadata={
            "stage": 2,
            "governed_repair_state": "WRITE_GRANTED",
            "max_cycles": max_cycles,
            "candidate_paths": list(candidate_paths),
            "allowed_paths": list(allowed_paths),
            "provider": config.provider,
            "model": config.model,
            "repair_round": repair_round_number,
            "max_repair_rounds": max_repair_rounds,
            "seed_patch_applied": seed_applied,
            "seed_patch_sha256": seed_patch_sha256,
            "rca_sha256": rca_sha,
            "write_grant_sha256": grant_sha,
            "repair_loop": loop_metadata,
        },
    )

    diagnostics = str(report.get("failure_summary") or "")
    previous_verifier_signature = ""
    repeated_verifier_count = 0
    before_fingerprint = _workspace_diff_fingerprint(workspace)

    for cycle in range(1, min(max_cycles, MAX_CYCLES) + 1):
        task.checkpoint(
            status="REPAIRING",
            phase="STAGE2_PATCHING",
            workspace_fingerprint=_workspace_diff_fingerprint(workspace),
            evidence_refs=[str(rca_path), str(write_grant_path)],
            metadata={
                "cycle": cycle,
                "repair_round": repair_round_number,
                "governed_repair_state": "PATCHING",
                "write_grant_sha256": grant_sha,
            },
        )
        try:
            current_grant_paths = validate_write_grant(
                grant,
                failure_case=report,
                rca=rca,
                candidate_paths=candidate_paths,
            )
            if current_grant_paths != allowed_paths:
                raise OrchestratorError("write authority changed during Stage-2 execution")
            row = repair_round(
                workspace=workspace,
                failure_case=report,
                allowed_paths=allowed_paths,
                diagnostics=diagnostics,
                cycle=cycle,
                config=config,
                rca=rca,
                write_grant=grant,
            )
        except (
            FixerError,
            OSError,
            subprocess.SubprocessError,
            RepairAuthorityError,
            OrchestratorError,
        ) as exc:
            return _block(
                task,
                workspace=workspace,
                code="STAGE2_FIXER_FAILED",
                reason=str(exc),
                evidence_refs=[str(rca_path), str(write_grant_path)],
                result_path=result_path,
                cycles=cycles,
                repair_round_number=repair_round_number,
                governed_state="RCA_READ_ONLY",
                next_action="re-enter read-only RCA; do not broaden the write grant",
                authority_evidence=authority_evidence,
            )

        current_paths = _changed_paths(workspace)
        unexpected = sorted(set(current_paths) - set(allowed_paths))
        if unexpected:
            return _block(
                task,
                workspace=workspace,
                code="STAGE2_SCOPE_VIOLATION",
                reason=f"repair changed paths outside exact write grant: {unexpected}",
                evidence_refs=[str(rca_path), str(write_grant_path)],
                result_path=result_path,
                cycles=[*cycles, row],
                repair_round_number=repair_round_number,
                governed_state="RCA_READ_ONLY",
                next_action="revoke write and investigate authority violation",
                authority_evidence=authority_evidence,
            )
        current_fingerprint = _workspace_diff_fingerprint(workspace)
        if current_fingerprint == before_fingerprint:
            return _block(
                task,
                workspace=workspace,
                code="STAGE2_NO_PROGRESS",
                reason="repair cycle produced no new governed source diff beyond the current round seed",
                evidence_refs=[str(rca_path), str(write_grant_path)],
                result_path=result_path,
                cycles=[*cycles, row],
                repair_round_number=repair_round_number,
                governed_state="RCA_READ_ONLY",
                next_action="re-plan root cause before another write attempt",
                authority_evidence=authority_evidence,
            )
        before_fingerprint = current_fingerprint
        row = dict(row)
        row["repair_round"] = repair_round_number
        row["rca_sha256"] = rca_sha
        row["write_grant_sha256"] = grant_sha
        cycles.append(row)
        cycle_file = evidence_root / f"cycle-{cycle:02d}.json"
        _write_result(cycle_file, row)
        task.checkpoint(
            status="VALIDATING",
            phase="STAGE2_LOCAL_VERIFICATION",
            workspace_fingerprint=current_fingerprint,
            evidence_refs=[str(cycle_file), str(write_grant_path)],
            metadata={
                "cycle": cycle,
                "repair_round": repair_round_number,
                "governed_repair_state": "LOCAL_VERIFICATION",
                "verification_passed": row.get("verification_passed") is True,
            },
        )

        if row.get("verification_passed") is True:
            patch = _git(workspace, "diff", "--no-ext-diff", "--binary")
            if not patch.strip():
                return _block(
                    task,
                    workspace=workspace,
                    code="STAGE2_EMPTY_PATCH",
                    reason="verification passed without a publishable source diff",
                    evidence_refs=[str(cycle_file), str(write_grant_path)],
                    result_path=result_path,
                    cycles=cycles,
                    repair_round_number=repair_round_number,
                    governed_state="RCA_READ_ONLY",
                    authority_evidence=authority_evidence,
                )
            patch_path.write_text(
                patch + ("\n" if not patch.endswith("\n") else ""),
                encoding="utf-8",
            )
            task.mark_condition(
                "source_changed",
                evidence_refs=[str(patch_path), f"repair-diff:{current_fingerprint}"],
            )
            task.checkpoint(
                status="WAITING_EXTERNAL_RESULT",
                phase="STAGE3_VALIDATION_REQUIRED",
                workspace_fingerprint=current_fingerprint,
                evidence_refs=[
                    str(patch_path),
                    str(cycle_file),
                    str(rca_path),
                    str(write_grant_path),
                ],
                metadata={
                    "stage2_status": "REPAIR_CANDIDATE_READY",
                    "governed_repair_state": "INDEPENDENT_REVIEW",
                    "changed_paths": list(current_paths),
                    "next_action": "run independent targeted and full regression validation",
                    "repair_round": repair_round_number,
                    "max_repair_rounds": max_repair_rounds,
                    **authority_evidence,
                },
            )
            result = {
                "schema": "github-governed-repair-stage2@1",
                "status": "REPAIR_CANDIDATE_READY",
                "workflow_run_id": report.get("workflow_run_id"),
                "head_sha": report.get("head_sha"),
                "failure_signature": report.get("failure_signature"),
                "repair_round": repair_round_number,
                "max_repair_rounds": max_repair_rounds,
                "seed_patch_applied": seed_applied,
                "seed_patch_sha256": seed_patch_sha256,
                "cycles": cycles,
                "changed_paths": list(current_paths),
                "write_scope": list(allowed_paths),
                "patch": str(patch_path),
                "rca_sha256": rca_sha,
                "write_grant_sha256": grant_sha,
                "violated_invariant": rca.get("violated_invariant"),
                "authority_owner": rca.get("authority_owner"),
                "drifted_projection": rca.get("drifted_projection"),
                "required_permanent_guard": rca.get("required_permanent_guard"),
                "governed_repair_state": "INDEPENDENT_REVIEW",
                "gates": grant.get("gates"),
                "deterministic_file_verification_passed": True,
                "full_validation_passed": False,
                "draft_pr_published": False,
                "production_closed": False,
            }
            _write_result(result_path, result)
            return 0

        verifier_signature = fingerprint(row.get("verification") or [])
        if verifier_signature == previous_verifier_signature:
            repeated_verifier_count += 1
        else:
            repeated_verifier_count = 1
            previous_verifier_signature = verifier_signature
        if repeated_verifier_count >= 2:
            revocation = revoke_write_grant(
                grant,
                reason="same_deterministic_failure_signature_twice",
                failure_signature=verifier_signature,
            )
            revocation_path = evidence_root / "write-revocation.json"
            _write_result(revocation_path, revocation)
            return _block(
                task,
                workspace=workspace,
                code="STAGE2_WRITE_REVOKED_REPLAN_REQUIRED",
                reason=(
                    "the same deterministic verification failure repeated twice; "
                    "write authority is revoked until architecture re-plan and a new read-only RCA"
                ),
                evidence_refs=[str(cycle_file), str(revocation_path)],
                result_path=result_path,
                cycles=cycles,
                repair_round_number=repair_round_number,
                governed_state="RCA_READ_ONLY",
                next_action="ARCHITECTURE_REPLAN_AND_NEW_RCA",
                authority_evidence={**authority_evidence, "revocation": revocation},
            )
        diagnostics = _canonical_diagnostics(row)

    revocation = revoke_write_grant(
        grant,
        reason="stage2_cycle_budget_exhausted",
        failure_signature=str(report.get("failure_signature") or ""),
    )
    revocation_path = evidence_root / "write-revocation.json"
    _write_result(revocation_path, revocation)
    return _block(
        task,
        workspace=workspace,
        code="STAGE2_CYCLE_BUDGET_EXHAUSTED",
        reason=(
            "repair did not reach a deterministic candidate within "
            f"{min(max_cycles, MAX_CYCLES)} fixer cycles"
        ),
        evidence_refs=[str(failure_case_path), str(revocation_path)],
        result_path=result_path,
        cycles=cycles,
        repair_round_number=repair_round_number,
        governed_state="RCA_READ_ONLY",
        next_action="ARCHITECTURE_REPLAN_AND_NEW_RCA",
        authority_evidence={**authority_evidence, "revocation": revocation},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--failure-case", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--rca", required=True)
    parser.add_argument("--write-grant", required=True)
    parser.add_argument("--max-cycles", type=int, default=8)
    parser.add_argument("--seed-patch")
    parser.add_argument("--repair-round", type=int, default=1)
    parser.add_argument("--max-repair-rounds", type=int, default=MAX_REPAIR_ROUNDS)
    args = parser.parse_args()
    if args.max_cycles < 1 or args.max_cycles > MAX_CYCLES:
        parser.error(f"--max-cycles must be between 1 and {MAX_CYCLES}")
    if args.max_repair_rounds < 1 or args.max_repair_rounds > MAX_REPAIR_ROUNDS:
        parser.error(f"--max-repair-rounds must be between 1 and {MAX_REPAIR_ROUNDS}")
    if args.repair_round < 1 or args.repair_round > args.max_repair_rounds:
        parser.error("--repair-round must be within --max-repair-rounds")
    return run_stage2(
        workspace=Path(args.workspace),
        failure_case_path=Path(args.failure_case).resolve(),
        task_run_path=Path(args.task_run).resolve(),
        evidence_root=Path(args.evidence_root),
        max_cycles=args.max_cycles,
        rca_path=Path(args.rca).resolve(),
        write_grant_path=Path(args.write_grant).resolve(),
        seed_patch_path=Path(args.seed_patch).resolve() if args.seed_patch else None,
        repair_round_number=args.repair_round,
        max_repair_rounds=args.max_repair_rounds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
