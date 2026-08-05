#!/usr/bin/env python3
"""Trusted Stage-2 scope normalizer for governed repair.

Stage-1 logs can mention verifier/control files that were not changed by the source
pull request. This wrapper narrows the already evidence-derived candidate set to
its intersection with ``source_changed_files`` before delegating to the existing
bounded orchestrator. Changed-file metadata can only remove scope, never add it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import github_repair_orchestrator as base


class ScopeNormalizationError(RuntimeError):
    """Fail-closed Stage-2 scope normalization error."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScopeNormalizationError(f"invalid JSON input: {path}") from exc
    if not isinstance(payload, dict):
        raise ScopeNormalizationError(f"JSON object required: {path}")
    return payload


def _normalize_path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("./")


def normalize_failure_case(report: dict[str, Any]) -> dict[str, Any]:
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
        "schema": "stage2-scope-normalization@1",
        "evidence_candidates": candidates,
        "source_changed_files": changed,
        "effective_candidates": scoped,
        "scope_expanded": False,
    }
    return normalized


def run(
    *,
    workspace: Path,
    failure_case_path: Path,
    task_run_path: Path,
    evidence_root: Path,
    max_cycles: int,
) -> int:
    evidence_root = evidence_root.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    report = normalize_failure_case(_load_object(failure_case_path))
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
