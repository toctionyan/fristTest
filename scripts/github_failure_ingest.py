#!/usr/bin/env python3
"""Ingest a failed GitHub Actions workflow_run into governed repair evidence.

The script treats logs and artifacts as untrusted data.  It never executes their
contents and never prints secrets.  It creates a durable TaskRun checkpoint so a
later repair job can resume without screenshots or a manually supplied run ID.
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
FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
ENVIRONMENT_TERMS = (
    "blocked_by_environment",
    "environment blocker",
    "missing secret",
    "secret is not configured",
    "api key is missing",
    "authentication failed",
    "unauthorized",
    "forbidden",
    "rate limit",
    "connection refused",
    "could not resolve host",
    "temporary failure in name resolution",
    "no space left on device",
    "runner lost communication",
)
TIMEOUT_TERMS = ("timed out", "deadline exceeded", "exit code 124", "operation was canceled")
PROTECTED_PREFIXES = (
    "governance/",
    "skill-system/",
    ".git/",
    ".quality/",
)
PROTECTED_EXACT = {
    ".github/workflows/governed-ci-repair.yml",
    "scripts/github_failure_ingest.py",
    "scripts/github_agent_fixer.py",
    "scripts/github_repair_orchestrator.py",
    "scripts/github_repair_task.py",
    "scripts/quality_loop.py",
    "scripts/repair_loop.py",
    "skill-system/registry/product-source-baseline.json",
}
PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])((?:services|scripts|tests|web|contracts|deployment|\.github/workflows)/"
    r"[A-Za-z0-9_./@+\-]+\.(?:py|js|jsx|ts|tsx|mjs|cjs|json|ya?ml|toml|md|sh))(?![A-Za-z0-9_.-])"
)
SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_ -]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
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


def _bounded_text_files(roots: Iterable[Path], *, max_total: int = 1_500_000) -> list[tuple[Path, str]]:
    rows: list[tuple[Path, str]] = []
    consumed = 0
    for root in roots:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in candidates:
            if consumed >= max_total:
                return rows
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:4096]:
                continue
            remaining = max_total - consumed
            text = data[:remaining].decode("utf-8", errors="replace")
            consumed += len(text.encode("utf-8", errors="ignore"))
            rows.append((path, redact(text)))
    return rows


def _safe_candidate(path: str, workspace: Path) -> bool:
    normalized = path.strip().lstrip("./")
    if not normalized or normalized in PROTECTED_EXACT:
        return False
    if any(normalized.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return False
    resolved = (workspace / normalized).resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError:
        return False
    return resolved.is_file()


def extract_candidate_paths(text: str, workspace: Path, changed_files: Iterable[str] = ()) -> list[str]:
    found: list[str] = []
    for raw in PATH_PATTERN.findall(text) + list(changed_files):
        path = str(raw).strip().lstrip("./")
        if _safe_candidate(path, workspace) and path not in found:
            found.append(path)
    return found[:16]


def _summary_failures(files: list[tuple[Path, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    failures: list[dict[str, Any]] = []
    excerpts: list[str] = []
    for path, text in files:
        if path.name == "run-summary.json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                for row in payload.get("results") or []:
                    if not isinstance(row, dict) or str(row.get("status") or "").upper() not in {"FAIL", "FAILED", "BLOCKED", "BLOCKED_BY_ENVIRONMENT"}:
                        continue
                    failures.append(
                        {
                            "gate_id": str(row.get("id") or "unknown"),
                            "status": str(row.get("status") or "FAIL"),
                            "category": str(row.get("category") or "verification"),
                            "owner": str(row.get("owner") or "unassigned"),
                            "failure_kind": str((row.get("metadata") or {}).get("failure_kind") or ""),
                            "summary": redact(str(row.get("stderr") or row.get("error") or ""))[:2000],
                        }
                    )
        for line in text.splitlines():
            low = line.casefold()
            if any(token in low for token in ("error", "failed", "exception", "traceback", "blocked_by_environment")):
                clean = redact(line.strip())
                if clean and clean not in excerpts:
                    excerpts.append(clean[:1000])
                    if len(excerpts) >= 40:
                        break
    return failures, excerpts


def classify(workflow_name: str, conclusion: str, combined_text: str, failures: list[dict[str, Any]]) -> str:
    low = combined_text.casefold()
    if conclusion == "timed_out" or any(term in low for term in TIMEOUT_TERMS):
        return "timeout"
    if any(term in low for term in ENVIRONMENT_TERMS):
        return "environment"
    if any(str(row.get("status") or "").upper() in {"BLOCKED", "BLOCKED_BY_ENVIRONMENT"} for row in failures):
        return "environment"
    if failures:
        return "code_or_contract"
    if workflow_name == "quality" and conclusion in FAILED_CONCLUSIONS:
        return "code_or_contract"
    if workflow_name == "wp08-full-stack-certification":
        return "production_diagnostic"
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
    diagnostics = "\n".join([str(row.get("summary") or "") for row in failures] + excerpts)
    classification = classify(workflow_name, conclusion, diagnostics, failures)
    candidates = extract_candidate_paths(combined, workspace, changed_files)
    same_repository = bool(repo_name and head_repo == repo_name)
    protected_source = any(path in PROTECTED_EXACT or path.startswith(PROTECTED_PREFIXES) for path in candidates)
    repair_allowed = bool(
        conclusion in FAILED_CONCLUSIONS
        and same_repository
        and classification == "code_or_contract"
        and candidates
        and not protected_source
        and workflow_name != "governed-ci-repair"
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
        ),
    )
    task.checkpoint(
        status="RUNNING",
        phase="FAILURE_INGESTED",
        workspace_fingerprint=None,
        evidence_refs=[str(path.with_name("failure-case.json"))],
        metadata={"classification": report["classification"], "repair_allowed": report["repair_allowed"]},
    )
    task.mark_condition("failure_ingested", evidence_refs=[str(path.with_name("failure-case.json"))])
    task.mark_condition("classification_complete", evidence_refs=[f"classification:{report['classification']}"])
    if report["repair_allowed"]:
        task.checkpoint(
            status="WAITING_EXTERNAL_RESULT",
            phase="REPAIR_READY",
            workspace_fingerprint=None,
            evidence_refs=[str(path.with_name("failure-case.json"))],
            metadata={"next_action": "run governed repair job"},
        )
    else:
        task.block(
            code="AUTOMATIC_REPAIR_NOT_AUTHORIZED",
            reason=f"classification={report['classification']} same_repository={report['same_repository']} candidates={len(report['candidate_paths'])}",
            attempted_strategies=("workflow-run-ingest",),
            next_action="inspect the generated GitHub issue and provision environment or approve a governed repair target",
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
    report = build_report(event, workspace=workspace, artifact_files=files, changed_files=changed)

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
    print(json.dumps({key: report[key] for key in ("status", "classification", "repair_allowed", "workflow_run_id")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
