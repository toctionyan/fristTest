#!/usr/bin/env python3
"""Convert an untrusted failed workflow_run into governed, machine-readable evidence."""
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
WATCHED_CODE_WORKFLOWS = {"quality", "skill-self-validation", "governed-repair-validation"}
AUTO_REPAIR_PREFIXES = ("services/", "web/", "contracts/")
PROTECTED_PREFIXES = (
    "governance/",
    "skill-system/",
    ".github/",
    "deployment/",
    "scripts/",
    ".git/",
    ".quality/",
)
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
PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])((?:services|scripts|tests|web|contracts|deployment|\.github/workflows)/"
    r"[A-Za-z0-9_./@+\-]+\.(?:py|js|jsx|ts|tsx|mjs|cjs|json|ya?ml|toml|md|sh))(?![A-Za-z0-9_.-])"
)
SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_ -]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def redact(text: str) -> str:
    value = text
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


def _bounded_text_files(roots: Iterable[Path], *, maximum: int = 1_500_000) -> list[tuple[Path, str]]:
    rows: list[tuple[Path, str]] = []
    consumed = 0
    for root in roots:
        if not root.exists() or root.is_symlink():
            continue
        boundary = root.resolve() if root.is_dir() else root.resolve().parent
        candidates = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in candidates:
            if consumed >= maximum:
                return rows
            if path.is_symlink():
                continue
            try:
                path.resolve().relative_to(boundary)
                data = path.read_bytes()
            except (OSError, ValueError):
                continue
            if b"\x00" in data[:4096]:
                continue
            text = data[: maximum - consumed].decode("utf-8", errors="replace")
            consumed += len(text.encode("utf-8", errors="ignore"))
            rows.append((path, redact(text)))
    return rows


