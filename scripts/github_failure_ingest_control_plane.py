#!/usr/bin/env python3
"""Augment governed failure ingestion with trusted control-plane failure markers.

The base ingestion controller intentionally requires machine-readable failed-gate
evidence before authorizing source repair. Some failures happen before the Quality
Loop can upload ``run-summary.json``; the Skill control plane still emits an exact
``product_source_changed:`` marker. This wrapper recognizes only that narrow marker,
keeps changed-file metadata non-authoritative, and then delegates to the existing
bounded ingestion controller.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import github_failure_ingest as base

PRODUCT_SOURCE_DRIFT_MARKER = "product_source_changed:"
PRODUCT_SOURCE_ROOTS = ("services/", "web/", "contracts/")
BRIDGE_PROTECTED_EXACT = {
    ".github/workflows/governed-ci-failure-sweeper.yml",
    ".github/workflows/governed-ci-failure-sweeper-wakeup.yml",
    ".github/workflows/governed-ci-stage2-auto-handoff.yml",
    "scripts/github_failure_ingest_control_plane.py",
    "scripts/github_failure_recovery_event.py",
    "scripts/github_failure_sweeper_event.py",
    "scripts/github_quality_failure_event.py",
    "scripts/github_stage2_auto_handoff.py",
    "scripts/github_repair_orchestrator_control_plane.py",
    "scripts/github_stage2_handoff.py",
    "scripts/github_repair_stage3.py",
    "scripts/github_repair_stage3_tree.py",
    "scripts/github_repair_stage3_publish.py",
    "scripts/github_repair_stage3_complete.py",
}
_ORIGINAL_SUMMARY_FAILURES = base._summary_failures


def _control_plane_failures(
    files: list[tuple[Path, str]],
) -> list[dict[str, Any]]:
    """Extract only exact product-source drift markers from bounded log evidence."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for _path, text in files:
        for line in text.splitlines():
            lowered = line.casefold()
            marker_index = lowered.find(PRODUCT_SOURCE_DRIFT_MARKER)
            if marker_index < 0:
                continue
            marker_text = line[marker_index:]
            paths: list[str] = []
            for raw in base.PATH_PATTERN.findall(marker_text):
                candidate = base._normalize_repo_path(str(raw))
                if not candidate.startswith(PRODUCT_SOURCE_ROOTS) or candidate in paths:
                    continue
                paths.append(candidate)
            key = tuple(paths)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "gate_id": "project-compatibility-smoke",
                    "status": "FAIL",
                    "category": "contract",
                    "owner": "skill-control-plane",
                    "failure_kind": "product_source_drift",
                    "summary": PRODUCT_SOURCE_DRIFT_MARKER + ",".join(paths),
                }
            )
    return rows


def _summary_failures(
    files: list[tuple[Path, str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    failures, excerpts = _ORIGINAL_SUMMARY_FAILURES(files)
    existing = {
        (str(row.get("gate_id") or ""), str(row.get("summary") or ""))
        for row in failures
    }
    for row in _control_plane_failures(files):
        identity = (str(row["gate_id"]), str(row["summary"]))
        if identity not in existing:
            failures.append(row)
            existing.add(identity)
    return failures, excerpts


def _recompute_failure_signature(report: dict[str, Any]) -> None:
    report["failure_signature"] = hashlib.sha256(
        json.dumps(
            {
                "workflow": report.get("workflow_name"),
                "sha": report.get("head_sha"),
                "classification": report.get("classification"),
                "gates": report.get("failed_gates") or [],
                "summary": report.get("failure_summary") or "",
                "candidate_paths": report.get("candidate_paths") or [],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _scope_candidates_to_changed_evidence(report: dict[str, Any]) -> dict[str, Any]:
    """Require PR candidates to be both log-derived and changed in that PR.

    Changed-file metadata never creates a candidate. It may only remove unrelated
    paths that appeared incidentally in logs, stack traces, or verifier commands.
    Push runs without changed-file metadata retain the base evidence-derived scope.
    """
    changed = {
        base._normalize_repo_path(str(item))
        for item in report.get("source_changed_files") or []
        if str(item).strip()
    }
    if not changed:
        return report
    candidates = [
        base._normalize_repo_path(str(item))
        for item in report.get("candidate_paths") or []
        if str(item).strip()
    ]
    scoped = [path for path in candidates if path in changed]
    if scoped == candidates:
        return report
    report["candidate_paths"] = scoped
    report["repair_allowed"] = bool(report.get("repair_allowed") is True and scoped)
    _recompute_failure_signature(report)
    return report


def install() -> None:
    """Install the narrow adapter without weakening the base ingestion boundary."""
    base.PROTECTED_EXACT.update(BRIDGE_PROTECTED_EXACT)
    base._summary_failures = _summary_failures


def build_report(*args: Any, **kwargs: Any) -> dict[str, Any]:
    install()
    return _scope_candidates_to_changed_evidence(base.build_report(*args, **kwargs))


def main() -> int:
    install()
    original_build_report = base.build_report

    def scoped_build_report(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _scope_candidates_to_changed_evidence(original_build_report(*args, **kwargs))

    base.build_report = scoped_build_report
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
