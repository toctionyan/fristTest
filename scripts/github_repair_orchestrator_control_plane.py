#!/usr/bin/env python3
"""Trusted Stage-2 scope compiler for governed repair.

Stage-1 evidence may mention product source, tests/oracles, or unrelated verifier
files. This wrapper first narrows evidence to the source PR's changed paths, then
compiles a separate writable repair scope using the same path guard enforced by
the Stage-2 fixer. Evidence can therefore remain visible to governance without
granting write authority to tests, CI, governance, dependency manifests, or other
protected files. Changed-file metadata and scope compilation can only remove
repair authority, never add it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import github_repair_orchestrator as base


class ScopeNormalizationError(RuntimeError):
    """Fail-closed Stage-2 scope normalization error."""


_TEST_PARTS = {"test", "tests", "e2e", "__tests__"}


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScopeNormalizationError(f"invalid JSON input: {path}") from exc
    if not isinstance(payload, dict):
        raise ScopeNormalizationError(f"JSON object required: {path}")
    return payload


def _normalize_path(value: object) -> str:
    value = str(value or "").strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _looks_like_test_oracle(path: str) -> bool:
    parts = {part.casefold() for part in Path(path).parts}
    name = Path(path).name.casefold()
    return bool(
        parts & _TEST_PARTS
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
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

    changed: list[str] = []
    for item in report.get("source_changed_files") or []:
        path = _normalize_path(item)
        if path and path not in changed:
            changed.append(path)

    scoped = [path for path in candidates if path in set(changed)] if changed else candidates
    normalized = dict(report)
    normalized["candidate_paths"] = scoped
    normalized["stage2_scope_normalization"] = {
        "schema": "stage2-scope-normalization@2",
        "evidence_candidates": candidates,
        "source_changed_files": changed,
        "evidence_paths": scoped,
        "writable_paths": [],
        "protected_oracle_paths": [],
        "excluded_paths": [],
        "repair_scope_status": "UNCOMPILED",
        "scope_expanded": False,
    }
    return normalized


def compile_repair_scope(report: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """Compile write authority from normalized evidence using the fixer path guard.

    The authoritative write decision is delegated to ``base.validate_allowed_paths``.
    This wrapper does not duplicate or weaken that policy. Non-writable paths remain
    recorded as evidence; test-shaped paths are additionally marked as protected
    oracles so a repair actor cannot mutate its own judge.
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
        status = "REPAIRABLE_WITH_PROTECTED_ORACLES"
    elif writable:
        status = "REPAIRABLE"
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
) -> int:
    workspace = workspace.resolve()
    evidence_root = evidence_root.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    report = compile_repair_scope(_load_object(failure_case_path), workspace=workspace)
    normalized_path = evidence_root / "normalized-failure-case.json"
    normalized_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return base.run_stage2(
        workspace=workspace,
        failure_case_path=normalized_path,
        task_run_path=task_run_path,
        evidence_root=evidence_root,
        max_cycles=max_cycles,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--failure-case", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--max-cycles", type=int, default=8)
    args = parser.parse_args()
    if args.max_cycles < 1 or args.max_cycles > base.MAX_CYCLES:
        parser.error(f"--max-cycles must be between 1 and {base.MAX_CYCLES}")
    return run(
        workspace=Path(args.workspace),
        failure_case_path=Path(args.failure_case).resolve(),
        task_run_path=Path(args.task_run).resolve(),
        evidence_root=Path(args.evidence_root),
        max_cycles=args.max_cycles,
    )


if __name__ == "__main__":
    raise SystemExit(main())
