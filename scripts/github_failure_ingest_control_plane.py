#!/usr/bin/env python3
"""Augment governed failure ingestion with trusted control-plane failure markers.

The base ingestion controller intentionally requires machine-readable failed-gate
evidence before authorizing product source repair. This adapter also recognizes a
narrow autonomous control-plane implementation route: a real unittest/pytest
failure must pair by module name with an exact source-PR change under
``scripts/verify_engineering_*.py``. Tests/oracles remain read-only and changed
file metadata alone never creates authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import github_failure_ingest as base

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from autonomous_repair_router import (  # type: ignore  # noqa: E402
    CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE,
    PRODUCT_CODE_REPAIRABLE,
    route_failure,
    validate_route,
)

PRODUCT_SOURCE_DRIFT_MARKER = "product_source_changed:"
PRODUCT_SOURCE_ROOTS = ("services/", "web/", "contracts/")
BASELINE_TEST_MARKER = "test_baseline_matches_current_git_tracked_protected_snapshot"
BASELINE_COUNT_ASSERTION = re.compile(r"AssertionError:\s*(\d+)\s*!=\s*(\d+)")
BRIDGE_PROTECTED_EXACT = {
    ".github/workflows/governed-ci-failure-sweeper.yml",
    ".github/workflows/governed-ci-failure-sweeper-wakeup.yml",
    ".github/workflows/governed-ci-stage2-auto-handoff.yml",
    ".github/workflows/governed-ci-existing-candidate-adoption.yml",
    ".github/workflows/governed-ci-existing-candidate-solo-governance.yml",
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
    "scripts/github_existing_candidate_adoption.py",
    "scripts/verify_existing_candidate_adoption_contract.py",
}
_ORIGINAL_SUMMARY_FAILURES = base._summary_failures
_ORIGINAL_TASK_IMMUTABLE_BINDING = base._task_immutable_binding


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


def _protected_baseline_count_failures(
    files: list[tuple[Path, str]],
) -> list[dict[str, Any]]:
    """Recognize baseline cardinality drift without inventing implicated source paths."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for path, text in files:
        if BASELINE_TEST_MARKER not in text:
            continue
        for match in BASELINE_COUNT_ASSERTION.finditer(text):
            recorded = int(match.group(1))
            current = int(match.group(2))
            identity = (recorded, current)
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(
                {
                    "gate_id": "protected-product-source-baseline",
                    "status": "FAIL",
                    "category": "governance",
                    "owner": "skill-control-plane",
                    "failure_kind": "protected_baseline_drift",
                    "summary": (
                        "Protected product-source baseline file count differs from the "
                        f"current tracked protected snapshot: recorded={recorded} current={current}"
                    ),
                    "implicated_paths": [],
                    "evidence_source": path.name,
                    "machine_envelope": False,
                }
            )
    return rows


def _summary_failures(
    files: list[tuple[Path, str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    failures, excerpts = _ORIGINAL_SUMMARY_FAILURES(files)
    existing = {
        (
            str(row.get("gate_id") or ""),
            str(row.get("failure_kind") or ""),
            str(row.get("summary") or ""),
        )
        for row in failures
    }
    for row in [*_protected_baseline_count_failures(files), *_control_plane_failures(files)]:
        identity = (
            str(row.get("gate_id") or ""),
            str(row.get("failure_kind") or ""),
            str(row.get("summary") or ""),
        )
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
                "repair_domain": report.get("repair_domain"),
                "repair_route_sha256": (report.get("repair_route") or {}).get("route_sha256"),
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
    """Require legacy product candidates to be both log-derived and changed in that PR."""
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


def _semantic_route(
    report: dict[str, Any],
    *,
    artifact_files: list[tuple[Path, str]],
) -> dict[str, Any]:
    combined = "\n".join(text for _path, text in artifact_files)
    route = validate_route(
        route_failure(
            workflow_name=str(report.get("workflow_name") or ""),
            conclusion=str(report.get("conclusion") or ""),
            legacy_classification=str(report.get("classification") or ""),
            combined_text=combined,
            failed_gates=[row for row in report.get("failed_gates") or [] if isinstance(row, dict)],
            legacy_candidate_paths=report.get("candidate_paths") or [],
            source_changed_files=report.get("source_changed_files") or [],
            same_repository=report.get("same_repository") is True,
        )
    )
    report["repair_route"] = route
    report["repair_domain"] = route["repair_domain"]

    if route["repair_class"] == CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE:
        report["classification"] = "control_plane_implementation"
        report["candidate_paths"] = list(route["allowed_write_paths"])
        report["repair_allowed"] = True
        guard = {
            "gate_id": "skill-control-plane",
            "status": "FAIL",
            "category": "control-plane-implementation",
            "owner": "skill-control-plane",
            "failure_kind": "control_plane_implementation",
            "summary": route["reason"],
            "implicated_paths": list(route["allowed_write_paths"]),
            "machine_envelope": False,
        }
        existing = report.get("failed_gates") if isinstance(report.get("failed_gates"), list) else []
        if not any(
            isinstance(row, dict)
            and row.get("gate_id") == guard["gate_id"]
            and row.get("failure_kind") == guard["failure_kind"]
            for row in existing
        ):
            report["failed_gates"] = [*existing, guard]
        if not str(report.get("failure_summary") or "").strip():
            report["failure_summary"] = route["reason"]
        _recompute_failure_signature(report)
        return report

    if route["repair_class"] == PRODUCT_CODE_REPAIRABLE:
        report["repair_domain"] = route["repair_domain"]
        _recompute_failure_signature(report)
        return report

    if report.get("repair_allowed") is not True:
        report["repair_allowed"] = False
        _recompute_failure_signature(report)
    return report


def _task_immutable_binding(report: dict[str, Any]) -> dict[str, Any]:
    binding = dict(_ORIGINAL_TASK_IMMUTABLE_BINDING(report))
    domain = str(report.get("repair_domain") or "NONE").strip() or "NONE"
    route = report.get("repair_route") if isinstance(report.get("repair_route"), dict) else {}
    binding.update(
        {
            "repair_domain": domain,
            "repair_route_sha256": str(route.get("route_sha256") or ""),
        }
    )
    return binding


def install() -> None:
    """Install the narrow adapter without weakening the base ingestion boundary."""
    base.PROTECTED_EXACT.update(BRIDGE_PROTECTED_EXACT)
    base._summary_failures = _summary_failures
    base._task_immutable_binding = _task_immutable_binding


def build_report(*args: Any, **kwargs: Any) -> dict[str, Any]:
    install()
    artifact_files = kwargs.get("artifact_files")
    if not isinstance(artifact_files, list):
        artifact_files = []
    report = _scope_candidates_to_changed_evidence(base.build_report(*args, **kwargs))
    return _semantic_route(report, artifact_files=artifact_files)


def main() -> int:
    install()
    original_build_report = base.build_report

    def scoped_build_report(*args: Any, **kwargs: Any) -> dict[str, Any]:
        artifact_files = kwargs.get("artifact_files")
        if not isinstance(artifact_files, list):
            artifact_files = []
        report = _scope_candidates_to_changed_evidence(original_build_report(*args, **kwargs))
        return _semantic_route(report, artifact_files=artifact_files)

    base.build_report = scoped_build_report
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
