#!/usr/bin/env python3
"""Augment governed failure ingestion with trusted control-plane failure markers.

The base ingestion controller intentionally requires machine-readable failed-gate
evidence before authorizing product source repair. This adapter recognizes the
narrow autonomous control-plane implementation route and preserves semantic
non-repairable failures such as protected baseline drift. Unknown failures remain
write-closed but may enter a bounded read-only diagnosis state instead of forcing
an immediate owner interaction.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

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
from failure_recovery_policy import AUTO_DIAGNOSE, decide_recovery  # type: ignore  # noqa: E402

PRODUCT_SOURCE_DRIFT_MARKER = "product_source_changed:"
PRODUCT_SOURCE_ROOTS = ("services/", "web/", "contracts/")
LEGACY_BASELINE_TEST_MARKER = "test_baseline_matches_current_git_tracked_protected_snapshot"
BASELINE_COUNT_ASSERTION = re.compile(r"AssertionError:\s*(\d+)\s*!=\s*(\d+)")
BASELINE_SEMANTIC_ERRORS = (
    "current_file_count_mismatch",
    "protected_baseline_drift",
)
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
_ORIGINAL_CREATE_TASK_RUN = base._create_task_run
_ORIGINAL_WRITE_OUTPUT = base._write_output
_LAST_RECOVERY_OUTPUT: dict[str, Any] = {}


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


def _protected_baseline_failures(
    files: list[tuple[Path, str]],
) -> list[dict[str, Any]]:
    """Recognize semantic baseline drift without depending on one test name/format.

    Machine failure envelopes remain preferred. This fallback keys on stable policy
    error codes emitted by the baseline authority itself, while retaining the legacy
    count assertion parser for historical evidence. Baseline/oracle evidence never
    invents source write paths; explicit source drift markers remain a separate
    diagnostic row and cannot be promoted into baseline write authority.
    """

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for path, text in files:
        low = text.casefold()
        semantic = [token for token in BASELINE_SEMANTIC_ERRORS if token in low]
        legacy_counts = list(BASELINE_COUNT_ASSERTION.finditer(text)) if LEGACY_BASELINE_TEST_MARKER in text else []
        if "protected_baseline_drift" not in semantic and not legacy_counts:
            continue

        implicated: list[str] = []
        for line in text.splitlines():
            marker_index = line.casefold().find(PRODUCT_SOURCE_DRIFT_MARKER)
            if marker_index < 0:
                continue
            marker_text = line[marker_index:]
            for raw in base.PATH_PATTERN.findall(marker_text):
                candidate = base._normalize_repo_path(str(raw))
                if candidate.startswith(PRODUCT_SOURCE_ROOTS) and candidate not in implicated:
                    implicated.append(candidate)

        if semantic:
            summary = "Protected product-source baseline binding failed: " + ",".join(semantic)
            key = (summary, tuple(implicated))
            if key not in seen:
                seen.add(key)
                rows.append(
                    {
                        "gate_id": "protected-product-source-baseline",
                        "status": "FAIL",
                        "category": "governance",
                        "owner": "skill-control-plane",
                        "failure_kind": "protected_baseline_drift",
                        "summary": summary,
                        "implicated_paths": [],
                        "evidence_source": path.name,
                        "machine_envelope": False,
                    }
                )

        for match in legacy_counts:
            recorded = int(match.group(1))
            current = int(match.group(2))
            summary = (
                "Protected product-source baseline file count differs from the current "
                f"tracked protected snapshot: recorded={recorded} current={current}"
            )
            key = (summary, tuple(implicated))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "gate_id": "protected-product-source-baseline",
                    "status": "FAIL",
                    "category": "governance",
                    "owner": "skill-control-plane",
                    "failure_kind": "protected_baseline_drift",
                    "summary": summary,
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
    for row in [*_protected_baseline_failures(files), *_control_plane_failures(files)]:
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
                "recovery_disposition": report.get("recovery_disposition"),
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
    global _LAST_RECOVERY_OUTPUT

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

    elif route["repair_class"] == PRODUCT_CODE_REPAIRABLE:
        report["repair_domain"] = route["repair_domain"]

    elif report.get("repair_allowed") is not True:
        report["repair_allowed"] = False

    policy = decide_recovery(
        repair_route=route,
        classification=str(report.get("classification") or ""),
        diagnosis_attempt=0,
        max_diagnosis_attempts=2,
        retry_count=0,
        max_retry_count=3,
    )
    report["recovery_policy"] = policy
    report["recovery_disposition"] = policy["disposition"]
    report["human_required"] = policy["human_required"]
    _LAST_RECOVERY_OUTPUT = {
        "recovery_disposition": policy["disposition"],
        "human_required": policy["human_required"],
        "diagnostic_allowed": policy["diagnostic_allowed"],
        "retry_allowed": policy["retry_allowed"],
    }
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


def _create_read_only_diagnosis_task(report: dict[str, Any], path: Path) -> None:
    identity_binding = base._task_identity_binding(report)
    binding = _task_immutable_binding(report)
    task = base.TaskRunStore.open_or_create(
        path,
        task_id=base.stable_task_id("github-repair", identity_binding),
        task_kind="github-governed-repair",
        binding=binding,
        required_conditions=(
            "failure_ingested",
            "classification_complete",
            "source_changed",
            "validation_passed",
            "draft_pr_published",
            "governance_closed",
            "baseline_accepted",
            "exact_head_certified",
            "ready_for_review",
        ),
    )
    evidence = [str(path.with_name("failure-case.json"))]
    task.checkpoint(
        status="RUNNING",
        phase="FAILURE_INGESTED",
        workspace_fingerprint=None,
        evidence_refs=evidence,
        metadata={
            "governed_repair_state": "EVIDENCE_FROZEN",
            "classification": report["classification"],
            "repair_allowed": False,
            "recovery_disposition": AUTO_DIAGNOSE,
            "human_required": False,
            "production_closed": False,
        },
    )
    task.mark_condition("failure_ingested", evidence_refs=evidence)
    task.mark_condition(
        "classification_complete",
        evidence_refs=[f"classification:{report['classification']}"],
    )
    task.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="READ_ONLY_DIAGNOSIS_REQUIRED",
        workspace_fingerprint=None,
        evidence_refs=evidence,
        metadata={
            "governed_repair_state": "EVIDENCE_FROZEN",
            "next_action": "analyze_failure",
            "source_write_allowed": False,
            "test_write_allowed": False,
            "oracle_write_allowed": False,
            "diagnosis_attempt": 0,
            "max_diagnosis_attempts": 2,
            "human_required": False,
            "production_closed": False,
        },
    )


def _create_task_run(report: dict[str, Any], path: Path) -> None:
    policy = report.get("recovery_policy") if isinstance(report.get("recovery_policy"), Mapping) else {}
    if policy.get("disposition") == AUTO_DIAGNOSE:
        _create_read_only_diagnosis_task(report, path)
        return
    _ORIGINAL_CREATE_TASK_RUN(report, path)


def _write_output(path: Path | None, values: dict[str, Any]) -> None:
    enriched = dict(values)
    enriched.update(_LAST_RECOVERY_OUTPUT)
    _ORIGINAL_WRITE_OUTPUT(path, enriched)


def install() -> None:
    """Install the narrow adapter without weakening the base ingestion boundary."""
    base.PROTECTED_EXACT.update(BRIDGE_PROTECTED_EXACT)
    base._summary_failures = _summary_failures
    base._task_immutable_binding = _task_immutable_binding
    base._create_task_run = _create_task_run
    base._write_output = _write_output


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
