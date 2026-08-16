#!/usr/bin/env python3
from __future__ import annotations

"""Finalize G6 only from baseline-accepted exact-head CI evidence.

This controller cannot merge or deploy.  It validates the governed baseline
acceptance receipt, requires the exact baseline commit to be the PR head, requires
mandatory PR-triggered workflows for that exact pull request to have succeeded on
that exact SHA, marks the final TaskRun conditions, and emits READY_FOR_REVIEW. A
caller may then convert the Draft PR to Ready for review and nothing more.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from task_run import TaskRunStore  # type: ignore  # noqa: E402

BASELINE_SCHEMA = "governed-baseline-acceptance@1"
EXACT_HEAD_SCHEMA = "governed-repair-exact-head@1"
MANDATORY_WORKFLOWS = ("skill-self-validation", "quality")


class ExactHeadError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ExactHeadError(f"JSON object required: {path}")
    return payload


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _without(payload: dict[str, Any], field: str) -> dict[str, Any]:
    result = dict(payload)
    result.pop(field, None)
    return result


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _pr_number_from_url(raw: object) -> int:
    value = str(raw or "").strip().rstrip("/")
    match = re.search(r"/pull/(\d+)$", value)
    if match is None:
        raise ExactHeadError("Draft PR URL does not contain a valid pull request number")
    number = int(match.group(1))
    if number <= 0:
        raise ExactHeadError("Draft PR number is invalid")
    return number


def finalize_exact_head(
    *,
    baseline_receipt_path: Path,
    ci_evidence_path: Path,
    task_run_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    baseline = _load(baseline_receipt_path)
    ci = _load(ci_evidence_path)
    if baseline.get("schema") != BASELINE_SCHEMA:
        raise ExactHeadError("unsupported baseline-acceptance receipt")
    if baseline.get("status") != "BASELINE_ACCEPTED":
        raise ExactHeadError("protected baseline was not accepted")
    if baseline.get("governance_closed") is not True:
        raise ExactHeadError("governance is not closed")
    if baseline.get("baseline_accepted") is not True:
        raise ExactHeadError("baseline_accepted is not true")
    if baseline.get("exact_head_certified") is not False:
        raise ExactHeadError("exact head was already certified")
    if baseline.get("ready_for_review") is not False:
        raise ExactHeadError("repair was already marked ready")
    if baseline.get("merge_allowed") is not False or baseline.get("deploy_allowed") is not False:
        raise ExactHeadError("baseline receipt illegally enabled merge/deploy")
    if baseline.get("production_closed") is not False:
        raise ExactHeadError("baseline receipt illegally closed production")
    expected_baseline_sha = _fingerprint(_without(baseline, "baseline_acceptance_sha256"))
    if str(baseline.get("baseline_acceptance_sha256") or "") != expected_baseline_sha:
        raise ExactHeadError("baseline acceptance fingerprint mismatch")

    exact_sha = str(baseline.get("baseline_commit_sha") or "")
    if len(exact_sha) != 40:
        raise ExactHeadError("baseline commit SHA is invalid")
    if ci.get("schema") != "governed-repair-exact-head-ci@1":
        raise ExactHeadError("unsupported exact-head CI evidence")
    if str(ci.get("head_sha") or "") != exact_sha:
        raise ExactHeadError("CI evidence is not bound to the baseline commit")
    draft_pr_url = str(baseline.get("draft_pr_url") or "")
    if str(ci.get("pr_url") or "") != draft_pr_url:
        raise ExactHeadError("CI evidence PR binding mismatch")
    expected_pr_number = _pr_number_from_url(draft_pr_url)
    if int(ci.get("pr_number") or 0) != expected_pr_number:
        raise ExactHeadError("CI evidence pull request number mismatch")
    if ci.get("pr_is_draft") is not True:
        raise ExactHeadError("PR must remain Draft during exact-head certification")
    if str(ci.get("pr_head_sha") or "") != exact_sha:
        raise ExactHeadError("Draft PR head is not the baseline commit")

    workflows = ci.get("workflows")
    if not isinstance(workflows, dict):
        raise ExactHeadError("exact-head workflow evidence is missing")
    for workflow in MANDATORY_WORKFLOWS:
        row = workflows.get(workflow)
        if not isinstance(row, dict):
            raise ExactHeadError(f"mandatory workflow evidence is missing: {workflow}")
        if str(row.get("head_sha") or "") != exact_sha:
            raise ExactHeadError(f"workflow {workflow} ran on the wrong SHA")
        if row.get("event") != "pull_request":
            raise ExactHeadError(f"workflow {workflow} is not a pull_request run")
        if int(row.get("pr_number") or 0) != expected_pr_number:
            raise ExactHeadError(f"workflow {workflow} ran for the wrong pull request")
        if row.get("status") != "completed" or row.get("conclusion") != "success":
            raise ExactHeadError(f"mandatory exact-head workflow did not succeed: {workflow}")
        if not str(row.get("run_id") or "").isdigit():
            raise ExactHeadError(f"workflow {workflow} lacks a valid run ID")

    gates = baseline.get("gates")
    if not isinstance(gates, dict):
        raise ExactHeadError("governed gates are missing")
    for gate in (
        "G0_SCOPE_AUTHORITY",
        "G1_CONTRACT_PROJECTION",
        "G2_SEMANTIC_INVARIANT",
        "G3_MUTATION",
        "G4_FINAL_AUTHORITY",
        "G5_INTEGRATION_CERTIFICATION",
    ):
        row = gates.get(gate)
        if not isinstance(row, dict) or row.get("status") != "PASS":
            raise ExactHeadError(f"pre-G6 gate regressed: {gate}")
    g6 = gates.get("G6_GOVERNANCE_EXACT_HEAD")
    if not isinstance(g6, dict) or g6.get("status") != "BASELINE_ACCEPTED_EXACT_HEAD_PENDING":
        raise ExactHeadError("G6 is not awaiting exact-head certification")

    final_gates = dict(gates)
    final_gates["G6_GOVERNANCE_EXACT_HEAD"] = {
        "status": "PASS",
        "evidence": [
            f"governance-sha256:{baseline.get('governance_sha256')}",
            f"baseline-acceptance-sha256:{baseline.get('baseline_acceptance_sha256')}",
            f"exact-head:{exact_sha}",
            f"pull-request:{expected_pr_number}",
            *[
                f"workflow:{name}:pull-request:{expected_pr_number}:run:{workflows[name]['run_id']}"
                for name in MANDATORY_WORKFLOWS
            ],
        ],
    }

    task_payload = _load(task_run_path)
    task = TaskRunStore(task_run_path.resolve(), task_payload)
    if task.payload.get("phase") != "STAGE6_EXACT_HEAD_CERTIFICATION_REQUIRED":
        raise ExactHeadError("TaskRun is not awaiting exact-head certification")
    task.mark_condition(
        "exact_head_certified",
        evidence_refs=[
            str(ci_evidence_path),
            f"exact-head:{exact_sha}",
            f"pull-request:{expected_pr_number}",
            *[
                f"workflow:{name}:pull-request:{expected_pr_number}:run:{workflows[name]['run_id']}"
                for name in MANDATORY_WORKFLOWS
            ],
        ],
    )
    task.mark_condition(
        "ready_for_review",
        evidence_refs=[str(ci_evidence_path), f"G6:PASS:{exact_sha}"],
    )
    task.complete(
        workspace_fingerprint=exact_sha,
        evidence_refs=[
            str(baseline_receipt_path),
            str(ci_evidence_path),
            "G0-G6:PASS",
            "merge_allowed:false",
            "deploy_allowed:false",
            "production_closed:false",
        ],
    )

    result: dict[str, Any] = {
        "schema": EXACT_HEAD_SCHEMA,
        "status": "READY_FOR_REVIEW",
        "governed_repair_state": "READY_FOR_REVIEW",
        "repository": baseline.get("repository"),
        "source_run_id": baseline.get("source_run_id"),
        "draft_pr_url": baseline.get("draft_pr_url"),
        "pull_request_number": expected_pr_number,
        "repair_branch": baseline.get("repair_branch"),
        "repair_base_branch": baseline.get("repair_base_branch"),
        "published_source_sha": baseline.get("published_source_sha"),
        "baseline_commit_sha": exact_sha,
        "rca_sha256": baseline.get("rca_sha256"),
        "write_grant_sha256": baseline.get("write_grant_sha256"),
        "required_guard_ids": list(baseline.get("required_guard_ids") or []),
        "governance_sha256": baseline.get("governance_sha256"),
        "baseline_acceptance_sha256": baseline.get("baseline_acceptance_sha256"),
        "exact_head_ci_sha256": _fingerprint(ci),
        "gates": final_gates,
        "governance_closed": True,
        "baseline_accepted": True,
        "exact_head_certified": True,
        "ready_for_review": True,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    result["exact_head_receipt_sha256"] = _fingerprint(result)
    _write(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-receipt", required=True)
    parser.add_argument("--ci-evidence", required=True)
    parser.add_argument("--task-run", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        finalize_exact_head(
            baseline_receipt_path=Path(args.baseline_receipt),
            ci_evidence_path=Path(args.ci_evidence),
            task_run_path=Path(args.task_run),
            output_path=Path(args.output),
        )
    except (OSError, json.JSONDecodeError, ExactHeadError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error": str(exc),
                    "ready_for_review": False,
                    "merge_allowed": False,
                    "deploy_allowed": False,
                    "production_closed": False,
                }
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
