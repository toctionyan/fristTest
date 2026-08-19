from __future__ import annotations

"""Deterministic single-use consumption state for EngineeringMergeGrant.

The network adapter stores the state as a GitHub commit status on the grant's
immutable initial TaskRun base SHA.  All final merge consumers are serialized at
repository scope, so the pending status is a durable crash-window reservation.
"""

import hashlib
import json
from typing import Any, Mapping

from engineering_merge_grant import validate_merge_grant_document

CONSUMPTION_SCHEMA = "engineering-merge-grant-consumption@1"
CONTEXT_PREFIX = "engineering-merge-consume/"
TERMINAL_STATES = {"success": "CONSUMED", "failure": "FAILED", "error": "FAILED"}


class EngineeringMergeConsumptionError(RuntimeError):
    pass


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def consumption_context(grant: Mapping[str, Any]) -> str:
    validated = validate_merge_grant_document(grant)
    context = CONTEXT_PREFIX + _text(validated.get("grant_sha256"))
    if len(context) > 100:
        raise EngineeringMergeConsumptionError("merge consumption context exceeds GitHub status limit")
    return context


def classify_consumption(
    grant: Mapping[str, Any], *, combined_status: Mapping[str, Any]
) -> dict[str, Any]:
    validated = validate_merge_grant_document(grant)
    context = consumption_context(validated)
    rows = combined_status.get("statuses")
    if not isinstance(rows, list):
        raise EngineeringMergeConsumptionError("combined commit status must contain statuses list")

    matching: list[Mapping[str, Any]] = [
        row for row in rows if isinstance(row, Mapping) and _text(row.get("context")) == context
    ]
    matching.sort(
        key=lambda row: (
            _text(row.get("updated_at")),
            _text(row.get("created_at")),
            int(row.get("id") or 0),
        ),
        reverse=True,
    )

    state = _text(matching[0].get("state")).lower() if matching else ""
    if not matching:
        status = "RESERVABLE"
        reservation_allowed = True
    elif state == "pending":
        status = "UNCERTAIN"
        reservation_allowed = False
    elif state in TERMINAL_STATES:
        status = TERMINAL_STATES[state]
        reservation_allowed = False
    else:
        status = "BLOCKED_UNKNOWN"
        reservation_allowed = False

    result: dict[str, Any] = {
        "schema": CONSUMPTION_SCHEMA,
        "status": status,
        "reservation_allowed": reservation_allowed,
        "repository": validated["repository"],
        "grant_id": validated["grant_id"],
        "grant_sha256": validated["grant_sha256"],
        "task_binding_fingerprint": validated["task_binding_fingerprint"],
        "anchor_sha": validated["initial_base_sha"],
        "context": context,
        "observed_state": state or None,
        "observed_status_id": int(matching[0].get("id") or 0) if matching else None,
        "single_use": True,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    result["consumption_state_sha256"] = _digest(result)
    return result
