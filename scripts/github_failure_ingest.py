#!/usr/bin/env python3
"""Ingest a failed GitHub Actions workflow_run into governed evidence.

Logs and artifacts are untrusted data. This module never executes their contents,
never exposes production secrets, and records a durable TaskRun so later repair
stages can resume without screenshots or a manually supplied workflow run ID.

The classifier prefers machine-readable failure envelopes and recognizes protected
baseline drift as its own non-repairable transition instead of misclassifying it
as an unknown product-code failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from task_run import TaskRunStore, stable_task_id  # type: ignore  # noqa: E402

SCHEMA = "github-failure-ingest@1"
MACHINE_FAILURE_SCHEMA = "machine-failure-envelope@1"
CODE_FAILURE_CONCLUSIONS = {"failure"}
NON_REPAIRABLE_CONCLUSIONS = {
    "cancelled": "cancelled",
    "action_required": "policy_or_approval",
    "startup_failure": "runner_or_platform",
    "stale": "stale",
    "skipped": "skipped",
}
ENVIRONMENT_TERMS = (
    "blocked_by_environment",
    "environment blocker",
    "missing secret",
    "secret is not configured",
    "api key is missing",
    "api key environment secret is missing",
    "authentication failed",
    "invalid api key",
    "incorrect api key",
    "rate limit exceeded",
    "could not resolve host",
    "temporary failure in name resolution",
    "connection refused",
    "no space left on device",
    "runner lost communication",
    "service unavailable",
)
TIMEOUT_TERMS = ("timed out", "deadline exceeded", "exit code 124")
PROTECTED_PREFIXES = (
    "governance/",
    "skill-system/",
    ".git/",
    ".quality/",
)
PROTECTED_EXACT = {
    ".github/workflows/governed-ci-failure-ingest.yml",
    ".github/workflows/governed-ci-repair.yml",
    ".github/workflows/governed-ci-repair-stage2.yml",
    ".github/workflows/governed-ci-repair-stage3.yml",
    ".github/workflows/governed-ci-repair-governance.yml",
    "scripts/github_failure_ingest.py",
    "scripts/github_agent_fixer.py",
    "scripts/github_repair_orchestrator.py",
    "scripts/github_repair_orchestrator_control_plane.py",
    "scripts/github_repair_authority.py",
    "scripts/github_repair_rca.py",
    "scripts/github_repair_stage3.py",
    "scripts/github_repair_governance.py",
    "scripts/github_repair_baseline_acceptance.py",
    "scripts/github_repair_exact_head.py",
    "scripts/github_repair_task.py",
    "scripts/quality_loop.py",
    "scripts/repair_loop.py",
    "skill-system/registry/product-source-baseline.json",
}
PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])((?:services|scripts|tests|web|contracts|deployment|\.github/workflows)/"
    r"[A-Za-z0-9_./@+\-]+\.(?:py|js|jsx|ts|tsx|mjs|cjs|json|ya?ml|toml|md|sh))(?![A-Za-z0-9_.-])"
)
BASELINE_ASSERTION_PATH = re.compile(
    r"(?m)^\s*:\s*((?:services|web|contracts)/[^\s]+)\s*$"
)
SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(api[_ -]?key|token|secret|password|authorization)\b"
        r"(\s*[:=]\s*|\s+)(bearer\s+)?[^\s,;]+"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def redact(text: str) -> str:
    result = text
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def _bounded_text_files(
    roots: Iterable[Path],
    *,
    max_total: int = 1_500_000,
    max_file: int = 250_000,
) -> list[tuple[Path, str]]:
    """Read bounded plain-text evidence without following artifact symlinks."""
    rows: list[tuple[Path, str]] = []
    consumed = 0
    for raw_root in roots:
        root = raw_root.resolve()
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in candidates:
            if consumed >= max_total:
                return rows
            if path.is_symlink() or not path.is_file():
                continue
            try:
                resolved = path.resolve()
                resolved.relative_to(root if root.is_dir() else root.parent)
            except (OSError, ValueError):
                continue
            try:
                data = path.read_bytes()[:max_file]
            except OSError:
                continue
            if b"\x00" in data[:4096]:
                continue
            remaining = max_total - consumed
            text = data[:remaining].decode("utf-8", errors="replace")
            consumed += len(text.encode("utf-8", errors="ignore"))
            rows.append((path, redact(text)))
    return rows


def _normalize_repo_path(value: str) -> str:
    return value.strip().replace("\\", "/").lstrip("./")


def _safe_candidate(path: str, workspace: Path) -> bool:
    normalized = _normalize_repo_path(path)
    if not normalized or normalized in PROTECTED_EXACT:
        return False
    if any(normalized.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return False
    resolved = (workspace / normalized).resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError:
        return False
    return resolved.is_file() and not resolved.is_symlink()


def extract_candidate_paths(
    text: str,
    workspace: Path,
    changed_files: Iterable[str] = (),
) -> list[str]:
    """Return evidence-derived candidates without expanding scope from PR metadata."""
    changed = {_normalize_repo_path(item) for item in changed_files}
    found: list[str] = []
    for raw in PATH_PATTERN.findall(text):
        path = _normalize_repo_path(str(raw))
        if not _safe_candidate(path, workspace) or path in found:
            continue
        found.append(path)
    if changed:
        found.sort(key=lambda item: (item not in changed, item))
    return found[:16]


def _machine_failure_rows(files: list[tuple[Path, str]]) -> list[dict[str, Any]]:
    """Parse bounded structured failure envelopes from whole files or JSONL rows."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def accept(payload: object, source: Path) -> None:
        if not isinstance(payload, dict) or payload.get("schema") != MACHINE_FAILURE_SCHEMA:
            return
        gate_id = str(payload.get("gate_id") or "machine-failure").strip()
        failure_kind = str(payload.get("failure_kind") or "unknown").strip().casefold()
        detail = redact(str(payload.get("detail") or payload.get("summary") or ""))[:2000]
        implicated: list[str] = []
        for raw in payload.get("implicated_paths") or []:
            path = _normalize_repo_path(str(raw))
            if path and path not in implicated:
                implicated.append(path)
        identity = json.dumps(
            {"gate": gate_id, "kind": failure_kind, "paths": implicated, "detail": detail},
            sort_keys=True,
        )
        if identity in seen:
            return
        seen.add(identity)
        rows.append(
            {
                "gate_id": gate_id,
                "status": str(payload.get("status") or "FAIL"),
                "category": str(payload.get("category") or "verification"),
                "owner": str(payload.get("owner") or "machine-gate"),
                "failure_kind": failure_kind,
                "summary": detail,
                "implicated_paths": implicated,
                "evidence_source": source.name,
                "machine_envelope": True,
            }
        )

    for path, text in files:
        stripped = text.strip()
        if not stripped:
            continue
        try:
            accept(json.loads(stripped), path)
        except json.JSONDecodeError:
            pass
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{") or MACHINE_FAILURE_SCHEMA not in line:
                continue
            try:
                accept(json.loads(line), path)
            except json.JSONDecodeError:
                continue
    return rows


def _protected_baseline_rows(files: list[tuple[Path, str]]) -> list[dict[str, Any]]:
    """Recognize the protected-baseline unittest failure even without gate JSON."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    marker = "test_baseline_matches_current_git_tracked_protected_snapshot"
    for path, text in files:
        if marker not in text:
            continue
        for match in BASELINE_ASSERTION_PATH.finditer(text):
            implicated = _normalize_repo_path(match.group(1))
            if implicated in seen:
                continue
            seen.add(implicated)
            results.append(
                {
                    "gate_id": "protected-product-source-baseline",
                    "status": "FAIL",
                    "category": "governance",
                    "owner": "skill-control-plane",
                    "failure_kind": "protected_baseline_drift",
                    "summary": (
                        "Protected product-source baseline differs from the current tracked "
                        f"snapshot: {implicated}"
                    ),
                    "implicated_paths": [implicated],
                    "evidence_source": path.name,
                    "machine_envelope": False,
                }
            )
    return results


def _summary_failures(
    files: list[tuple[Path, str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    failures: list[dict[str, Any]] = []
    excerpts: list[str] = []
    for row in [*_machine_failure_rows(files), *_protected_baseline_rows(files)]:
        if row not in failures:
            failures.append(row)

    for path, text in files:
        if path.name == "run-summary.json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                for row in payload.get("results") or []:
                    if not isinstance(row, dict):
                        continue
                    status = str(row.get("status") or "").upper()
                    if status not in {
                        "FAIL",
                        "FAILED",
                        "BLOCKED",
                        "BLOCKED_BY_ENVIRONMENT",
                    }:
                        continue
                    metadata = row.get("metadata")
                    if not isinstance(metadata, dict):
                        metadata = {}
                    failure = {
                        "gate_id": str(row.get("id") or "unknown"),
                        "status": str(row.get("status") or "FAIL"),
                        "category": str(row.get("category") or "verification"),
                        "owner": str(row.get("owner") or "unassigned"),
                        "failure_kind": str(metadata.get("failure_kind") or ""),
                        "summary": redact(
                            str(row.get("stderr") or row.get("error") or "")
                        )[:2000],
                    }
                    if failure not in failures:
                        failures.append(failure)
        for line in text.splitlines():
            low = line.casefold()
            if any(
                token in low
                for token in (
                    "error",
                    "failed",
                    "exception",
                    "traceback",
                    "blocked_by_environment",
                )
            ):
                clean = redact(line.strip())
                if clean and clean not in excerpts:
                    excerpts.append(clean[:1000])
                    if len(excerpts) >= 40:
                        break
    return failures, excerpts


def classify(
    workflow_name: str,
    conclusion: str,
    combined_text: str,
    failures: list[dict[str, Any]],
) -> str:
    normalized_conclusion = conclusion.casefold()
    low = combined_text.casefold()
    kinds = {str(row.get("failure_kind") or "").casefold() for row in failures}
    if "protected_baseline_drift" in kinds:
        return "protected_baseline_drift"
    if normalized_conclusion == "timed_out" or any(term in low for term in TIMEOUT_TERMS):
        return "timeout"
    if normalized_conclusion in NON_REPAIRABLE_CONCLUSIONS:
        return NON_REPAIRABLE_CONCLUSIONS[normalized_conclusion]
    if any(term in low for term in ENVIRONMENT_TERMS):
        return "environment"
    if any(
        str(row.get("status") or "").upper()
        in {"BLOCKED", "BLOCKED_BY_ENVIRONMENT"}
        or str(row.get("failure_kind") or "").casefold() == "environment"
        for row in failures
    ):
        return "environment"
    if failures:
        return "code_or_contract"
    if workflow_name == "wp08-full-stack-certification":
        return "production_diagnostic"
    if workflow_name == "quality" and normalized_conclusion in CODE_FAILURE_CONCLUSIONS:
        return "unknown_failure_without_gate_evidence"
    return "unknown"


def _sanitize_branch(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._/-]+", "-", value).strip("-./")
    return cleaned[:180] or "governed-repair/unknown"


def build_report(
    event: dict[str, Any],
    *,
    workspace: Path,
    artifact_files: list[tuple[Path, str]],
    changed_files: Iterable[str] = (),
) -> dict[str, Any]:
    run = event.get("workflow_run") if isinstance(event.get("workflow_run"), dict) else {}
    repository = event.get("repository") if isinstance(event.get("repository"), dict) else {}
    repo_name = str(repository.get("full_name") or os.getenv("GITHUB_REPOSITORY") or "")
    run_repo = run.get("head_repository") if isinstance(run.get("head_repository"), dict) else {}
    head_repo = str(run_repo.get("full_name") or repo_name)
    workflow_name = str(run.get("name") or "unknown")
    conclusion = str(run.get("conclusion") or "unknown")
    run_id = str(run.get("id") or "unknown")
    run_attempt = str(run.get("run_attempt") or "1")
    head_sha = str(run.get("head_sha") or "")
    head_branch = str(run.get("head_branch") or "main")
    pull_requests = run.get("pull_requests") if isinstance(run.get("pull_requests"), list) else []
    first_pr = pull_requests[0] if pull_requests and isinstance(pull_requests[0], dict) else {}
    pr_head = first_pr.get("head") if isinstance(first_pr.get("head"), dict) else {}
    pr_base = first_pr.get("base") if isinstance(first_pr.get("base"), dict) else {}
    source_pr = int(first_pr.get("number") or 0)

    failures, excerpts = _summary_failures(artifact_files)
    combined = "\n".join(text for _path, text in artifact_files)
    classification = classify(workflow_name, conclusion, combined, failures)
    source_changed_files = [
        path
        for path in (_normalize_repo_path(item) for item in changed_files)
        if path
    ][:250]
    candidates = extract_candidate_paths(combined, workspace, source_changed_files)
    for row in failures:
        for raw in row.get("implicated_paths") or []:
            path = _normalize_repo_path(str(raw))
            if _safe_candidate(path, workspace) and path not in candidates:
                candidates.append(path)
    candidates = candidates[:16]
    same_repository = bool(repo_name and head_repo == repo_name)
    repair_allowed = bool(
        conclusion.casefold() in CODE_FAILURE_CONCLUSIONS
        and same_repository
        and classification == "code_or_contract"
        and failures
        and candidates
        and workflow_name
        not in {
            "governed-ci-repair",
            "governed-ci-repair-stage2",
            "governed-ci-repair-stage3",
            "governed-ci-repair-governance",
        }
    )

    if head_branch.startswith("governed-repair/"):
        repair_branch = head_branch
        base_branch = str(pr_base.get("ref") or "main")
    else:
        repair_branch = _sanitize_branch(f"governed-repair/{workflow_name}-{run_id}")
        base_branch = str(pr_head.get("ref") or (head_branch if source_pr else "main"))

    summary_parts = [row.get("summary") for row in failures if row.get("summary")]
    if not summary_parts:
        summary_parts = excerpts[:8]
    failure_summary = "\n".join(str(item) for item in summary_parts)[:12000]
    report = {
        "schema": SCHEMA,
        "status": "INGESTED",
        "repository": repo_name,
        "workflow_name": workflow_name,
        "workflow_run_id": run_id,
        "workflow_run_attempt": run_attempt,
        "workflow_url": str(run.get("html_url") or ""),
        "conclusion": conclusion,
        "event": str(run.get("event") or ""),
        "head_sha": head_sha,
        "head_branch": head_branch,
        "head_repository": head_repo,
        "same_repository": same_repository,
        "source_pr_number": source_pr,
        "classification": classification,
        "repair_allowed": repair_allowed,
        "candidate_paths": candidates,
        "source_changed_files": source_changed_files,
        "failed_gates": failures,
        "failure_summary": failure_summary,
        "repair_branch": repair_branch,
        "repair_base_branch": base_branch,
        "production_closed": False,
    }
    report["failure_signature"] = hashlib.sha256(
        json.dumps(
            {
                "workflow": workflow_name,
                "sha": head_sha,
                "classification": classification,
                "gates": failures,
                "summary": failure_summary,
                "candidate_paths": candidates,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return report


def _write_output(path: Path | None, values: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            text = "true" if value is True else "false" if value is False else str(value)
            if "\n" in text:
                marker = f"EOF_{hashlib.sha256((key + text).encode()).hexdigest()[:12]}"
                handle.write(f"{key}<<{marker}\n{text}\n{marker}\n")
            else:
                handle.write(f"{key}={text}\n")


def _create_task_run(report: dict[str, Any], path: Path) -> None:
    binding = {
        "repository": report["repository"],
        "workflow_name": report["workflow_name"],
        "workflow_run_id": report["workflow_run_id"],
        "workflow_run_attempt": report["workflow_run_attempt"],
        "head_sha": report["head_sha"],
        "failure_signature": report["failure_signature"],
    }
    task = TaskRunStore.open_or_create(
        path,
        task_id=stable_task_id("github-repair", binding),
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
    task.checkpoint(
        status="RUNNING",
        phase="FAILURE_INGESTED",
        workspace_fingerprint=None,
        evidence_refs=[str(path.with_name("failure-case.json"))],
        metadata={
            "governed_repair_state": "EVIDENCE_FROZEN",
            "classification": report["classification"],
            "repair_allowed": report["repair_allowed"],
            "production_closed": False,
        },
    )
    task.mark_condition(
        "failure_ingested",
        evidence_refs=[str(path.with_name("failure-case.json"))],
    )
    task.mark_condition(
        "classification_complete",
        evidence_refs=[f"classification:{report['classification']}"],
    )
    if report["repair_allowed"]:
        task.checkpoint(
            status="WAITING_EXTERNAL_RESULT",
            phase="RCA_REQUIRED",
            workspace_fingerprint=None,
            evidence_refs=[str(path.with_name("failure-case.json"))],
            metadata={
                "governed_repair_state": "EVIDENCE_FROZEN",
                "next_action": "run mandatory read-only RCA before any write grant",
                "production_closed": False,
            },
        )
    else:
        task.block(
            code="AUTOMATIC_REPAIR_NOT_AUTHORIZED",
            reason=(
                f"classification={report['classification']} "
                f"same_repository={report['same_repository']} "
                f"failed_gates={len(report['failed_gates'])} "
                f"candidates={len(report['candidate_paths'])}"
            ),
            attempted_strategies=("workflow-run-ingest",),
            next_action=(
                "inspect the machine-classified evidence; protected baseline drift must "
                "enter governance/baseline acceptance rather than automatic source repair"
                if report["classification"] == "protected_baseline_drift"
                else "inspect the generated GitHub issue and provision the missing "
                "environment or create a separately governed repair target"
            ),
            workspace_fingerprint=None,
            evidence_refs=[str(path.with_name("failure-case.json"))],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--artifacts", action="append", default=[])
    parser.add_argument("--logs", action="append", default=[])
    parser.add_argument("--changed-files")
    parser.add_argument("--output", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    event = _read_json(Path(args.event))
    workspace = Path(args.workspace).resolve()
    roots = [Path(value).resolve() for value in [*args.artifacts, *args.logs]]
    files = _bounded_text_files(roots)
    changed: list[str] = []
    if args.changed_files and Path(args.changed_files).is_file():
        payload = json.loads(Path(args.changed_files).read_text(encoding="utf-8"))
        if isinstance(payload, list):
            changed = [str(item) for item in payload]
    report = build_report(
        event,
        workspace=workspace,
        artifact_files=files,
        changed_files=changed,
    )

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    task_run = Path(args.task_run).resolve()
    task_run.parent.mkdir(parents=True, exist_ok=True)
    _create_task_run(report, task_run)
    _write_output(
        Path(args.github_output).resolve() if args.github_output else None,
        {
            "repair_allowed": report["repair_allowed"],
            "classification": report["classification"],
            "repair_branch": report["repair_branch"],
            "repair_base_branch": report["repair_base_branch"],
            "head_sha": report["head_sha"],
            "head_branch": report["head_branch"],
            "source_pr_number": report["source_pr_number"],
            "workflow_run_id": report["workflow_run_id"],
            "failure_signature": report["failure_signature"],
        },
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "classification",
                    "repair_allowed",
                    "workflow_run_id",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
