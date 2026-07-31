from __future__ import annotations

"""Trusted context partitions for model-facing runtime projections."""

from copy import deepcopy
from typing import Any, Iterable

_DIAGNOSTIC_AUTHORITY = "execution_diagnostic_not_user_intent_or_business_fact"
_VERIFIED_AUTHORITY = "verified_execution_observation"


def _handles_from_result(result: dict[str, Any]) -> list[str]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    handles: list[str] = []
    for key, value in data.items():
        if key.endswith("_handle") and value:
            handles.append(str(value))
        elif key.endswith("_handles") and isinstance(value, list):
            handles.extend(str(item) for item in value if item)
    for entry in list(result.get("entries") or []):
        if isinstance(entry, dict) and entry.get("handle"):
            handles.append(str(entry["handle"]))
    return list(dict.fromkeys(handles))


def _match_status(result: dict[str, Any]) -> str:
    proof = result.get("match_proof") if isinstance(result.get("match_proof"), dict) else {}
    return str(proof.get("status") or "").strip().upper()


def _verified(result: dict[str, Any]) -> bool:
    if result.get("ok") is not True:
        return False
    if _match_status(result) in {"REJECTED", "MISMATCH", "ABSENT", "ABSENT_PROVEN", "FORBIDDEN"}:
        return False
    # Query observations may predate permits.  A rejected proof always wins;
    # otherwise a successful, schema-shaped tool result is a verified runtime
    # observation, not a claim of business write success.
    return True


def _row(tool_name: str, result: dict[str, Any], *, authority: str) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return {
        "tool_name": tool_name,
        "ok": bool(result.get("ok")),
        "code": str(result.get("code") or "") or None,
        "message": str(result.get("message") or data.get("message") or "")[:500],
        "result_handles": _handles_from_result(result),
        "runtime_outcome": deepcopy(result.get("runtime_outcome")) if isinstance(result.get("runtime_outcome"), dict) else None,
        "execution_disposition": deepcopy(result.get("execution_disposition")) if isinstance(result.get("execution_disposition"), dict) else None,
        "match_proof_status": _match_status(result) or None,
        "permit_id": str((result.get("execution_permit") or {}).get("permit_id") or "") or None,
        "authority": authority,
    }


def partition_tool_trace(
    trace: Iterable[dict[str, Any]],
    *,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [row for row in list(trace or []) if isinstance(row, dict)]
    if limit is not None:
        rows = rows[-max(0, int(limit)):] if int(limit) > 0 else []
    verified: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for trace_row in rows:
        result = trace_row.get("result") if isinstance(trace_row.get("result"), dict) else {}
        tool_name = str(trace_row.get("name") or trace_row.get("tool_name") or "")
        if _verified(result):
            verified.append(_row(tool_name, result, authority=_VERIFIED_AUTHORITY))
        else:
            diagnostics.append(_row(tool_name, result, authority=_DIAGNOSTIC_AUTHORITY))
    return verified, diagnostics
