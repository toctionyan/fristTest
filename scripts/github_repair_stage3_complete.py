#!/usr/bin/env python3
"""Close Stage-3 only after the published candidate's required PR CI is green.

Draft publication is not completion.  The published candidate SHA is frozen first,
then this trusted controller waits for the exact-head pull-request `quality` and
`skill-self-validation` workflows.  A failed required workflow is a retryable
condition; it never marks the TaskRun complete.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from task_run import TaskRunStore  # type: ignore  # noqa: E402

REQUIRED_PR_WORKFLOWS = ("quality", "skill-self-validation")
ALLOWED_TERMINAL_EXIT_REASONS = {
    "VERIFIED_GREEN",
    "ATTEMPT_BUDGET_EXHAUSTED",
    "ENVIRONMENT_BLOCKED",
    "HUMAN_DECISION_REQUIRED",
}


class CompletionError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CompletionError(f"JSON object required: {path}")
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validate_publication_inputs(
    validation: dict[str, Any],
    publication: dict[str, Any],
    pr_url: str,
) -> None:
    if validation.get("schema") != "github-governed-repair-stage3@1":
        raise CompletionError("unsupported Stage-3 validation schema")
    if validation.get("status") != "VALIDATED_FOR_DRAFT_PR":
        raise CompletionError("Stage-3 validation result is not publishable")
    if validation.get("full_validation_passed") is not True:
        raise CompletionError("full validation did not pass")
    if validation.get("draft_pr_published") is not False:
        raise CompletionError("Draft PR publication was already asserted")
    if validation.get("production_closed") is not False:
        raise CompletionError("invalid production closure authority")
    if publication.get("schema") != "github-governed-repair-stage3-publication@1":
        raise CompletionError("unsupported publication commit schema")
    if publication.get("status") != "PUBLICATION_COMMIT_PREPARED":
        raise CompletionError("publication commit was not prepared")
    if publication.get("full_validation_passed") is not True:
        raise CompletionError("publication commit is not bound to full validation")
    if publication.get("draft_pr_published") is not False:
        raise CompletionError("publication commit already asserts a Draft PR")
    if publication.get("production_closed") is not False:
        raise CompletionError("invalid publication closure authority")
    if not pr_url.startswith("https://github.com/") or "/pull/" not in pr_url:
        raise CompletionError("a valid GitHub Draft PR URL is required")

    expected = {
        "source_run_id": validation.get("source_run_id"),
        "source_head_sha": validation.get("head_sha"),
        "validated_candidate_sha": validation.get("candidate_sha"),
        "repair_branch": validation.get("repair_branch"),
        "repair_base_branch": validation.get("repair_base_branch"),
        "changed_paths": validation.get("changed_paths"),
    }
    mismatched = [key for key, value in expected.items() if publication.get(key) != value]
    if mismatched:
        raise CompletionError(f"validation/publication binding mismatch: {mismatched}")


def evaluate_pr_ci_runs(
    runs: Iterable[dict[str, Any]],
    *,
    candidate_sha: str,
    required_workflows: Iterable[str] = REQUIRED_PR_WORKFLOWS,
) -> dict[str, Any]:
    """Evaluate only the newest exact-head pull-request run for each required workflow."""

    required = tuple(str(item) for item in required_workflows)
    selected: dict[str, dict[str, Any]] = {}
    for raw in runs:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("head_sha") or "") != candidate_sha:
            continue
        # Push/workflow_dispatch runs are useful diagnostics but cannot satisfy
        # the PR closure gate.  This prevents a later manual run from masking a
        # failed pull_request check on the same commit.
        if str(raw.get("event") or "") != "pull_request":
            continue
        name = str(raw.get("name") or "")
        if name not in required:
            continue
        current = selected.get(name)
        raw_id = int(raw.get("id") or 0)
        current_id = int(current.get("id") or 0) if isinstance(current, dict) else -1
        if current is None or raw_id > current_id:
            selected[name] = raw

    evidence: dict[str, Any] = {}
    missing: list[str] = []
    pending: list[str] = []
    failed: list[str] = []
    for name in required:
        run = selected.get(name)
        if run is None:
            missing.append(name)
            continue
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        evidence[name] = {
            "run_id": int(run.get("id") or 0),
            "status": status,
            "conclusion": conclusion,
            "head_sha": str(run.get("head_sha") or ""),
            "event": str(run.get("event") or ""),
            "html_url": str(run.get("html_url") or ""),
        }
        if status != "completed":
            pending.append(name)
        elif conclusion != "success":
            failed.append(name)

    if failed:
        return {
            "status": "PR_CI_FAILED_RETRYABLE",
            "closure_eligible": False,
            "continue_repair": True,
            "exit_reason": None,
            "missing": missing,
            "pending": pending,
            "failed": failed,
            "required_checks": evidence,
            "production_closed": False,
        }
    if missing or pending:
        return {
            "status": "AWAITING_PR_CI",
            "closure_eligible": False,
            "continue_repair": False,
            "exit_reason": None,
            "missing": missing,
            "pending": pending,
            "failed": [],
            "required_checks": evidence,
            "production_closed": False,
        }
    return {
        "status": "VERIFIED_GREEN",
        "closure_eligible": True,
        "continue_repair": False,
        "exit_reason": "VERIFIED_GREEN",
        "missing": [],
        "pending": [],
        "failed": [],
        "required_checks": evidence,
        "production_closed": False,
    }


def _gh_runs(repository: str, candidate_sha: str) -> list[dict[str, Any]]:
    if not repository or "/" not in repository:
        raise CompletionError("GITHUB_REPOSITORY is required for exact-head PR CI closure")
    completed = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repository}/actions/runs?head_sha={candidate_sha}&per_page=100",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if completed.returncode:
        raise CompletionError((completed.stderr or completed.stdout or "gh api failed").strip())
    payload = json.loads(completed.stdout)
    rows = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise CompletionError("GitHub Actions run query returned no workflow_runs array")
    return [row for row in rows if isinstance(row, dict)]


def wait_for_pr_ci(
    *,
    repository: str,
    candidate_sha: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while True:
        last = evaluate_pr_ci_runs(
            _gh_runs(repository, candidate_sha),
            candidate_sha=candidate_sha,
        )
        print(
            json.dumps(
                {
                    "repair_closure": last["status"],
                    "candidate_sha": candidate_sha,
                    "missing": last["missing"],
                    "pending": last["pending"],
                    "failed": last["failed"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if last["status"] in {"VERIFIED_GREEN", "PR_CI_FAILED_RETRYABLE"}:
            return last
        if time.monotonic() >= deadline:
            return {
                **last,
                "status": "ENVIRONMENT_BLOCKED",
                "closure_eligible": False,
                "continue_repair": False,
                "exit_reason": "ENVIRONMENT_BLOCKED",
                "reason": "required exact-head PR CI did not reach a terminal state before timeout",
            }
        time.sleep(max(1, poll_seconds))


def complete_publication(
    *,
    validation_result_path: Path,
    publication_commit_path: Path,
    task_run_path: Path,
    pr_url: str,
    output_path: Path,
    ci_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Finalize only with exact-head green evidence.

    Without `ci_evidence`, publication is recorded as waiting and the final TaskRun
    condition deliberately remains unsatisfied.  `TaskRun.complete()` is therefore
    mechanically impossible until both required pull-request workflows are green.
    """

    validation = _load(validation_result_path)
    publication = _load(publication_commit_path)
    _validate_publication_inputs(validation, publication, pr_url)

    task_payload = _load(task_run_path)
    task = TaskRunStore(task_run_path.resolve(), task_payload)
    if task.payload.get("status") not in {"WAITING_EXTERNAL_RESULT", "VALIDATING"}:
        raise CompletionError("TaskRun is not waiting for Stage-3 publication/PR CI")
    if task.payload.get("phase") not in {
        "STAGE3_DRAFT_PR_REQUIRED",
        "STAGE3_PR_CI_REQUIRED",
    }:
        raise CompletionError("TaskRun phase is not a Stage-3 publication/PR CI phase")

    validated_candidate_sha = str(validation.get("candidate_sha") or "")
    published_candidate_sha = str(publication.get("published_candidate_sha") or "")
    validated_tree_sha = str(publication.get("validated_tree_sha") or "")
    snapshot = str(validation.get("quick_workspace_snapshot_fingerprint") or "")
    if not validated_candidate_sha or not published_candidate_sha or not validated_tree_sha or not snapshot:
        raise CompletionError("Stage-3 evidence lacks candidate/tree identity")

    if ci_evidence is None:
        if task.payload.get("phase") == "STAGE3_DRAFT_PR_REQUIRED":
            task.checkpoint(
                status="WAITING_EXTERNAL_RESULT",
                phase="STAGE3_PR_CI_REQUIRED",
                workspace_fingerprint=snapshot,
                evidence_refs=[pr_url, str(validation_result_path), str(publication_commit_path)],
                metadata={
                    "draft_pr_url": pr_url,
                    "validated_candidate_sha": validated_candidate_sha,
                    "published_candidate_sha": published_candidate_sha,
                    "validated_tree_sha": validated_tree_sha,
                    "next_action": "wait for exact-head pull-request quality and skill-self-validation",
                },
            )
        pending = dict(validation)
        pending.update(
            {
                "status": "AWAITING_PR_CI",
                "validated_candidate_sha": validated_candidate_sha,
                "published_candidate_sha": published_candidate_sha,
                "validated_tree_sha": validated_tree_sha,
                "draft_pr_published": True,
                "draft_pr_url": pr_url,
                "closure_eligible": False,
                "exit_reason": None,
                "production_closed": False,
            }
        )
        _write(output_path, pending)
        return pending

    if ci_evidence.get("status") != "VERIFIED_GREEN" or ci_evidence.get("closure_eligible") is not True:
        raise CompletionError("TaskRun completion requires VERIFIED_GREEN exact-head PR CI evidence")
    if ci_evidence.get("exit_reason") not in ALLOWED_TERMINAL_EXIT_REASONS:
        raise CompletionError("invalid repair-loop terminal exit reason")
    checks = ci_evidence.get("required_checks")
    if not isinstance(checks, dict) or set(checks) != set(REQUIRED_PR_WORKFLOWS):
        raise CompletionError("required PR CI evidence is incomplete")
    ci_refs = []
    for name in REQUIRED_PR_WORKFLOWS:
        row = checks.get(name) if isinstance(checks.get(name), dict) else {}
        if row.get("head_sha") != published_candidate_sha:
            raise CompletionError(f"{name} CI evidence is not bound to published candidate SHA")
        if row.get("event") != "pull_request":
            raise CompletionError(f"{name} CI evidence is not a pull-request run")
        if row.get("status") != "completed" or row.get("conclusion") != "success":
            raise CompletionError(f"{name} CI evidence is not terminal green")
        url = str(row.get("html_url") or "")
        run_id = str(row.get("run_id") or "")
        ci_refs.append(url or f"github-actions:{name}:{run_id}")

    # Reuse the historical condition name for compatibility, but change its
    # semantics to the stronger atomic condition: Draft PR published AND exact-head
    # required PR CI verified.  Publication alone can no longer complete the TaskRun.
    if task.payload.get("conditions", {}).get("draft_pr_published", {}).get("satisfied") is not True:
        task.mark_condition(
            "draft_pr_published",
            evidence_refs=[
                pr_url,
                f"published-candidate-sha:{published_candidate_sha}",
                f"validated-tree-sha:{validated_tree_sha}",
                *ci_refs,
            ],
        )
    task.checkpoint(
        status="VALIDATING",
        phase="STAGE3_PR_CI_VERIFIED",
        workspace_fingerprint=snapshot,
        evidence_refs=[pr_url, *ci_refs],
        metadata={
            "draft_pr_url": pr_url,
            "published_candidate_sha": published_candidate_sha,
            "required_workflows": list(REQUIRED_PR_WORKFLOWS),
            "closure_eligible": True,
        },
    )
    task.complete(
        workspace_fingerprint=snapshot,
        evidence_refs=[str(validation_result_path), str(publication_commit_path), pr_url, *ci_refs],
    )

    completed = dict(validation)
    completed.update(
        {
            "status": "VERIFIED_GREEN",
            "validated_candidate_sha": validated_candidate_sha,
            "published_candidate_sha": published_candidate_sha,
            "validated_tree_sha": validated_tree_sha,
            "draft_pr_published": True,
            "draft_pr_url": pr_url,
            "required_checks": checks,
            "closure_eligible": True,
            "exit_reason": "VERIFIED_GREEN",
            "production_closed": False,
        }
    )
    _write(output_path, completed)
    return completed


