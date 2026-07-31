
from __future__ import annotations

import datetime as dt
from typing import Any


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def merge_issue_state(
    previous: list[dict[str, Any]],
    current_failures: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_ids = {str(item.get("issue_id")) for item in current_failures}
    result_by_gate = {str(item.get("id")): str(item.get("status")) for item in results if isinstance(item, dict)}
    merged = list(current_failures)
    for item in previous:
        issue_id = str(item.get("issue_id") or "")
        if not issue_id or issue_id in current_ids:
            continue
        status = result_by_gate.get(str(item.get("gate_id") or ""))
        if status == "PASS":
            next_status = "RESOLVED_BY_FULL_JUDGE"
        elif status == "SKIPPED_UPSTREAM_FAILURE":
            next_status = "BLOCKED_BY_UPSTREAM"
        elif status == "BLOCKED_BY_ENVIRONMENT":
            next_status = "BLOCKED_BY_ENVIRONMENT"
        else:
            next_status = "NOT_RERUN"
        copy = dict(item)
        copy["status"] = next_status
        copy["updated_at"] = now()
        merged.append(copy)
    return sorted(merged, key=lambda item: (str(item.get("status")), str(item.get("gate_id")), str(item.get("issue_id"))))