def _safe_candidate(relative: str, workspace: Path) -> bool:
    path_text = relative.strip().lstrip("./")
    if not path_text or not any(path_text.startswith(prefix) for prefix in AUTO_REPAIR_PREFIXES):
        return False
    if any(path_text.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return False
    if re.search(r"(^|/)\.env($|\.)", path_text):
        return False
    unresolved = workspace / path_text
    if unresolved.is_symlink():
        return False
    resolved = unresolved.resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError:
        return False
    return resolved.is_file()


def extract_candidate_paths(text: str, workspace: Path, changed_files: Iterable[str] = ()) -> list[str]:
    result: list[str] = []
    for raw in PATH_PATTERN.findall(text) + list(changed_files):
        relative = str(raw).strip().lstrip("./")
        if _safe_candidate(relative, workspace) and relative not in result:
            result.append(relative)
    return result[:16]


def _summaries(files: list[tuple[Path, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    failed: list[dict[str, Any]] = []
    excerpts: list[str] = []
    for path, text in files:
        if path.name == "run-summary.json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                for row in payload.get("results") or []:
                    status = str(row.get("status") or "").upper() if isinstance(row, dict) else ""
                    if not isinstance(row, dict) or status not in {"FAIL", "FAILED", "BLOCKED", "BLOCKED_BY_ENVIRONMENT"}:
                        continue
                    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                    failed.append(
                        {
                            "gate_id": str(row.get("id") or "unknown"),
                            "status": status,
                            "category": str(row.get("category") or "verification"),
                            "owner": str(row.get("owner") or "unassigned"),
                            "failure_kind": str(metadata.get("failure_kind") or ""),
                            "summary": redact(str(row.get("stderr") or row.get("error") or ""))[:2000],
                        }
                    )
        for line in text.splitlines():
            lower = line.casefold()
            if any(token in lower for token in ("error", "failed", "exception", "traceback", "blocked_by_environment")):
                clean = redact(line.strip())[:1000]
                if clean and clean not in excerpts:
                    excerpts.append(clean)
                    if len(excerpts) >= 40:
                        break
    return failed, excerpts


def classify(workflow: str, conclusion: str, diagnostics: str, failures: list[dict[str, Any]]) -> str:
    lower = diagnostics.casefold()
    if conclusion == "timed_out" or any(term in lower for term in TIMEOUT_TERMS):
        return "timeout"
    if conclusion == "cancelled":
        return "interrupted"
    if conclusion in {"action_required", "startup_failure"}:
        return "environment"
    if any(term in lower for term in ENVIRONMENT_TERMS):
        return "environment"
    if any(str(row.get("status") or "") in {"BLOCKED", "BLOCKED_BY_ENVIRONMENT"} for row in failures):
        return "environment"
    if failures or (workflow in WATCHED_CODE_WORKFLOWS and conclusion == "failure"):
        return "code_or_contract"
    if workflow == "wp08-full-stack-certification":
        return "production_diagnostic"
    return "unknown"


def _branch_name(workflow: str, run_id: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._/-]+", "-", f"governed-repair/{workflow}-{run_id}").strip("-./")
    return clean[:180]


def build_report(
    event: dict[str, Any],
    *,
    workspace: Path,
    artifact_files: list[tuple[Path, str]],
    changed_files: Iterable[str] = (),
    pr_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run = event.get("workflow_run") if isinstance(event.get("workflow_run"), dict) else {}
    repository = event.get("repository") if isinstance(event.get("repository"), dict) else {}
    repo = str(repository.get("full_name") or os.getenv("GITHUB_REPOSITORY") or "")
    head_repository = run.get("head_repository") if isinstance(run.get("head_repository"), dict) else {}
    head_repo = str(head_repository.get("full_name") or repo)
    workflow = str(run.get("name") or "unknown")
    conclusion = str(run.get("conclusion") or "unknown")
    run_id = str(run.get("id") or "unknown")
    attempt = str(run.get("run_attempt") or "1")
    head_sha = str(run.get("head_sha") or "")
    head_branch = str(run.get("head_branch") or "main")

    event_prs = run.get("pull_requests") if isinstance(run.get("pull_requests"), list) else []
    event_pr = event_prs[0] if event_prs and isinstance(event_prs[0], dict) else {}
    context = dict(pr_context or {})
    source_pr = int(context.get("number") or event_pr.get("number") or 0)
    event_head = event_pr.get("head") if isinstance(event_pr.get("head"), dict) else {}
    event_base = event_pr.get("base") if isinstance(event_pr.get("base"), dict) else {}
    pr_head = str(context.get("head") or event_head.get("ref") or head_branch)
    pr_base = str(context.get("base") or event_base.get("ref") or "main")

    failures, excerpts = _summaries(artifact_files)
    combined = "\n".join(text for _path, text in artifact_files)
    diagnostics = "\n".join([str(row.get("summary") or "") for row in failures] + excerpts)
    classification = classify(workflow, conclusion, diagnostics, failures)
    candidates = extract_candidate_paths(combined, workspace, changed_files)
    same_repo = bool(repo and head_repo == repo)
    has_diagnostics = bool(failures or excerpts)
    repair_allowed = bool(
        conclusion == "failure"
        and same_repo
        and classification == "code_or_contract"
        and has_diagnostics
        and candidates
    )

    if head_branch.startswith("governed-repair/"):
        repair_branch = head_branch
        repair_base = pr_base
    else:
        repair_branch = _branch_name(workflow, run_id)
        repair_base = pr_head if source_pr else "main"

    summaries = [str(row.get("summary") or "") for row in failures if row.get("summary")] or excerpts[:8]
    failure_summary = "\n".join(summaries)[:12000]
    semantic_payload = {
        "workflow": workflow,
        "classification": classification,
        "gates": failures,
        "summary": failure_summary,
    }
    semantic_signature = hashlib.sha256(
        json.dumps(semantic_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    identity_payload = dict(semantic_payload, head_sha=head_sha, run_id=run_id, run_attempt=attempt)
    failure_signature = hashlib.sha256(
        json.dumps(identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return {
        "schema": SCHEMA,
        "status": "INGESTED",
        "repository": repo,
        "workflow_name": workflow,
        "workflow_run_id": run_id,
        "workflow_run_attempt": attempt,
        "workflow_url": str(run.get("html_url") or ""),
        "conclusion": conclusion,
        "event": str(run.get("event") or ""),
        "head_sha": head_sha,
        "head_branch": head_branch,
        "head_repository": head_repo,
        "same_repository": same_repo,
        "source_pr_number": source_pr,
        "source_pr_head": pr_head,
        "source_pr_base": pr_base,
        "classification": classification,
        "has_diagnostic_evidence": has_diagnostics,
        "repair_allowed": repair_allowed,
        "automatic_repair_roots": list(AUTO_REPAIR_PREFIXES),
        "candidate_paths": candidates,
        "failed_gates": failures,
        "failure_summary": failure_summary,
        "failure_signature": failure_signature,
        "semantic_failure_signature": semantic_signature,
        "repair_branch": repair_branch,
        "repair_base_branch": repair_base,
        "production_closed": False,
    }


def _write_outputs(path: Path | None, report: dict[str, Any]) -> None:
    if path is None:
        return
    keys = (
        "repair_allowed",
        "classification",
        "repair_branch",
        "repair_base_branch",
        "head_sha",
        "head_branch",
        "source_pr_number",
        "workflow_run_id",
        "failure_signature",
    )
    with path.open("a", encoding="utf-8") as handle:
        for key in keys:
            value = report[key]
            text = "true" if value is True else "false" if value is False else str(value)
            handle.write(f"{key}={text}\n")


def _task_run(report: dict[str, Any], path: Path) -> None:
    binding = {
        "repository": report["repository"],
        "workflow_name": report["workflow_name"],
        "workflow_run_id": report["workflow_run_id"],
        "workflow_run_attempt": report["workflow_run_attempt"],
        "origin_sha": report["head_sha"],
        "failure_signature": report["failure_signature"],
    }
    task = TaskRunStore.open_or_create(
        path,
        task_id=stable_task_id("github-repair", binding),
        task_kind="github-governed-repair-cycle",
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
        workspace_fingerprint=report["head_sha"],
        evidence_refs=[str(path.with_name("failure-case.json"))],
        metadata={"classification": report["classification"], "repair_allowed": report["repair_allowed"]},
    )
    task.mark_condition("failure_ingested", evidence_refs=[str(path.with_name("failure-case.json"))])
    task.mark_condition("classification_complete", evidence_refs=[f"classification:{report['classification']}"])
    task.set_metadata(
        semantic_failure_signature=report["semantic_failure_signature"],
        repair_branch=report["repair_branch"],
        repair_base_branch=report["repair_base_branch"],
    )
    if report["repair_allowed"]:
        task.checkpoint(
            status="WAITING_EXTERNAL_RESULT",
            phase="REPAIR_READY",
            workspace_fingerprint=report["head_sha"],
            evidence_refs=[str(path.with_name("failure-case.json"))],
            metadata={"next_action": "run one isolated governed repair cycle"},
        )
    else:
        task.block(
            code="AUTOMATIC_REPAIR_NOT_AUTHORIZED",
            reason=(
                f"classification={report['classification']} same_repository={report['same_repository']} "
                f"diagnostics={report['has_diagnostic_evidence']} candidates={len(report['candidate_paths'])}"
            ),
            attempted_strategies=("workflow-run-ingest",),
            next_action="inspect the generated issue and provision environment or create an explicit governed target",
            workspace_fingerprint=report["head_sha"],
            evidence_refs=[str(path.with_name("failure-case.json"))],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--artifacts", action="append", default=[])
    parser.add_argument("--logs", action="append", default=[])
    parser.add_argument("--changed-files")
    parser.add_argument("--pr-context")
    parser.add_argument("--output", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    event = _load_json(Path(args.event))
    workspace = Path(args.workspace).resolve()
    files = _bounded_text_files([Path(value).resolve() for value in [*args.artifacts, *args.logs]])
    changed: list[str] = []
    if args.changed_files and Path(args.changed_files).is_file():
        payload = json.loads(Path(args.changed_files).read_text(encoding="utf-8"))
        if isinstance(payload, list):
            changed = [str(item) for item in payload]
    context = _load_json(Path(args.pr_context)) if args.pr_context and Path(args.pr_context).is_file() else None
    report = build_report(
        event,
        workspace=workspace,
        artifact_files=files,
        changed_files=changed,
        pr_context=context,
    )

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    task_path = Path(args.task_run).resolve()
    task_path.parent.mkdir(parents=True, exist_ok=True)
    _task_run(report, task_path)
    _write_outputs(Path(args.github_output).resolve() if args.github_output else None, report)
    print(json.dumps({key: report[key] for key in ("status", "classification", "repair_allowed", "workflow_run_id")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
