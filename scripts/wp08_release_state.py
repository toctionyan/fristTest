#!/usr/bin/env python3
"""Pure state contract for one durable WP-08 release authorization."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping

CONTRACT = "wp08-release-run@1"
BOOTSTRAP_CONTRACT = "wp08-release-bootstrap@1"
STATE_BEGIN = "<!-- WP08_RELEASE_RUN_STATE_BEGIN -->"
STATE_END = "<!-- WP08_RELEASE_RUN_STATE_END -->"
DEFAULT_MAX_ATTEMPTS = 8

STATUS_AUTHORIZED = "AUTHORIZED"
STATUS_CERTIFYING = "CERTIFYING"
STATUS_WAITING_MAIN_QUALITY = "WAITING_FOR_MAIN_QUALITY"
STATUS_WAITING_REPAIR_CI = "WAITING_FOR_REPAIR_CI"
STATUS_FAILED_NEEDS_CLASSIFICATION = "FAILED_NEEDS_CLASSIFICATION"
STATUS_AWAITING_ENVIRONMENT_CONFIGURATION = "AWAITING_ENVIRONMENT_CONFIGURATION"
STATUS_AWAITING_ENVIRONMENT_RUNTIME = "AWAITING_ENVIRONMENT_RUNTIME"
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
    STATUS_AWAITING_ENVIRONMENT_CONFIGURATION,
    STATUS_AWAITING_ENVIRONMENT_RUNTIME,
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
_REPAIR_ENVIRONMENT_GATE_RE = re.compile(
    r"(?mi)^\s*WP08-Post-Repair-Environment-Gate:\s*(environment_runtime)\s*$"
)


class ReleaseStateError(RuntimeError):
    """Invalid or unsafe release-run state."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha40(value: Any, *, name: str) -> str:
    sha = str(value or "").strip().casefold()
    if not _SHA40_RE.fullmatch(sha):
        raise ReleaseStateError(f"{name} must be a full 40-character commit SHA")
    return sha


def positive_int(value: Any, *, name: str, allow_zero: bool = False) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ReleaseStateError(f"{name} must be an integer") from exc
    minimum = 0 if allow_zero else 1
    if number < minimum:
        raise ReleaseStateError(f"{name} must be >= {minimum}")
    return number


def release_id(value: Any) -> str:
    result = str(value or "").strip()
    if not _RELEASE_ID_RE.fullmatch(result):
        raise ReleaseStateError("release_run_id is invalid")
    return result


def append_history(state: Mapping[str, Any], event: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("history")
    history = [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []
    history.append(dict(event))
    return history[-64:]


def validate_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    state = dict(payload)
    if state.get("contract") != CONTRACT:
        raise ReleaseStateError("release-run state contract is invalid")
    state["release_run_id"] = release_id(state.get("release_run_id"))
    state["authorized_initial_sha"] = sha40(state.get("authorized_initial_sha"), name="authorized_initial_sha")
    state["current_candidate_sha"] = sha40(state.get("current_candidate_sha"), name="current_candidate_sha")
    state["attempt"] = positive_int(state.get("attempt", 0), name="attempt", allow_zero=True)
    state["max_attempts"] = positive_int(state.get("max_attempts"), name="max_attempts")
    if state["attempt"] > state["max_attempts"]:
        raise ReleaseStateError("attempt exceeds max_attempts")
    status = str(state.get("status") or "").strip()
    if status not in ACTIVE_STATUSES | {STATUS_ABORTED}:
        raise ReleaseStateError(f"unknown release-run status: {status!r}")
    state["status"] = status
    if state.get("production_closed") is not False:
        raise ReleaseStateError("WP-08 coordinator cannot claim production_closed")
    current_run = state.get("current_wp08_run_id")
    if current_run not in (None, ""):
        state["current_wp08_run_id"] = positive_int(current_run, name="current_wp08_run_id")
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
        raise ReleaseStateError("release-run issue contains invalid JSON state") from exc
    if not isinstance(payload, dict):
        raise ReleaseStateError("release-run issue state must be a JSON object")
    return validate_state(payload)


def parse_repair_markers(body: str) -> tuple[str, int] | None:
    release = _REPAIR_RELEASE_RE.search(str(body or ""))
    parent = _REPAIR_PARENT_RE.search(str(body or ""))
    if not release and not parent:
        return None
    if not release or not parent:
        raise ReleaseStateError("repair PR must provide both WP08-Release-Run-ID and WP08-Parent-Run-ID")
    return release_id(release.group(1)), positive_int(parent.group(1), name="WP08-Parent-Run-ID")


def parse_repair_environment_gate(body: str) -> str | None:
    match = _REPAIR_ENVIRONMENT_GATE_RE.search(str(body or ""))
    return str(match.group(1)).strip().casefold() if match else None


def new_state(
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


__all__ = [
    "ACTIVE_STATUSES",
    "BOOTSTRAP_CONTRACT",
    "CONTRACT",
    "DEFAULT_MAX_ATTEMPTS",
    "REPAIRABLE_SOURCE_STATUSES",
    "RETRYABLE_WORKFLOW_CONCLUSIONS",
    "ReleaseStateError",
    "STATUS_ABORTED",
    "STATUS_ATTEMPT_BUDGET_EXHAUSTED",
    "STATUS_AUTHORIZED",
    "STATUS_AWAITING_ENVIRONMENT_CONFIGURATION",
    "STATUS_AWAITING_ENVIRONMENT_RUNTIME",
    "STATUS_CERTIFYING",
    "STATUS_FAILED_NEEDS_CLASSIFICATION",
    "STATUS_MAIN_QUALITY_FAILED",
    "STATUS_WAITING_MAIN_QUALITY",
    "STATUS_WAITING_REPAIR_CI",
    "STATUS_WP08_PASS",
    "append_history",
    "new_state",
    "parse_issue_state",
    "parse_repair_environment_gate",
    "parse_repair_markers",
    "positive_int",
    "release_id",
    "render_issue_body",
    "sha40",
    "utc_now",
    "validate_state",
]
