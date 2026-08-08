#!/usr/bin/env python3
"""Durable WP-08 release-run coordinator.

The coordinator turns one human release authorization into a bounded, auditable
release session.  It deliberately keeps production secrets out of the control
plane: it only records state in a GitHub issue and dispatches the existing
protected WP-08 workflow on ``main``.

State is fail-closed:
- exactly one active release run is allowed;
- every automatic continuation must bind to the current candidate SHA;
- repair continuation requires explicit PR markers that bind the repair to the
  failed WP-08 run;
- automatic retry is limited to non-semantic workflow conclusions such as
  cancellation/timeout and is capped by ``max_attempts``;
- semantic ``failure`` never retries blindly.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Iterable, Mapping
from urllib import error, parse, request

CONTRACT = "wp08-release-run@1"
BOOTSTRAP_CONTRACT = "wp08-release-bootstrap@1"
STATE_BEGIN = "<!-- WP08_RELEASE_RUN_STATE_BEGIN -->"
STATE_END = "<!-- WP08_RELEASE_RUN_STATE_END -->"
DEFAULT_MAX_ATTEMPTS = 8
WP08_WORKFLOW_NAME = "wp08-full-stack-certification"
WP08_WORKFLOW_FILE = "wp08-certification.yml"
QUALITY_WORKFLOW_NAME = "quality"
QUALITY_WORKFLOW_FILE = "quality.yml"
MAIN_REF = "refs/heads/main"
MAIN_BRANCH = "main"

STATUS_AUTHORIZED = "AUTHORIZED"
STATUS_CERTIFYING = "CERTIFYING"
STATUS_WAITING_MAIN_QUALITY = "WAITING_FOR_MAIN_QUALITY"
STATUS_WAITING_REPAIR_CI = "WAITING_FOR_REPAIR_CI"
STATUS_FAILED_NEEDS_CLASSIFICATION = "FAILED_NEEDS_CLASSIFICATION"
STATUS_MAIN_QUALITY_FAILED = "MAIN_QUALITY_FAILED"
STATUS_ATTEMPT_BUDGET_EXHAUSTED = "ATTEMPT_BUDGET_EXHAUSTED"
STATUS_WP08_PASS = "WP08_PASS"
STATUS_ABORTED = "ABORTED"

ACTIVE_STATUSES = {
    STATUS_AUTHORIZED,
    STATUS_CERTIFYING,
    STATUS_WAITING_MAIN_QUALITY,
    STATUS_WAITING_REPAIR_CI,
    STATUS_FAILED_NEEDS_CLASSIFICATION,
    STATUS_MAIN_QUALITY_FAILED,
    STATUS_ATTEMPT_BUDGET_EXHAUSTED,
    STATUS_WP08_PASS,
}
REPAIRABLE_SOURCE_STATUSES = {
    STATUS_FAILED_NEEDS_CLASSIFICATION,
    STATUS_MAIN_QUALITY_FAILED,
}
RETRYABLE_WORKFLOW_CONCLUSIONS = {"cancelled", "timed_out", "stale"}
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
_REPAIR_RELEASE_RE = re.compile(r"(?mi)^\s*WP08-Release-Run-ID:\s*([A-Za-z0-9_.:-]+)\s*$")
_REPAIR_PARENT_RE = re.compile(r"(?mi)^\s*WP08-Parent-Run-ID:\s*([0-9]+)\s*$")


class CoordinatorError(RuntimeError):
    """Fail-closed coordinator error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(value: Any, *, name: str) -> str:
    sha = str(value or "").strip().casefold()
    if not _SHA40_RE.fullmatch(sha):
        raise CoordinatorError(f"{name} must be a full 40-character commit SHA")
    return sha


def _positive_int(value: Any, *, name: str, allow_zero: bool = False) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise CoordinatorError(f"{name} must be an integer") from exc
    minimum = 0 if allow_zero else 1
    if number < minimum:
        raise CoordinatorError(f"{name} must be >= {minimum}")
    return number


