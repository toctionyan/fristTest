#!/usr/bin/env python3
"""Orchestrate one durable, bounded WP-08 release authorization.

The coordinator is intentionally secret-free. It never owns model/runtime
configuration and never claims production closure. Its only responsibilities
are durable ReleaseRun state, guarded GitHub workflow dispatch, repair binding,
and bounded retry for non-semantic workflow termination.

Semantic ``failure`` never retries blindly.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

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
from wp08_release_state import (  # noqa: E402
    BOOTSTRAP_CONTRACT,
    CONTRACT,
    DEFAULT_MAX_ATTEMPTS,
    REPAIRABLE_SOURCE_STATUSES,
    RETRYABLE_WORKFLOW_CONCLUSIONS,
    ReleaseStateError,
    STATUS_ATTEMPT_BUDGET_EXHAUSTED,
    STATUS_CERTIFYING,
    STATUS_FAILED_NEEDS_CLASSIFICATION,
    STATUS_MAIN_QUALITY_FAILED,
    STATUS_WAITING_MAIN_QUALITY,
    STATUS_WAITING_REPAIR_CI,
    STATUS_WP08_PASS,
    append_history,
    new_state,
    parse_repair_markers,
    positive_int,
    release_id,
    render_issue_body,
    sha40,
    utc_now,
    validate_state,
)

WP08_WORKFLOW_NAME = "wp08-full-stack-certification"
QUALITY_WORKFLOW_NAME = "quality"
MAIN_REF = "refs/heads/main"


class CoordinatorError(RuntimeError):
    """Fail-closed orchestration error."""


def _history(state: Mapping[str, Any], **event: Any) -> list[dict[str, Any]]:
    return append_history(state, {"at": utc_now(), **event})


def _dispatch(
    api: GitHubAPI,
    issue_number: int,
    state: Mapping[str, Any],
    *,
    reason: str,
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

    run_id = api.dispatch_wp08(candidate_sha=current["current_candidate_sha"])
    updated = {
        **current,
        "status": STATUS_CERTIFYING,
        "attempt": current["attempt"] + 1,
        "current_wp08_run_id": run_id,
        "updated_at": utc_now(),
        "history": _history(
            current,
            event="wp08_dispatched",
            reason=reason,
            attempt=current["attempt"] + 1,
            candidate_sha=current["current_candidate_sha"],
            wp08_run_id=run_id,
        ),
    }
    return persist_release_state(api, issue_number, updated)


def _quality_pass_exists(api: GitHubAPI, candidate_sha: str) -> bool:
    candidate_sha = sha40(candidate_sha, name="candidate_sha")
    for row in api.list_workflow_runs(QUALITY_WORKFLOW_FILE, event="push"):
        if (
            str(row.get("head_sha") or "").casefold() == candidate_sha
            and str(row.get("head_branch") or "") == MAIN_BRANCH
            and str(row.get("conclusion") or "") == "success"
        ):
            return True
    return False


def _maybe_dispatch_after_quality(
    api: GitHubAPI,
    issue_number: int,
    state: Mapping[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    current = validate_state(state)
    if _quality_pass_exists(api, current["current_candidate_sha"]):
        return _dispatch(api, issue_number, current, reason=reason)
    return persist_release_state(api, issue_number, current)


def authorize(api: GitHubAPI, env: Mapping[str, str]) -> dict[str, Any]:
    if str(env.get("GITHUB_EVENT_NAME") or "") != "workflow_dispatch":
        raise CoordinatorError("release authorization must originate from workflow_dispatch")
    if (
        str(env.get("GITHUB_REF") or "") != MAIN_REF
        or str(env.get("GITHUB_REF_PROTECTED") or "").lower() != "true"
    ):
        raise CoordinatorError("release authorization requires protected main")
    if find_release_issue(api) is not None:
        raise CoordinatorError("another WP-08 release run is already active")

    candidate_sha = sha40(env.get("GITHUB_SHA"), name="GITHUB_SHA")
    coordinator_run = positive_int(env.get("GITHUB_RUN_ID"), name="GITHUB_RUN_ID")
    release_run_id = f"wp08-release-{coordinator_run}"
    state = new_state(
        release_run_id=release_run_id,
        authorized_initial_sha=candidate_sha,
        current_candidate_sha=candidate_sha,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        actor=str(env.get("GITHUB_ACTOR") or "unknown"),
    )
    state.update({
        "status": STATUS_WAITING_MAIN_QUALITY,
        "updated_at": utc_now(),
        "history": _history(
            state,
            event="human_release_authorized",
            candidate_sha=candidate_sha,
            coordinator_run_id=coordinator_run,
        ),
    })
    issue = api.create_issue(
        title=f"[WP08 Release] {release_run_id}",
        body=render_issue_body(state),
    )
    issue_number = positive_int(issue.get("number"), name="issue number")
    return _maybe_dispatch_after_quality(
        api,
        issue_number,
        state,
        reason="initial_authorization_main_quality_passed",
    )


def _validate_bootstrap_run(api: GitHubAPI, bootstrap: Mapping[str, Any]) -> dict[str, Any]:
    if bootstrap.get("contract") != BOOTSTRAP_CONTRACT:
        raise CoordinatorError("bootstrap contract is invalid")
    if bootstrap.get("production_closed") is not False:
        raise CoordinatorError("bootstrap cannot claim production_closed")

    run_id = positive_int(
        bootstrap.get("authorized_initial_wp08_run_id"),
        name="authorized_initial_wp08_run_id",
    )
    expected_sha = sha40(bootstrap.get("authorized_initial_sha"), name="authorized_initial_sha")
    run = api.get_workflow_run(run_id)
    if str(run.get("name") or "") != WP08_WORKFLOW_NAME:
        raise CoordinatorError("bootstrap run is not the WP-08 workflow")
    if str(run.get("event") or "") != "workflow_dispatch":
        raise CoordinatorError("bootstrap run was not manually authorized")
    if str(run.get("head_branch") or "") != MAIN_BRANCH:
        raise CoordinatorError("bootstrap run did not target main")
    if str(run.get("head_sha") or "").casefold() != expected_sha:
        raise CoordinatorError("bootstrap run SHA does not match the authorization record")
    if str(run.get("status") or "") != "completed":
        raise CoordinatorError("bootstrap WP-08 run is not completed")
    if str(run.get("conclusion") or "") not in {"failure", "cancelled", "timed_out"}:
        raise CoordinatorError("bootstrap is only valid for an unfinished failed WP-08 release")
    return run


def bootstrap(api: GitHubAPI, env: Mapping[str, str], paths: Iterable[Path]) -> None:
    current_sha = sha40(env.get("GITHUB_SHA"), name="GITHUB_SHA")
    for path in sorted(paths):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise CoordinatorError(f"bootstrap file must contain a JSON object: {path}")

        current_release_id = release_id(payload.get("release_run_id"))
        if find_release_issue(
            api,
            release_run_id=current_release_id,
            include_closed=True,
        ) is not None:
            print(json.dumps(
                {"contract": CONTRACT, "bootstrap": str(path), "status": "ALREADY_CONSUMED"},
                ensure_ascii=False,
            ))
            continue
        if find_release_issue(api) is not None:
            raise CoordinatorError("cannot bootstrap while another WP-08 release run is active")

        run = _validate_bootstrap_run(api, payload)
        actor = payload.get("authorized_by")
        if not actor and isinstance(run.get("actor"), Mapping):
            actor = run["actor"].get("login")
        state = new_state(
            release_run_id=current_release_id,
            authorized_initial_sha=sha40(
                payload.get("authorized_initial_sha"),
                name="authorized_initial_sha",
            ),
            current_candidate_sha=current_sha,
            max_attempts=positive_int(
                payload.get("max_attempts", DEFAULT_MAX_ATTEMPTS),
                name="max_attempts",
            ),
            actor=str(actor or "legacy-manual-dispatch"),
        )
        state.update({
            "status": STATUS_WAITING_MAIN_QUALITY,
            "attempt": 1,
            "current_wp08_run_id": int(run["id"]),
            "last_wp08_run_id": int(run["id"]),
            "last_wp08_conclusion": str(run.get("conclusion") or ""),
            "failure_signature": "legacy_wp08_failure_requires_continuation",
            "updated_at": utc_now(),
            "history": _history(
                state,
                event="legacy_authorization_bootstrapped",
                wp08_run_id=int(run["id"]),
                authorized_initial_sha=state["authorized_initial_sha"],
                current_candidate_sha=current_sha,
            ),
        })
        issue = api.create_issue(
            title=f"[WP08 Release] {current_release_id}",
            body=render_issue_body(state),
        )
        issue_number = positive_int(issue.get("number"), name="issue number")
        _maybe_dispatch_after_quality(
            api,
            issue_number,
            state,
            reason="bootstrap_main_quality_already_passed",
        )


def handle_pull_request(api: GitHubAPI, event: Mapping[str, Any]) -> None:
    pr = event.get("pull_request") if isinstance(event.get("pull_request"), Mapping) else {}
    if not pr or pr.get("merged") is not True:
        return
    base = pr.get("base") if isinstance(pr.get("base"), Mapping) else {}
    if str(base.get("ref") or "") != MAIN_BRANCH:
        return

    markers = parse_repair_markers(str(pr.get("body") or ""))
    if markers is None:
        return
    release_run_id, parent_run_id = markers
    found = find_release_issue(api, release_run_id=release_run_id)
    if found is None:
        raise CoordinatorError(f"repair PR references unknown active release run {release_run_id}")

    issue_number, state = found
    current = validate_state(state)
    if current["status"] not in REPAIRABLE_SOURCE_STATUSES:
        raise CoordinatorError(f"release run {release_run_id} is not waiting for a repair PR")
    if int(current.get("current_wp08_run_id") or 0) != parent_run_id:
        raise CoordinatorError("repair PR parent run does not match the failed WP-08 run")

    merge_sha = sha40(pr.get("merge_commit_sha"), name="repair merge_commit_sha")
    updated = {
        **current,
        "status": STATUS_WAITING_REPAIR_CI,
        "current_candidate_sha": merge_sha,
        "repair_pr": {
            "number": positive_int(pr.get("number"), name="pull request number"),
            "url": str(pr.get("html_url") or ""),
            "parent_wp08_run_id": parent_run_id,
            "merge_commit_sha": merge_sha,
        },
        "updated_at": utc_now(),
        "history": _history(
            current,
            event="repair_merged",
            pr_number=int(pr["number"]),
            parent_wp08_run_id=parent_run_id,
            candidate_sha=merge_sha,
        ),
    }
    _maybe_dispatch_after_quality(
        api,
        issue_number,
        updated,
        reason="repair_main_quality_already_passed",
    )


def handle_quality_run(api: GitHubAPI, workflow_run: Mapping[str, Any]) -> None:
    if (
        str(workflow_run.get("event") or "") != "push"
        or str(workflow_run.get("head_branch") or "") != MAIN_BRANCH
    ):
        return
    head_sha = sha40(workflow_run.get("head_sha"), name="quality head_sha")
    found = find_release_issue(api)
    if found is None:
        return

    issue_number, state = found
    current = validate_state(state)
    if current["status"] not in {STATUS_WAITING_MAIN_QUALITY, STATUS_WAITING_REPAIR_CI}:
        return
    if current["current_candidate_sha"] != head_sha:
        return

    conclusion = str(workflow_run.get("conclusion") or "")
    if conclusion == "success":
        _dispatch(api, issue_number, current, reason="main_quality_passed")
        return

    failed = {
        **current,
        "status": STATUS_MAIN_QUALITY_FAILED,
        "failure_signature": f"main_quality:{conclusion or 'unknown'}",
        "updated_at": utc_now(),
        "history": _history(
            current,
            event="main_quality_failed",
            quality_run_id=workflow_run.get("id"),
            conclusion=conclusion,
            candidate_sha=head_sha,
        ),
    }
    persist_release_state(api, issue_number, failed)


def handle_wp08_run(api: GitHubAPI, workflow_run: Mapping[str, Any]) -> None:
    if str(workflow_run.get("event") or "") != "workflow_dispatch":
        return
    run_id = positive_int(workflow_run.get("id"), name="WP-08 workflow run id")
    found = find_release_issue(api)
    if found is None:
        return

    issue_number, state = found
    current = validate_state(state)
    if (
        current["status"] != STATUS_CERTIFYING
        or int(current.get("current_wp08_run_id") or 0) != run_id
    ):
        return

    head_sha = sha40(workflow_run.get("head_sha"), name="WP-08 head_sha")
    if current["current_candidate_sha"] != head_sha:
        raise CoordinatorError(
            "completed WP-08 run does not match the current release candidate"
        )
    conclusion = str(workflow_run.get("conclusion") or "")

    if conclusion == "success":
        passed = {
            **current,
            "status": STATUS_WP08_PASS,
            "last_wp08_run_id": run_id,
            "last_wp08_conclusion": conclusion,
            "failure_signature": None,
            "updated_at": utc_now(),
            "history": _history(
                current,
                event="wp08_passed",
                wp08_run_id=run_id,
                attempt=current["attempt"],
                candidate_sha=head_sha,
            ),
        }
        persist_release_state(api, issue_number, passed)
        return

    if conclusion in RETRYABLE_WORKFLOW_CONCLUSIONS:
        retry_state = {
            **current,
            "last_wp08_run_id": run_id,
            "last_wp08_conclusion": conclusion,
            "failure_signature": f"workflow:{conclusion}",
            "updated_at": utc_now(),
            "history": _history(
                current,
                event="wp08_retryable_workflow_end",
                wp08_run_id=run_id,
                conclusion=conclusion,
                attempt=current["attempt"],
            ),
        }
        _dispatch(api, issue_number, retry_state, reason=f"bounded_retry_after_{conclusion}")
        return

    failed = {
        **current,
        "status": STATUS_FAILED_NEEDS_CLASSIFICATION,
        "last_wp08_run_id": run_id,
        "last_wp08_conclusion": conclusion or "unknown",
        "failure_signature": f"wp08:{conclusion or 'unknown'}",
        "updated_at": utc_now(),
        "history": _history(
            current,
            event="wp08_requires_classification",
            wp08_run_id=run_id,
            conclusion=conclusion or "unknown",
            attempt=current["attempt"],
            candidate_sha=head_sha,
        ),
    }
    persist_release_state(api, issue_number, failed)


def handle_workflow_run(api: GitHubAPI, event: Mapping[str, Any]) -> None:
    workflow_run = event.get("workflow_run") if isinstance(event.get("workflow_run"), Mapping) else {}
    if not workflow_run:
        return
    name = str(workflow_run.get("name") or "")
    if name == QUALITY_WORKFLOW_NAME:
        handle_quality_run(api, workflow_run)
    elif name == WP08_WORKFLOW_NAME:
        handle_wp08_run(api, workflow_run)


def _load_event(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CoordinatorError("GITHUB_EVENT_PATH must contain a JSON object")
    return payload


def _api_from_env(env: Mapping[str, str]) -> GitHubAPI:
    return GitHubAPI(
        str(env.get("GITHUB_REPOSITORY") or ""),
        str(env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or ""),
        api_url=str(env.get("GITHUB_API_URL") or "https://api.github.com"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Coordinate a bounded WP-08 release run.")
    parser.add_argument(
        "--mode",
        required=True,
        choices=("authorize", "bootstrap", "pull-request", "workflow-run"),
    )
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument(
        "--bootstrap-glob",
        default="governance/release-runs/wp08-bootstrap-*.json",
    )
    args = parser.parse_args()
    env = dict(os.environ)

    try:
        api = _api_from_env(env)
        event = _load_event(args.event_path)
        if args.mode == "authorize":
            authorize(api, env)
        elif args.mode == "bootstrap":
            bootstrap(api, env, Path(".").glob(args.bootstrap_glob))
        elif args.mode == "pull-request":
            handle_pull_request(api, event)
        elif args.mode == "workflow-run":
            handle_workflow_run(api, event)
        return 0
    except (
        CoordinatorError,
        GitHubCoordinatorError,
        ReleaseStateError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
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
