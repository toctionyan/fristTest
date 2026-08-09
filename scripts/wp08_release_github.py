#!/usr/bin/env python3
"""GitHub API adapter and durable issue ledger for WP-08 release coordination."""
from __future__ import annotations

import json
import time
from typing import Any, Iterable, Mapping
from urllib import error, parse, request

from wp08_release_state import (
    ACTIVE_STATUSES,
    parse_issue_state,
    positive_int,
    render_issue_body,
    sha40,
)

WP08_WORKFLOW_FILE = "wp08-certification.yml"
QUALITY_WORKFLOW_FILE = "quality.yml"
MAIN_BRANCH = "main"


class GitHubCoordinatorError(RuntimeError):
    """GitHub control-plane operation failed or returned unsafe state."""


class GitHubAPI:
    def __init__(self, repository: str, token: str, *, api_url: str = "https://api.github.com") -> None:
        repository = str(repository or "").strip()
        if "/" not in repository:
            raise GitHubCoordinatorError("GITHUB_REPOSITORY must be owner/repository")
        if not str(token or "").strip():
            raise GitHubCoordinatorError("GITHUB_TOKEN is required")
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
            raise GitHubCoordinatorError(
                f"GitHub API {method} {path} failed with {exc.code}: {detail}"
            ) from exc
        except error.URLError as exc:
            raise GitHubCoordinatorError(f"GitHub API {method} {path} unavailable: {exc}") from exc
        if status not in set(expected):
            raise GitHubCoordinatorError(
                f"GitHub API {method} {path} returned unexpected status {status}"
            )
        if not raw:
            return status, None
        try:
            return status, json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise GitHubCoordinatorError(
                f"GitHub API {method} {path} returned invalid JSON"
            ) from exc

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
            raise GitHubCoordinatorError("GitHub issue creation returned no issue number")
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
            raise GitHubCoordinatorError("GitHub issue update returned invalid payload")
        return payload

    def get_workflow_run(self, run_id: int) -> dict[str, Any]:
        _, payload = self._request("GET", f"/repos/{self.repository}/actions/runs/{int(run_id)}")
        if not isinstance(payload, dict):
            raise GitHubCoordinatorError("workflow run payload is invalid")
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

    def dispatch_wp08(
        self,
        *,
        candidate_sha: str,
        resume_run_id: int | None = None,
        resume_run_attempt: int = 1,
    ) -> int:
        candidate_sha = sha40(candidate_sha, name="candidate_sha")
        resume_id = (
            positive_int(resume_run_id, name="resume_run_id")
            if resume_run_id is not None else None
        )
        resume_attempt = positive_int(resume_run_attempt, name="resume_run_attempt")
        before = {
            int(row.get("id"))
            for row in self.list_workflow_runs(WP08_WORKFLOW_FILE, event="workflow_dispatch")
            if str(row.get("head_sha") or "").casefold() == candidate_sha
            and str(row.get("id") or "").isdigit()
        }
        dispatch_payload: dict[str, Any] = {"ref": MAIN_BRANCH}
        if resume_id is not None:
            dispatch_payload["inputs"] = {
                "resume_run_id": str(resume_id),
                "resume_run_attempt": str(resume_attempt),
            }
        status, payload = self._request(
            "POST",
            f"/repos/{self.repository}/actions/workflows/{WP08_WORKFLOW_FILE}/dispatches",
            payload=dispatch_payload,
            expected=(200, 204),
        )
        if status == 200 and isinstance(payload, dict):
            run_id = payload.get("workflow_run_id") or payload.get("id")
            if str(run_id or "").isdigit():
                return int(run_id)
        for _ in range(15):
            time.sleep(2)
            candidates = [
                row
                for row in self.list_workflow_runs(WP08_WORKFLOW_FILE, event="workflow_dispatch")
                if str(row.get("head_sha") or "").casefold() == candidate_sha
                and str(row.get("id") or "").isdigit()
                and int(row["id"]) not in before
            ]
            if candidates:
                return int(max(candidates, key=lambda row: int(row["id"]))["id"])
        raise GitHubCoordinatorError(
            "workflow dispatch succeeded but the WP-08 run ID could not be resolved"
        )


def release_issues(api: GitHubAPI, *, state: str = "open") -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    for issue in api.list_issues(state=state):
        parsed = parse_issue_state(str(issue.get("body") or ""))
        if parsed is None:
            continue
        rows.append((positive_int(issue.get("number"), name="issue number"), parsed))
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
        for number, state in release_issues(api, state=issue_state):
            if release_run_id is None or state["release_run_id"] == release_run_id:
                matches.append((number, state))
    if release_run_id is not None:
        if len(matches) > 1:
            raise GitHubCoordinatorError(
                f"multiple release ledger issues exist for {release_run_id}"
            )
        return matches[0] if matches else None
    active = [(number, state) for number, state in matches if state["status"] in ACTIVE_STATUSES]
    if len(active) > 1:
        raise GitHubCoordinatorError("multiple active WP-08 release runs are forbidden")
    return active[0] if active else None


def persist_release_state(
    api: GitHubAPI,
    issue_number: int,
    state: Mapping[str, Any],
    *,
    close: bool = False,
) -> dict[str, Any]:
    body = render_issue_body(state)
    api.update_issue(issue_number, body=body, close=close)
    parsed = parse_issue_state(body)
    assert parsed is not None
    print(json.dumps(
        {"contract": parsed["contract"], "issue_number": issue_number, "state": parsed},
        ensure_ascii=False,
    ))
    return parsed


__all__ = [
    "GitHubAPI",
    "GitHubCoordinatorError",
    "MAIN_BRANCH",
    "QUALITY_WORKFLOW_FILE",
    "WP08_WORKFLOW_FILE",
    "find_release_issue",
    "persist_release_state",
]