def _release_id(value: Any) -> str:
    release_id = str(value or "").strip()
    if not _RELEASE_ID_RE.fullmatch(release_id):
        raise CoordinatorError("release_run_id is invalid")
    return release_id


def _history(state: Mapping[str, Any], event: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("history")
    history = [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []
    history.append(dict(event))
    return history[-64:]


def validate_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    state = dict(payload)
    if state.get("contract") != CONTRACT:
        raise CoordinatorError("release-run state contract is invalid")
    state["release_run_id"] = _release_id(state.get("release_run_id"))
    state["authorized_initial_sha"] = _sha(state.get("authorized_initial_sha"), name="authorized_initial_sha")
    state["current_candidate_sha"] = _sha(state.get("current_candidate_sha"), name="current_candidate_sha")
    state["attempt"] = _positive_int(state.get("attempt", 0), name="attempt", allow_zero=True)
    state["max_attempts"] = _positive_int(state.get("max_attempts"), name="max_attempts")
    if state["attempt"] > state["max_attempts"]:
        raise CoordinatorError("attempt exceeds max_attempts")
    status = str(state.get("status") or "").strip()
    if status not in ACTIVE_STATUSES | {STATUS_ABORTED}:
        raise CoordinatorError(f"unknown release-run status: {status!r}")
    state["status"] = status
    if state.get("production_closed") is not False:
        raise CoordinatorError("WP-08 coordinator cannot claim production_closed")
    current_run = state.get("current_wp08_run_id")
    if current_run not in (None, ""):
        state["current_wp08_run_id"] = _positive_int(current_run, name="current_wp08_run_id")
    return state


def render_issue_body(state: Mapping[str, Any]) -> str:
    validated = validate_state(state)
    payload = json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True)
    return (
        "This issue is the durable machine ledger for one WP-08 release authorization.\n"
        "Do not edit the state block by hand. Product and production closure remain separate authorities.\n\n"
        f"{STATE_BEGIN}\n```json\n{payload}\n```\n{STATE_END}\n"
    )


def parse_issue_state(body: str) -> dict[str, Any] | None:
    text = str(body or "")
    start = text.find(STATE_BEGIN)
    end = text.find(STATE_END)
    if start < 0 or end < 0 or end <= start:
        return None
    block = text[start + len(STATE_BEGIN):end].strip()
    if block.startswith("```json"):
        block = block[len("```json"):].strip()
    if block.endswith("```"):
        block = block[:-3].strip()
    try:
        payload = json.loads(block)
    except json.JSONDecodeError as exc:
        raise CoordinatorError("release-run issue contains invalid JSON state") from exc
    if not isinstance(payload, dict):
        raise CoordinatorError("release-run issue state must be a JSON object")
    return validate_state(payload)


def parse_repair_markers(body: str) -> tuple[str, int] | None:
    release = _REPAIR_RELEASE_RE.search(str(body or ""))
    parent = _REPAIR_PARENT_RE.search(str(body or ""))
    if not release and not parent:
        return None
    if not release or not parent:
        raise CoordinatorError("repair PR must provide both WP08-Release-Run-ID and WP08-Parent-Run-ID")
    return _release_id(release.group(1)), _positive_int(parent.group(1), name="WP08-Parent-Run-ID")


class GitHubAPI:
    def __init__(self, repository: str, token: str, *, api_url: str = "https://api.github.com") -> None:
        repository = str(repository or "").strip()
        if "/" not in repository:
            raise CoordinatorError("GITHUB_REPOSITORY must be owner/repository")
        if not str(token or "").strip():
            raise CoordinatorError("GITHUB_TOKEN is required")
        self.repository = repository
        self.token = token
        self.api_url = str(api_url or "").rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        expected: Iterable[int] = (200,),
    ) -> tuple[int, Any]:
        data = None if payload is None else json.dumps(dict(payload)).encode("utf-8")
        req = request.Request(
            f"{self.api_url}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "wp08-release-coordinator",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                status = int(response.status)
                raw = response.read()
        except error.HTTPError as exc:
            raw = exc.read()
            detail = raw.decode("utf-8", errors="replace")[:800]
            raise CoordinatorError(f"GitHub API {method} {path} failed with {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise CoordinatorError(f"GitHub API {method} {path} unavailable: {exc}") from exc
        if status not in set(expected):
            raise CoordinatorError(f"GitHub API {method} {path} returned unexpected status {status}")
        if not raw:
            return status, None
        try:
            return status, json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise CoordinatorError(f"GitHub API {method} {path} returned invalid JSON") from exc

    def list_issues(self, *, state: str = "open") -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, 11):
            _, payload = self._request(
                "GET",
                f"/repos/{self.repository}/issues?state={parse.quote(state)}&per_page=100&page={page}",
            )
            rows = payload if isinstance(payload, list) else []
            result.extend(row for row in rows if isinstance(row, dict) and "pull_request" not in row)
            if len(rows) < 100:
                break
        return result

    def create_issue(self, *, title: str, body: str) -> dict[str, Any]:
        _, payload = self._request(
            "POST",
            f"/repos/{self.repository}/issues",
            payload={"title": title, "body": body},
            expected=(201,),
        )
        if not isinstance(payload, dict) or not payload.get("number"):
            raise CoordinatorError("GitHub issue creation returned no issue number")
        return payload

    def update_issue(self, issue_number: int, *, body: str, close: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {"body": body}
        if close:
            data["state"] = "closed"
        _, payload = self._request(
            "PATCH",
            f"/repos/{self.repository}/issues/{int(issue_number)}",
            payload=data,
        )
        if not isinstance(payload, dict):
            raise CoordinatorError("GitHub issue update returned invalid payload")
        return payload

    def get_workflow_run(self, run_id: int) -> dict[str, Any]:
        _, payload = self._request("GET", f"/repos/{self.repository}/actions/runs/{int(run_id)}")
        if not isinstance(payload, dict):
            raise CoordinatorError("workflow run payload is invalid")
        return payload

    def list_workflow_runs(
        self,
        workflow_file: str,
        *,
        branch: str = MAIN_BRANCH,
        event: str | None = None,
    ) -> list[dict[str, Any]]:
        query = {"branch": branch, "per_page": "50"}
        if event:
            query["event"] = event
        encoded = parse.urlencode(query)
        _, payload = self._request(
            "GET",
            f"/repos/{self.repository}/actions/workflows/{parse.quote(workflow_file)}/runs?{encoded}",
        )
        rows = payload.get("workflow_runs") if isinstance(payload, dict) else None
        return [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []

    def dispatch_wp08(self, *, candidate_sha: str) -> int:
        candidate_sha = _sha(candidate_sha, name="candidate_sha")
        before = {
            int(row.get("id"))
            for row in self.list_workflow_runs(WP08_WORKFLOW_FILE, event="workflow_dispatch")
            if str(row.get("head_sha") or "").casefold() == candidate_sha and str(row.get("id") or "").isdigit()
        }
        status, payload = self._request(
            "POST",
            f"/repos/{self.repository}/actions/workflows/{WP08_WORKFLOW_FILE}/dispatches",
            payload={"ref": MAIN_BRANCH},
            expected=(200, 204),
        )
        if status == 200 and isinstance(payload, dict):
            run_id = payload.get("workflow_run_id") or payload.get("id")
            if str(run_id or "").isdigit():
                return int(run_id)
        for _ in range(15):
            time.sleep(2)
            rows = self.list_workflow_runs(WP08_WORKFLOW_FILE, event="workflow_dispatch")
            candidates = [
                row for row in rows
                if str(row.get("head_sha") or "").casefold() == candidate_sha
                and str(row.get("id") or "").isdigit()
                and int(row["id"]) not in before
            ]
            if candidates:
                return int(max(candidates, key=lambda row: int(row["id"]))["id"])
        raise CoordinatorError("workflow dispatch succeeded but the WP-08 run ID could not be resolved")


def _release_issues(api: GitHubAPI, *, state: str = "open") -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    for issue in api.list_issues(state=state):
        parsed = parse_issue_state(str(issue.get("body") or ""))
        if parsed is None:
            continue
        number = _positive_int(issue.get("number"), name="issue number")
        rows.append((number, parsed))
    return rows


def find_release_issue(
    api: GitHubAPI,
    *,
    release_run_id: str | None = None,
    include_closed: bool = False,
) -> tuple[int, dict[str, Any]] | None:
    states = ("open", "closed") if include_closed else ("open",)
    matches: list[tuple[int, dict[str, Any]]] = []
    for issue_state in states:
        for number, state in _release_issues(api, state=issue_state):
            if release_run_id is None or state["release_run_id"] == release_run_id:
                matches.append((number, state))
    if release_run_id is not None:
        if len(matches) > 1:
            raise CoordinatorError(f"multiple release ledger issues exist for {release_run_id}")
        return matches[0] if matches else None
    active = [(number, state) for number, state in matches if state["status"] in ACTIVE_STATUSES]
    if len(active) > 1:
        raise CoordinatorError("multiple active WP-08 release runs are forbidden")
    return active[0] if active else None


def _persist(api: GitHubAPI, issue_number: int, state: Mapping[str, Any], *, close: bool = False) -> dict[str, Any]:
    validated = validate_state(state)
    api.update_issue(issue_number, body=render_issue_body(validated), close=close)
    print(json.dumps({"contract": CONTRACT, "issue_number": issue_number, "state": validated}, ensure_ascii=False))
    return validated


def _new_state(
    *,
    release_run_id: str,
    authorized_initial_sha: str,
    current_candidate_sha: str,
    max_attempts: int,
    actor: str,
) -> dict[str, Any]:
    now = utc_now()
    return validate_state({
        "contract": CONTRACT,
        "release_run_id": release_run_id,
        "status": STATUS_AUTHORIZED,
        "authorized_initial_sha": authorized_initial_sha,
        "current_candidate_sha": current_candidate_sha,
        "attempt": 0,
        "max_attempts": max_attempts,
        "current_wp08_run_id": None,
        "last_wp08_run_id": None,
        "last_wp08_conclusion": None,
        "failure_signature": None,
        "repair_pr": None,
        "authorized_by": actor,
        "created_at": now,
        "updated_at": now,
        "production_closed": False,
        "history": [],
    })


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
            "history": _history(current, {
                "at": utc_now(),
                "event": "attempt_budget_exhausted",
                "reason": reason,
            }),
        }
        return _persist(api, issue_number, exhausted)
    run_id = api.dispatch_wp08(candidate_sha=current["current_candidate_sha"])
    now = utc_now()
    updated = {
        **current,
        "status": STATUS_CERTIFYING,
        "attempt": current["attempt"] + 1,
        "current_wp08_run_id": run_id,
        "updated_at": now,
        "history": _history(current, {
            "at": now,
            "event": "wp08_dispatched",
            "reason": reason,
            "attempt": current["attempt"] + 1,
            "candidate_sha": current["current_candidate_sha"],
            "wp08_run_id": run_id,
        }),
    }
    return _persist(api, issue_number, updated)


def _quality_pass_exists(api: GitHubAPI, candidate_sha: str) -> bool:
    candidate_sha = _sha(candidate_sha, name="candidate_sha")
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
    return _persist(api, issue_number, current)


def authorize(api: GitHubAPI, env: Mapping[str, str]) -> dict[str, Any]:
    if str(env.get("GITHUB_EVENT_NAME") or "") != "workflow_dispatch":
        raise CoordinatorError("release authorization must originate from workflow_dispatch")
    if str(env.get("GITHUB_REF") or "") != MAIN_REF or str(env.get("GITHUB_REF_PROTECTED") or "").lower() != "true":
        raise CoordinatorError("release authorization requires protected main")
    if find_release_issue(api) is not None:
        raise CoordinatorError("another WP-08 release run is already active")
    sha = _sha(env.get("GITHUB_SHA"), name="GITHUB_SHA")
    coordinator_run = _positive_int(env.get("GITHUB_RUN_ID"), name="GITHUB_RUN_ID")
    release_run_id = f"wp08-release-{coordinator_run}"
    state = _new_state(
        release_run_id=release_run_id,
        authorized_initial_sha=sha,
        current_candidate_sha=sha,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        actor=str(env.get("GITHUB_ACTOR") or "unknown"),
    )
    now = utc_now()
    state = {
        **state,
        "status": STATUS_WAITING_MAIN_QUALITY,
        "updated_at": now,
        "history": [{
            "at": now,
            "event": "human_release_authorized",
            "candidate_sha": sha,
            "coordinator_run_id": coordinator_run,
        }],
    }
    issue = api.create_issue(title=f"[WP08 Release] {release_run_id}", body=render_issue_body(state))
    issue_number = _positive_int(issue.get("number"), name="issue number")
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
    run_id = _positive_int(bootstrap.get("authorized_initial_wp08_run_id"), name="authorized_initial_wp08_run_id")
    expected_sha = _sha(bootstrap.get("authorized_initial_sha"), name="authorized_initial_sha")
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
    current_sha = _sha(env.get("GITHUB_SHA"), name="GITHUB_SHA")
    for path in sorted(paths):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise CoordinatorError(f"bootstrap file must contain a JSON object: {path}")
        release_run_id = _release_id(payload.get("release_run_id"))
        existing = find_release_issue(api, release_run_id=release_run_id, include_closed=True)
        if existing is not None:
            print(json.dumps({"contract": CONTRACT, "bootstrap": str(path), "status": "ALREADY_CONSUMED"}, ensure_ascii=False))
            continue
        if find_release_issue(api) is not None:
            raise CoordinatorError("cannot bootstrap while another WP-08 release run is active")
        run = _validate_bootstrap_run(api, payload)
        max_attempts = _positive_int(payload.get("max_attempts", DEFAULT_MAX_ATTEMPTS), name="max_attempts")
        state = _new_state(
            release_run_id=release_run_id,
            authorized_initial_sha=_sha(payload.get("authorized_initial_sha"), name="authorized_initial_sha"),
            current_candidate_sha=current_sha,
            max_attempts=max_attempts,
            actor=str(
                payload.get("authorized_by")
                or ((run.get("actor") or {}).get("login") if isinstance(run.get("actor"), Mapping) else "")
                or "legacy-manual-dispatch"
            ),
        )
        now = utc_now()
        state.update({
            "status": STATUS_WAITING_MAIN_QUALITY,
            "attempt": 1,
            "current_wp08_run_id": int(run["id"]),
            "last_wp08_run_id": int(run["id"]),
            "last_wp08_conclusion": str(run.get("conclusion") or ""),
            "failure_signature": "legacy_wp08_failure_requires_continuation",
            "updated_at": now,
            "history": [{
                "at": now,
                "event": "legacy_authorization_bootstrapped",
                "wp08_run_id": int(run["id"]),
                "authorized_initial_sha": state["authorized_initial_sha"],
                "current_candidate_sha": current_sha,
            }],
        })
        issue = api.create_issue(title=f"[WP08 Release] {release_run_id}", body=render_issue_body(state))
        issue_number = _positive_int(issue.get("number"), name="issue number")
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
    merge_sha = _sha(pr.get("merge_commit_sha"), name="repair merge_commit_sha")
    now = utc_now()
    updated = {
        **current,
        "status": STATUS_WAITING_REPAIR_CI,
        "current_candidate_sha": merge_sha,
        "repair_pr": {
            "number": _positive_int(pr.get("number"), name="pull request number"),
            "url": str(pr.get("html_url") or ""),
            "parent_wp08_run_id": parent_run_id,
            "merge_commit_sha": merge_sha,
        },
        "updated_at": now,
        "history": _history(current, {
            "at": now,
            "event": "repair_merged",
            "pr_number": int(pr["number"]),
            "parent_wp08_run_id": parent_run_id,
            "candidate_sha": merge_sha,
        }),
    }
    _maybe_dispatch_after_quality(
        api,
        issue_number,
        updated,
        reason="repair_main_quality_already_passed",
    )


def handle_quality_run(api: GitHubAPI, workflow_run: Mapping[str, Any]) -> None:
    if str(workflow_run.get("event") or "") != "push" or str(workflow_run.get("head_branch") or "") != MAIN_BRANCH:
        return
    head_sha = _sha(workflow_run.get("head_sha"), name="quality head_sha")
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
    now = utc_now()
    failed = {
        **current,
        "status": STATUS_MAIN_QUALITY_FAILED,
        "failure_signature": f"main_quality:{conclusion or 'unknown'}",
        "updated_at": now,
        "history": _history(current, {
            "at": now,
            "event": "main_quality_failed",
            "quality_run_id": workflow_run.get("id"),
            "conclusion": conclusion,
            "candidate_sha": head_sha,
        }),
    }
    _persist(api, issue_number, failed)


def handle_wp08_run(api: GitHubAPI, workflow_run: Mapping[str, Any]) -> None:
    if str(workflow_run.get("event") or "") != "workflow_dispatch":
        return
    run_id = _positive_int(workflow_run.get("id"), name="WP-08 workflow run id")
    found = find_release_issue(api)
    if found is None:
        return
    issue_number, state = found
    current = validate_state(state)
    if current["status"] != STATUS_CERTIFYING or int(current.get("current_wp08_run_id") or 0) != run_id:
        return
    head_sha = _sha(workflow_run.get("head_sha"), name="WP-08 head_sha")
    if current["current_candidate_sha"] != head_sha:
        raise CoordinatorError("completed WP-08 run does not match the current release candidate")
    conclusion = str(workflow_run.get("conclusion") or "")
    now = utc_now()
    if conclusion == "success":
        passed = {
            **current,
            "status": STATUS_WP08_PASS,
            "last_wp08_run_id": run_id,
            "last_wp08_conclusion": conclusion,
            "failure_signature": None,
            "updated_at": now,
            "history": _history(current, {
                "at": now,
                "event": "wp08_passed",
                "wp08_run_id": run_id,
                "attempt": current["attempt"],
                "candidate_sha": head_sha,
            }),
        }
        _persist(api, issue_number, passed)
        return
    if conclusion in RETRYABLE_WORKFLOW_CONCLUSIONS:
        retry_state = {
            **current,
            "last_wp08_run_id": run_id,
            "last_wp08_conclusion": conclusion,
            "failure_signature": f"workflow:{conclusion}",
            "updated_at": now,
            "history": _history(current, {
                "at": now,
                "event": "wp08_retryable_workflow_end",
                "wp08_run_id": run_id,
                "conclusion": conclusion,
                "attempt": current["attempt"],
            }),
        }
        _dispatch(api, issue_number, retry_state, reason=f"bounded_retry_after_{conclusion}")
        return
    failed = {
        **current,
        "status": STATUS_FAILED_NEEDS_CLASSIFICATION,
        "last_wp08_run_id": run_id,
        "last_wp08_conclusion": conclusion or "unknown",
        "failure_signature": f"wp08:{conclusion or 'unknown'}",
        "updated_at": now,
        "history": _history(current, {
            "at": now,
            "event": "wp08_requires_classification",
            "wp08_run_id": run_id,
            "conclusion": conclusion or "unknown",
            "attempt": current["attempt"],
            "candidate_sha": head_sha,
        }),
    }
    _persist(api, issue_number, failed)


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
    parser.add_argument("--mode", required=True, choices=("authorize", "bootstrap", "pull-request", "workflow-run"))
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--bootstrap-glob", default="governance/release-runs/wp08-bootstrap-*.json")
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
    except (CoordinatorError, OSError, json.JSONDecodeError) as exc:
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
