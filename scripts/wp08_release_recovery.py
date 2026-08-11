#!/usr/bin/env python3
"""Recover missed WP-08 events and resume approved environment blockers.

This controller is deliberately separate from the primary ReleaseRun orchestrator:
- schedule reconciliation repairs missed GitHub workflow_run delivery;
- issue-comment commands classify/resume a protected Environment blocker;
- no production secret is read or written here;
- every transition is bound to the active issue, exact run ID and exact candidate SHA.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from wp08_release_github import (  # noqa: E402
    GitHubAPI,
    GitHubCoordinatorError,
    MAIN_BRANCH,
    QUALITY_WORKFLOW_FILE,
    find_release_issue,
    persist_release_state,
)
from wp08_release_coordinator import handle_workflow_run as consume_workflow_run  # noqa: E402
from wp08_release_state import (  # noqa: E402
    CONTRACT,
    ReleaseStateError,
    STATUS_ABORTED,
    STATUS_ATTEMPT_BUDGET_EXHAUSTED,
    STATUS_AWAITING_ENVIRONMENT_CONFIGURATION,
    STATUS_AWAITING_ENVIRONMENT_RUNTIME,
    STATUS_CERTIFYING,
    STATUS_FAILED_NEEDS_CLASSIFICATION,
    STATUS_WAITING_MAIN_QUALITY,
    STATUS_WAITING_REPAIR_CI,
    append_history,
    positive_int,
    sha40,
    utc_now,
    validate_state,
)

WP08_WORKFLOW_NAME = "wp08-full-stack-certification"
_CLASSIFY_RE = re.compile(r"^/wp08\s+classify\s+environment_configuration\s+run=([0-9]+)\s*$")
_RESUME_RE = re.compile(r"^/wp08\s+resume\s+environment_configuration\s+run=([0-9]+)\s*$")
_CLASSIFY_RUNTIME_RE = re.compile(r"^/wp08\s+classify\s+environment_runtime\s+run=([0-9]+)\s*$")
_RESUME_RUNTIME_RE = re.compile(r"^/wp08\s+resume\s+environment_runtime\s+run=([0-9]+)\s*$")
_RETIRE_EXHAUSTED_RE = re.compile(r"^/wp08\s+retire\s+attempt_budget_exhausted\s+run=([0-9]+)\s*$")


class RecoveryError(RuntimeError):
    pass


def _history(state: Mapping[str, Any], **event: Any) -> list[dict[str, Any]]:
    return append_history(state, {"at": utc_now(), **event})


def _dispatch(
    api: GitHubAPI,
    issue_number: int,
    state: Mapping[str, Any],
    *,
    reason: str,
    resume_run_id: int | None = None,
    resume_run_attempt: int = 1,
) -> dict[str, Any]:
    current = validate_state(state)
    if current["attempt"] >= current["max_attempts"]:
        exhausted = {
            **current,
            "status": STATUS_ATTEMPT_BUDGET_EXHAUSTED,
            "updated_at": utc_now(),
            "history": _history(current, event="attempt_budget_exhausted", reason=reason),
        }
        return persist_release_state(api, issue_number, exhausted)
    run_id = api.dispatch_wp08(
        candidate_sha=current["current_candidate_sha"],
        resume_run_id=resume_run_id,
        resume_run_attempt=resume_run_attempt,
    )
    updated = {
        **current,
        "status": STATUS_CERTIFYING,
        "attempt": current["attempt"] + 1,
        "current_wp08_run_id": run_id,
        "environment_runtime": None,
        "updated_at": utc_now(),
        "history": _history(
            current,
            event="wp08_dispatched",
            reason=reason,
            attempt=current["attempt"] + 1,
            candidate_sha=current["current_candidate_sha"],
            wp08_run_id=run_id,
            **({"resume_from_wp08_run_id": resume_run_id} if resume_run_id is not None else {}),
        ),
    }
    return persist_release_state(api, issue_number, updated)


def _validate_wp08_run(
    api: GitHubAPI,
    state: Mapping[str, Any],
    run_id: int,
    *,
    require_completed: bool = True,
    expected_candidate_sha: str | None = None,
) -> dict[str, Any]:
    current = validate_state(state)
    expected_sha = sha40(
        expected_candidate_sha or current["current_candidate_sha"],
        name="expected WP-08 candidate SHA",
    )
    run = api.get_workflow_run(run_id)
    if str(run.get("name") or "") != WP08_WORKFLOW_NAME:
        raise RecoveryError("referenced run is not wp08-full-stack-certification")
    if str(run.get("event") or "") != "workflow_dispatch":
        raise RecoveryError("referenced WP-08 run is not a workflow_dispatch run")
    if str(run.get("head_branch") or "") != MAIN_BRANCH:
        raise RecoveryError("referenced WP-08 run did not target main")
    if sha40(run.get("head_sha"), name="WP-08 head_sha") != expected_sha:
        raise RecoveryError("referenced WP-08 run does not match expected candidate SHA")
    if require_completed and str(run.get("status") or "") != "completed":
        raise RecoveryError("referenced WP-08 run is not completed")
    return run



def _replay_workflow_run(api: GitHubAPI, run: Mapping[str, Any]) -> None:
    """Replay authoritative GitHub run evidence through coordinator authority."""
    consume_workflow_run(api, {"workflow_run": dict(run)}, source="reconcile")


def _latest_completed_quality_run(
    api: GitHubAPI,
    state: Mapping[str, Any],
) -> dict[str, Any] | None:
    current = validate_state(state)
    matching = [
        row
        for row in api.list_workflow_runs(QUALITY_WORKFLOW_FILE, event="push")
        if str(row.get("head_branch") or "") == MAIN_BRANCH
        and str(row.get("head_sha") or "").casefold() == current["current_candidate_sha"]
        and str(row.get("status") or "") == "completed"
    ]
    if not matching:
        return None
    return dict(max(matching, key=lambda row: int(row.get("id") or 0)))


def reconcile(api: GitHubAPI) -> None:
    """Observe missed terminal evidence and replay it; never decide state here."""
    found = find_release_issue(api)
    if found is None:
        return
    _, state = found
    current = validate_state(state)
    if current["status"] == STATUS_CERTIFYING:
        run_id = positive_int(current.get("current_wp08_run_id"), name="current_wp08_run_id")
        run = _validate_wp08_run(api, current, run_id, require_completed=False)
        if str(run.get("status") or "") == "completed":
            _replay_workflow_run(api, run)
        return
    if current["status"] in {STATUS_WAITING_MAIN_QUALITY, STATUS_WAITING_REPAIR_CI}:
        run = _latest_completed_quality_run(api, current)
        if run is not None:
            _replay_workflow_run(api, run)

def _authorized_commenter(state: Mapping[str, Any], event: Mapping[str, Any], repository: str) -> str:
    comment = event.get("comment") if isinstance(event.get("comment"), Mapping) else {}
    user = comment.get("user") if isinstance(comment.get("user"), Mapping) else {}
    actor = str(user.get("login") or "")
    owner = repository.split("/", 1)[0]
    if actor not in {str(state.get("authorized_by") or ""), owner}:
        raise RecoveryError("issue-comment actor is not authorized for this ReleaseRun")
    return actor


def handle_issue_comment(api: GitHubAPI, event: Mapping[str, Any]) -> None:
    issue = event.get("issue") if isinstance(event.get("issue"), Mapping) else {}
    if not issue or "pull_request" in issue:
        return
    found = find_release_issue(api)
    if found is None:
        return
    issue_number, state = found
    if positive_int(issue.get("number"), name="issue number") != issue_number:
        return
    current = validate_state(state)
    actor = _authorized_commenter(current, event, api.repository)
    comment = event.get("comment") if isinstance(event.get("comment"), Mapping) else {}
    body = str(comment.get("body") or "").strip()


    retire_exhausted = _RETIRE_EXHAUSTED_RE.fullmatch(body)
    if retire_exhausted:
        run_id = positive_int(retire_exhausted.group(1), name="retired WP-08 run id")
        if int(current.get("current_wp08_run_id") or 0) != run_id:
            raise RecoveryError("retirement run ID does not match active WP-08 run")
        if current["attempt"] != current["max_attempts"]:
            raise RecoveryError("ReleaseRun attempt budget is not exhausted")
        if current["status"] not in {
            STATUS_FAILED_NEEDS_CLASSIFICATION,
            STATUS_ATTEMPT_BUDGET_EXHAUSTED,
        }:
            raise RecoveryError("ReleaseRun is not eligible for exhausted-budget retirement")
        run = _validate_wp08_run(api, current, run_id)
        conclusion = str(run.get("conclusion") or "")
        if conclusion not in {"failure", "cancelled", "timed_out", "stale"}:
            raise RecoveryError("retirement requires a terminal failure-like WP-08 run")
        retired = {
            **current,
            "status": STATUS_ABORTED,
            "updated_at": utc_now(),
            "history": _history(
                current,
                event="release_run_retired_attempt_budget_exhausted",
                wp08_run_id=run_id,
                conclusion=conclusion,
                actor=actor,
            ),
        }
        persist_release_state(api, issue_number, retired, close=True)
        return

    classify_runtime = _CLASSIFY_RUNTIME_RE.fullmatch(body)
    if classify_runtime:
        run_id = positive_int(classify_runtime.group(1), name="classified WP-08 run id")
        if int(current.get("current_wp08_run_id") or 0) != run_id:
            raise RecoveryError("classification run ID does not match active WP-08 run")
        if current["status"] not in {STATUS_CERTIFYING, STATUS_FAILED_NEEDS_CLASSIFICATION}:
            raise RecoveryError("ReleaseRun is not awaiting WP-08 failure classification")
        run = _validate_wp08_run(api, current, run_id)
        if str(run.get("conclusion") or "") != "failure":
            raise RecoveryError("environment runtime classification requires a failed WP-08 run")
        updated = {
            **current,
            "status": STATUS_AWAITING_ENVIRONMENT_RUNTIME,
            "last_wp08_run_id": run_id,
            "last_wp08_conclusion": "failure",
            "failure_signature": "environment_runtime:configured_model_provider_unavailable",
            "environment_runtime": {
                "source_wp08_run_id": run_id,
                "source_candidate_sha": current["current_candidate_sha"],
                "resume_checkpoint": True,
                "reason": "configured_model_provider_unavailable",
            },
            "updated_at": utc_now(),
            "history": _history(
                current,
                event="wp08_classified_environment_runtime",
                wp08_run_id=run_id,
                actor=actor,
            ),
        }
        persist_release_state(api, issue_number, updated)
        return

    resume_runtime = _RESUME_RUNTIME_RE.fullmatch(body)
    if resume_runtime:
        run_id = positive_int(resume_runtime.group(1), name="resume WP-08 run id")
        if current["status"] != STATUS_AWAITING_ENVIRONMENT_RUNTIME:
            raise RecoveryError("ReleaseRun is not awaiting configured model runtime recovery")
        runtime_gate = current.get("environment_runtime") if isinstance(current.get("environment_runtime"), Mapping) else {}
        source_run_id = positive_int(runtime_gate.get("source_wp08_run_id"), name="environment runtime source run id")
        if run_id != source_run_id:
            raise RecoveryError("resume run ID does not match active environment runtime blocker")
        source_candidate_sha = sha40(
            runtime_gate.get("source_candidate_sha"),
            name="environment runtime source candidate SHA",
        )
        run = _validate_wp08_run(
            api,
            current,
            run_id,
            expected_candidate_sha=source_candidate_sha,
        )
        if str(run.get("conclusion") or "") != "failure":
            raise RecoveryError("environment runtime resume requires the failed blocker run")
        resume_checkpoint = bool(runtime_gate.get("resume_checkpoint"))
        if resume_checkpoint:
            if source_candidate_sha != current["current_candidate_sha"]:
                raise RecoveryError("checkpoint resume cannot cross candidate source identity")
            _dispatch(
                api,
                issue_number,
                current,
                reason="environment_runtime_recovered",
                resume_run_id=run_id,
                resume_run_attempt=positive_int(run.get("run_attempt", 1), name="WP-08 run attempt"),
            )
        else:
            _dispatch(
                api,
                issue_number,
                current,
                reason="environment_runtime_recovered_after_repair",
            )
        return

    classify = _CLASSIFY_RE.fullmatch(body)
    if classify:
        run_id = positive_int(classify.group(1), name="classified WP-08 run id")
        if int(current.get("current_wp08_run_id") or 0) != run_id:
            raise RecoveryError("classification run ID does not match active WP-08 run")
        if current["status"] not in {STATUS_CERTIFYING, STATUS_FAILED_NEEDS_CLASSIFICATION}:
            raise RecoveryError("ReleaseRun is not awaiting WP-08 failure classification")
        run = _validate_wp08_run(api, current, run_id)
        if str(run.get("conclusion") or "") != "failure":
            raise RecoveryError("environment classification requires a failed WP-08 run")
        updated = {
            **current,
            "status": STATUS_AWAITING_ENVIRONMENT_CONFIGURATION,
            "last_wp08_run_id": run_id,
            "last_wp08_conclusion": "failure",
            "failure_signature": "environment_configuration:missing_or_invalid_protected_environment_configuration",
            "updated_at": utc_now(),
            "history": _history(
                current,
                event="wp08_classified_environment_configuration",
                wp08_run_id=run_id,
                actor=actor,
            ),
        }
        persist_release_state(api, issue_number, updated)
        return

    resume = _RESUME_RE.fullmatch(body)
    if resume:
        run_id = positive_int(resume.group(1), name="resume WP-08 run id")
        if current["status"] != STATUS_AWAITING_ENVIRONMENT_CONFIGURATION:
            raise RecoveryError("ReleaseRun is not awaiting protected Environment configuration")
        if int(current.get("current_wp08_run_id") or 0) != run_id:
            raise RecoveryError("resume run ID does not match active environment blocker")
        run = _validate_wp08_run(api, current, run_id)
        if str(run.get("conclusion") or "") != "failure":
            raise RecoveryError("environment resume requires the failed blocker run")
        _dispatch(
            api,
            issue_number,
            current,
            reason="environment_configuration_updated",
            resume_run_id=run_id,
            resume_run_attempt=positive_int(run.get("run_attempt", 1), name="WP-08 run attempt"),
        )


def _load_event(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RecoveryError("event payload must be a JSON object")
    return payload


def _api_from_env(env: Mapping[str, str]) -> GitHubAPI:
    return GitHubAPI(
        str(env.get("GITHUB_REPOSITORY") or ""),
        str(env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or ""),
        api_url=str(env.get("GITHUB_API_URL") or "https://api.github.com"),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("reconcile", "issue-comment"))
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH"))
    args = parser.parse_args()
    env = dict(os.environ)
    try:
        api = _api_from_env(env)
        if args.mode == "reconcile":
            reconcile(api)
        else:
            handle_issue_comment(api, _load_event(args.event_path))
        return 0
    except (RecoveryError, GitHubCoordinatorError, ReleaseStateError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "contract": CONTRACT,
            "status": "FAIL",
            "reason": exc.__class__.__name__,
            "error": str(exc),
            "production_closed": False,
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