def _write_failed_closure(
    *,
    validation_result_path: Path,
    publication_commit_path: Path,
    pr_url: str,
    output_path: Path,
    decision: dict[str, Any],
) -> None:
    validation = _load(validation_result_path)
    publication = _load(publication_commit_path)
    payload = dict(validation)
    payload.update(
        {
            "status": decision["status"],
            "published_candidate_sha": publication.get("published_candidate_sha"),
            "draft_pr_published": True,
            "draft_pr_url": pr_url,
            "required_checks": decision.get("required_checks", {}),
            "closure_eligible": False,
            "continue_repair": bool(decision.get("continue_repair")),
            "exit_reason": decision.get("exit_reason"),
            "production_closed": False,
        }
    )
    _write(output_path, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-result", required=True)
    parser.add_argument("--publication-commit", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--pr-url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--ci-timeout-seconds",
        type=int,
        default=int(os.getenv("GOVERNED_REPAIR_PR_CI_TIMEOUT_SECONDS", "3000")),
    )
    parser.add_argument(
        "--ci-poll-seconds",
        type=int,
        default=int(os.getenv("GOVERNED_REPAIR_PR_CI_POLL_SECONDS", "15")),
    )
    args = parser.parse_args()
    validation_path = Path(args.validation_result)
    publication_path = Path(args.publication_commit)
    task_path = Path(args.task_run)
    output_path = Path(args.output)
    try:
        publication = _load(publication_path)
        validation = _load(validation_path)
        _validate_publication_inputs(validation, publication, args.pr_url)
        pending = complete_publication(
            validation_result_path=validation_path,
            publication_commit_path=publication_path,
            task_run_path=task_path,
            pr_url=args.pr_url,
            output_path=output_path,
            ci_evidence=None,
        )
        candidate_sha = str(pending.get("published_candidate_sha") or "")
        decision = wait_for_pr_ci(
            repository=str(os.getenv("GITHUB_REPOSITORY") or ""),
            candidate_sha=candidate_sha,
            timeout_seconds=max(1, args.ci_timeout_seconds),
            poll_seconds=max(1, args.ci_poll_seconds),
        )
        if decision["status"] != "VERIFIED_GREEN":
            _write_failed_closure(
                validation_result_path=validation_path,
                publication_commit_path=publication_path,
                pr_url=args.pr_url,
                output_path=output_path,
                decision=decision,
            )
            print(json.dumps(decision, sort_keys=True), file=sys.stderr)
            return 3
        complete_publication(
            validation_result_path=validation_path,
            publication_commit_path=publication_path,
            task_run_path=task_path,
            pr_url=args.pr_url,
            output_path=output_path,
            ci_evidence=decision,
        )
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError, CompletionError) as exc:
        print(json.dumps({"status": "HUMAN_DECISION_REQUIRED", "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
