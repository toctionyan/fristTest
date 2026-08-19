#!/usr/bin/env python3
"""Trusted Stage-2 scope compiler and mandatory read-only RCA authority.

Stage-1 evidence may mention product source, tests/oracles, or control-plane
implementation files. This wrapper first narrows evidence to the source PR's
changed paths, then compiles a separate writable repair scope using the same
canonical domain-aware path guard enforced by the existing Stage-2 fixer.
Evidence can remain visible without granting write authority to tests/oracles,
workflows, governance, dependencies, secrets, or unrelated control-plane files.

Before *any* seed patch or repair edit is applied, the wrapper runs a mandatory
read-only RCA against the clean candidate checkout. A deterministic write grant
is compiled only from that immutable RCA and is bound to the exact failure case,
repair domain, head SHA, failure signature, and narrowed path set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import github_repair_orchestrator as base
from github_repair_authority import (
    RepairAuthorityError,
    compile_write_grant,
)
from github_repair_domain_adapter import repair_domain_runtime
from github_repair_rca import RCAError, run_read_only_rca
from governed_repair_path_policy import (
    REPAIR_DOMAIN_CONTROL_PLANE,
    REPAIR_DOMAIN_PRODUCT,
)


class ScopeNormalizationError(RuntimeError):
    """Fail-closed Stage-2 scope normalization error."""


_TEST_PARTS = {"test", "tests", "e2e", "__tests__"}
_LOOP_FEEDBACK_SCHEMA = "github-governed-repair-feedback@1"


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScopeNormalizationError(f"invalid JSON input: {path}") from exc
    if not isinstance(payload, dict):
        raise ScopeNormalizationError(f"JSON object required: {path}")
    return payload


def _write_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _normalize_path(value: object) -> str:
    value = str(value or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _candidate_paths_digest(paths: list[str]) -> str:
    canonical = json.dumps(
        paths,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _expected_loop_failure_class(report: dict[str, Any]) -> str:
    domain = str(report.get("repair_domain") or "").strip()
    if domain == REPAIR_DOMAIN_PRODUCT:
        return "PRODUCT_SOURCE_FAILURE"
    if domain == REPAIR_DOMAIN_CONTROL_PLANE:
        return "CONTROL_PLANE_IMPLEMENTATION_FAILURE"
    raise ScopeNormalizationError(f"unsupported outer-loop repair domain: {domain!r}")


def _validate_outer_loop_scope_binding(
    report: dict[str, Any],
    candidates: list[str],
) -> None:
    feedback = report.get("loop_feedback")
    if feedback is None:
        return
    if not isinstance(feedback, dict) or feedback.get("schema") != _LOOP_FEEDBACK_SCHEMA:
        raise ScopeNormalizationError("invalid outer-loop feedback contract")
    expected_failure_class = _expected_loop_failure_class(report)
    if feedback.get("failure_class") != expected_failure_class:
        raise ScopeNormalizationError(
            "outer-loop failure class drifted from the immutable repair domain"
        )
    if feedback.get("repair_domain") != report.get("repair_domain"):
        raise ScopeNormalizationError("outer-loop repair domain changed between rounds")
    if feedback.get("scope_expanded") is not False:
        raise ScopeNormalizationError("outer-loop feedback asserted scope expansion")

    raw_candidates = report.get("candidate_paths")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ScopeNormalizationError("outer-loop feedback has no candidate scope")
    raw_normalized = [_normalize_path(item) for item in raw_candidates]
    if (
        any(not path for path in raw_normalized)
        or len(set(raw_normalized)) != len(raw_normalized)
        or raw_normalized != candidates
    ):
        raise ScopeNormalizationError("outer-loop candidate scope is invalid")

    try:
        repair_round = int(feedback.get("repair_round") or 0)
        next_repair_round = int(feedback.get("next_repair_round") or 0)
    except (TypeError, ValueError) as exc:
        raise ScopeNormalizationError("outer-loop repair round binding is invalid") from exc
    if repair_round < 1 or next_repair_round != repair_round + 1:
        raise ScopeNormalizationError("outer-loop repair round binding is invalid")

    expected_digest = str(feedback.get("candidate_paths_sha256") or "")
    if len(expected_digest) != 64 or any(
        char not in "0123456789abcdef" for char in expected_digest
    ):
        raise ScopeNormalizationError("outer-loop candidate scope digest is missing or invalid")
    if _candidate_paths_digest(candidates) != expected_digest:
        raise ScopeNormalizationError("outer-loop candidate scope drifted from routed repair paths")


def _looks_like_test_oracle(path: str) -> bool:
    parts = {part.casefold() for part in Path(path).parts}
    name = Path(path).name.casefold()
    return bool(
        parts & _TEST_PARTS
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
    )


def _assert_clean_candidate(workspace: Path) -> None:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if completed.returncode:
        raise ScopeNormalizationError(
            (completed.stderr or completed.stdout or "git status failed")[-4000:]
        )
    if completed.stdout.strip():
        raise ScopeNormalizationError(
            "candidate workspace must be clean before read-only RCA and write-grant compilation"
        )


def normalize_failure_case(report: dict[str, Any]) -> dict[str, Any]:
    """Intersect evidence-derived candidates with source changes, without adding scope."""
    if report.get("schema") != "github-failure-ingest@1":
        raise ScopeNormalizationError("unsupported Stage-1 failure-case schema")

    candidates: list[str] = []
    for item in report.get("candidate_paths") or []:
        path = _normalize_path(item)
        if path and path not in candidates:
            candidates.append(path)

    _validate_outer_loop_scope_binding(report, candidates)

    changed: list[str] = []
    for item in report.get("source_changed_files") or []:
        path = _normalize_path(item)
        if path and path not in changed:
            changed.append(path)

    scoped = [path for path in candidates if path in set(changed)] if changed else candidates
    normalized = dict(report)
    normalized["candidate_paths"] = scoped
    normalized["stage2_scope_normalization"] = {
        "schema": "stage2-scope-normalization@4",
        "repair_domain": normalized.get("repair_domain"),
        "evidence_candidates": candidates,
        "source_changed_files": changed,
        "evidence_paths": scoped,
        "writable_paths": [],
        "protected_oracle_paths": [],
        "excluded_paths": [],
        "repair_scope_status": "UNCOMPILED",
        "scope_expanded": False,
        "rca_required": True,
        "write_grant_required": True,
    }
    return normalized


def compile_repair_scope(report: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """Compile maximum possible write candidates using the active domain path guard.

    This result is *not* write authority. The read-only RCA may only narrow this
    tuple, and ``github_repair_authority`` must then compile the exact grant.
    """
    normalized = normalize_failure_case(report)
    scope = dict(normalized["stage2_scope_normalization"])
    evidence_paths = list(scope["evidence_paths"])
    writable: list[str] = []
    protected_oracles: list[str] = []
    excluded: list[dict[str, str]] = []

    for path in evidence_paths:
        try:
            allowed = base.validate_allowed_paths(workspace, [path])
        except base.FixerError as exc:
            if _looks_like_test_oracle(path):
                protected_oracles.append(path)
            excluded.append({"path": path, "reason": str(exc)[:1000]})
            continue
        if allowed != (path,):
            raise ScopeNormalizationError(
                f"unexpected Stage-2 path-guard normalization for {path!r}: {allowed!r}"
            )
        writable.append(path)

    if writable and protected_oracles:
        status = "RCA_REQUIRED_WITH_PROTECTED_ORACLES"
    elif writable:
        status = "RCA_REQUIRED"
    elif protected_oracles:
        status = "TEST_CONTRACT_REVIEW_REQUIRED"
    else:
        status = "NO_REPAIRABLE_SOURCE"

    normalized["candidate_paths"] = writable
    scope.update(
        {
            "writable_paths": writable,
            "protected_oracle_paths": protected_oracles,
            "excluded_paths": excluded,
            "repair_scope_status": status,
            "scope_expanded": False,
        }
    )
    normalized["stage2_scope_normalization"] = scope
    return normalized


def run(
    *,
    workspace: Path,
    failure_case_path: Path,
    task_run_path: Path,
    evidence_root: Path,
    max_cycles: int,
    seed_patch_path: Path | None = None,
    repair_round: int = 1,
    max_repair_rounds: int = base.MAX_REPAIR_ROUNDS,
) -> int:
    workspace = workspace.resolve()
    evidence_root = evidence_root.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)

    _assert_clean_candidate(workspace)
    raw_report = _load_object(failure_case_path)
    with repair_domain_runtime(raw_report) as domain:
        report = compile_repair_scope(raw_report, workspace=workspace)
        if report.get("repair_domain") != domain:
            raise ScopeNormalizationError("runtime repair domain differs from failure-case domain")
        normalized_path = evidence_root / "normalized-failure-case.json"
        _write_object(normalized_path, report)

        candidate_paths = tuple(report.get("candidate_paths") or ())
        if not candidate_paths:
            raise ScopeNormalizationError(
                "no candidate remains inside the immutable repair domain; write authority denied"
            )

        rca = run_read_only_rca(
            workspace=workspace,
            failure_case=report,
            candidate_paths=candidate_paths,
            repair_round=repair_round,
        )
        if rca.get("repair_domain") is None:
            rca["repair_domain"] = domain
            rca["rca_sha256"] = __import__("github_repair_authority").rca_fingerprint(rca)
        rca_path = evidence_root / "rca.json"
        _write_object(rca_path, rca)

        grant = compile_write_grant(
            failure_case=report,
            rca=rca,
            candidate_paths=candidate_paths,
        )
        grant_path = evidence_root / "write-grant.json"
        _write_object(grant_path, grant)

        report["stage2_scope_normalization"]["granted_paths"] = list(
            grant["allowed_paths"]
        )
        report["stage2_scope_normalization"]["repair_scope_status"] = "WRITE_GRANTED"
        report["governed_repair_state"] = "WRITE_GRANTED"
        _write_object(normalized_path, report)

        return base.run_stage2(
            workspace=workspace,
            failure_case_path=normalized_path,
            task_run_path=task_run_path,
            evidence_root=evidence_root,
            max_cycles=max_cycles,
            seed_patch_path=seed_patch_path,
            repair_round_number=repair_round,
            max_repair_rounds=max_repair_rounds,
            rca_path=rca_path,
            write_grant_path=grant_path,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--failure-case", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--max-cycles", type=int, default=8)
    parser.add_argument("--seed-patch")
    parser.add_argument("--repair-round", type=int, default=1)
    parser.add_argument("--max-repair-rounds", type=int, default=base.MAX_REPAIR_ROUNDS)
    args = parser.parse_args()
    if args.max_cycles < 1 or args.max_cycles > base.MAX_CYCLES:
        parser.error(f"--max-cycles must be between 1 and {base.MAX_CYCLES}")
    if args.max_repair_rounds < 1 or args.max_repair_rounds > base.MAX_REPAIR_ROUNDS:
        parser.error(
            f"--max-repair-rounds must be between 1 and {base.MAX_REPAIR_ROUNDS}"
        )
    if args.repair_round < 1 or args.repair_round > args.max_repair_rounds:
        parser.error("--repair-round must be within --max-repair-rounds")
    try:
        return run(
            workspace=Path(args.workspace),
            failure_case_path=Path(args.failure_case).resolve(),
            task_run_path=Path(args.task_run).resolve(),
            evidence_root=Path(args.evidence_root),
            max_cycles=args.max_cycles,
            seed_patch_path=Path(args.seed_patch).resolve() if args.seed_patch else None,
            repair_round=args.repair_round,
            max_repair_rounds=args.max_repair_rounds,
        )
    except (OSError, ScopeNormalizationError, RCAError, RepairAuthorityError, base.FixerError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "state": "RCA_READ_ONLY",
                    "write_authority": False,
                    "error": str(exc),
                    "production_closed": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
