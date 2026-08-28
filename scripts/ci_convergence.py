#!/usr/bin/env python3
"""Reduce exact pull-request workflow runs into one deterministic CI status."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


SCHEMA = "ci-convergence@1"
REQUIRED_WORKFLOWS = ("quality", "skill-self-validation")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


class CIConvergenceError(ValueError):
    """Raised when workflow evidence cannot be reduced safely."""


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise CIConvergenceError(f"{field} must be a positive integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise CIConvergenceError(f"{field} must be a positive integer") from exc
    if parsed < 1 or str(parsed) != str(value):
        raise CIConvergenceError(f"{field} must be a positive integer")
    return parsed


def _sha(value: object, *, field: str) -> str:
    normalized = str(value or "")
    if not _SHA40.fullmatch(normalized):
        raise CIConvergenceError(f"{field} must be a 40-character lowercase SHA")
    return normalized


def _pull_request_numbers(row: Mapping[str, Any]) -> tuple[int, ...]:
    pull_requests = row.get("pull_requests")
    if not isinstance(pull_requests, list):
        return ()
    numbers: list[int] = []
    for pull_request in pull_requests:
        if not isinstance(pull_request, Mapping):
            continue
        number = pull_request.get("number")
        try:
            parsed = _positive_int(number, field="pull request number")
        except CIConvergenceError:
            continue
        numbers.append(parsed)
    return tuple(sorted(set(numbers)))


def _matching_runs(
    rows: Sequence[Mapping[str, Any]],
    *,
    workflow: str,
    head_sha: str,
    pull_request_number: int,
) -> list[Mapping[str, Any]]:
    return [
        row
        for row in rows
        if row.get("name") == workflow
        and row.get("event") == "pull_request"
        and row.get("head_sha") == head_sha
        and pull_request_number in _pull_request_numbers(row)
    ]


def _check_for_runs(
    workflow: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    head_sha: str,
    pull_request_number: int,
    trigger_run_id: int,
    trigger_run_attempt: int,
    trigger_workflow: str,
) -> dict[str, Any]:
    matches = _matching_runs(
        rows,
        workflow=workflow,
        head_sha=head_sha,
        pull_request_number=pull_request_number,
    )
    by_id: dict[int, list[Mapping[str, Any]]] = {}
    for row in matches:
        run_id = _positive_int(row.get("id"), field=f"{workflow} run id")
        by_id.setdefault(run_id, []).append(row)

    if workflow == trigger_workflow:
        exact_trigger = [
            row
            for row in by_id.get(trigger_run_id, [])
            if _positive_int(
                row.get("run_attempt"), field=f"{workflow} run attempt"
            )
            == trigger_run_attempt
        ]
        if len(exact_trigger) != 1:
            return {
                "workflow": workflow,
                "status": "BLOCKED",
                "reason": "trigger_run_not_exactly_bound",
                "run_id": trigger_run_id,
                "run_attempt": trigger_run_attempt,
            }

    if not by_id:
        return {
            "workflow": workflow,
            "status": "PENDING",
            "reason": "run_not_terminal_or_not_started",
        }
    if len(by_id) != 1:
        return {
            "workflow": workflow,
            "status": "BLOCKED",
            "reason": "multiple_distinct_runs_for_exact_head",
            "run_ids": sorted(by_id),
        }

    run_id, attempts = next(iter(by_id.items()))
    if len(attempts) != 1:
        return {
            "workflow": workflow,
            "status": "BLOCKED",
            "reason": "multiple_run_records_for_exact_attempt",
            "run_id": run_id,
        }
    row = attempts[0]
    run_attempt = _positive_int(row.get("run_attempt"), field=f"{workflow} run attempt")
    run_status = str(row.get("status") or "")
    conclusion = row.get("conclusion")
    if run_status != "completed":
        return {
            "workflow": workflow,
            "status": "PENDING",
            "reason": "run_not_terminal_or_not_started",
            "run_id": run_id,
            "run_attempt": run_attempt,
            "run_status": run_status,
        }
    if conclusion == "success":
        result = "PASS"
    else:
        result = "FAIL"
    return {
        "workflow": workflow,
        "status": result,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "run_status": run_status,
        "conclusion": conclusion,
    }


def reduce_ci_convergence(
    workflow_runs: Sequence[Mapping[str, Any]],
    *,
    head_sha: object,
    pull_request_number: object,
    trigger_run_id: object,
    trigger_run_attempt: object,
    trigger_workflow: object,
) -> dict[str, Any]:
    """Reduce only caller-supplied exact workflow-run records."""

    exact_sha = _sha(head_sha, field="head_sha")
    pr_number = _positive_int(pull_request_number, field="pull_request_number")
    exact_trigger_id = _positive_int(trigger_run_id, field="trigger_run_id")
    exact_trigger_attempt = _positive_int(
        trigger_run_attempt, field="trigger_run_attempt"
    )
    exact_trigger_workflow = str(trigger_workflow or "")
    if exact_trigger_workflow not in REQUIRED_WORKFLOWS:
        raise CIConvergenceError("trigger_workflow is not required")
    rows = [row for row in workflow_runs if isinstance(row, Mapping)]
    checks = {
        workflow: _check_for_runs(
            workflow,
            rows,
            head_sha=exact_sha,
            pull_request_number=pr_number,
            trigger_run_id=exact_trigger_id,
            trigger_run_attempt=exact_trigger_attempt,
            trigger_workflow=exact_trigger_workflow,
        )
        for workflow in REQUIRED_WORKFLOWS
    }
    statuses = {check["status"] for check in checks.values()}
    if "BLOCKED" in statuses:
        status = "BLOCKED"
    elif "FAIL" in statuses:
        status = "FAIL"
    elif "PENDING" in statuses:
        status = "PENDING"
    else:
        status = "PASS"
    reasons = sorted(
        f"{workflow}:{check['reason']}"
        for workflow, check in checks.items()
        if check.get("reason")
    )
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "head_sha": exact_sha,
        "pull_request_number": pr_number,
        "status": status,
        "checks": checks,
        "reasons": reasons,
    }
    canonical = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    result["convergence_digest"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-runs", required=True, type=Path)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--pull-request-number", required=True)
    parser.add_argument("--trigger-run-id", required=True)
    parser.add_argument("--trigger-run-attempt", required=True)
    parser.add_argument("--trigger-workflow", required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = json.loads(args.workflow_runs.read_text(encoding="utf-8"))
        rows = payload.get("workflow_runs") if isinstance(payload, Mapping) else payload
        if not isinstance(rows, list):
            raise CIConvergenceError("workflow-runs input must contain a list")
        result = reduce_ci_convergence(
            rows,
            head_sha=args.head_sha,
            pull_request_number=args.pull_request_number,
            trigger_run_id=args.trigger_run_id,
            trigger_run_attempt=args.trigger_run_attempt,
            trigger_workflow=args.trigger_workflow,
        )
        encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, CIConvergenceError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "BLOCKED", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
