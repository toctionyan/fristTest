from __future__ import annotations

"""Verified artifact ledger for the Lifecycle Agent Loop.

The ledger stores only authority-backed business artifacts, stable collection
views, exact query-result coverage, offers, explicit UI authorities, eligibility evidence and execution
receipts.  It never stores model-produced intent, focus, pronoun guesses or
unverified semantic claims.
"""

from copy import deepcopy
from time import time
from typing import Any
from uuid import uuid4

from agent_core.operations.draft import display_projection, ensure_transaction_draft, transition_draft

LEDGER_SCHEMA_VERSION = 12
MAX_LEDGER_ENTRIES = 200
ACTIVE_OFFER_SECONDS = 10 * 60
VIEW_SECONDS = 2 * 60 * 60
RESULT_SECONDS = 2 * 60 * 60
ARTIFACT_SECONDS = 2 * 60 * 60


def scope_for_state(state: dict[str, Any]) -> dict[str, str]:
    return {
        "tenant_id": str(state.get("current_tenant_id") or "default"),
        "user_id": str(state.get("current_user_id") or ""),
        "thread_id": str(state.get("current_thread_id") or ""),
    }


def _same_scope(left: dict[str, Any] | None, right: dict[str, str]) -> bool:
    return all(str((left or {}).get(k) or "") == str(right.get(k) or "") for k in ("tenant_id", "user_id", "thread_id"))


def _now() -> float:
    return time()


def create_handle(prefix: str) -> str:
    """Opaque handles never reveal a business database identifier."""
    return f"{prefix}:{uuid4().hex}"


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _valid_entry(item: dict[str, Any]) -> bool:
    kind = str(item.get("kind") or "")
    handle = str(item.get("handle") or "")
    if kind not in {"artifact", "view", "result", "offer", "receipt", "eligibility", "authority"} or not handle:
        return False
    scope = item.get("scope")
    if not isinstance(scope, dict) or not scope.get("user_id") or not scope.get("thread_id"):
        return False
    if kind == "artifact":
        return bool(item.get("resource_type") and item.get("resource_id"))
    if kind == "view":
        return bool(item.get("view_type")) and isinstance(item.get("member_handles"), list)
    if kind == "result":
        return bool(item.get("capability")) and isinstance(item.get("member_handles"), list)
    if kind in {"offer", "eligibility"}:
        return bool(item.get("action_id") and item.get("target_handle") and isinstance(item.get("preview"), dict))
    if kind == "authority":
        return bool(item.get("offer_handle") and item.get("action_id") and item.get("target_handle") and item.get("authority_type"))
    return True


def _expired(item: dict[str, Any], *, now: float) -> bool:
    try:
        return float(item.get("expires_at") or 0) > 0 and now >= float(item.get("expires_at"))
    except (TypeError, ValueError):
        return True


def normalize_ledger(entries: list[dict[str, Any]] | None, *, now: float | None = None) -> list[dict[str, Any]]:
    current = _now() if now is None else now
    deduped: dict[str, dict[str, Any]] = {}
    for raw in entries or []:
        if not isinstance(raw, dict) or not _valid_entry(raw):
            continue
        item = deepcopy(raw)
        if str(item.get("kind") or "") == "offer":
            item = ensure_transaction_draft(item, previous=deduped.get(str(item.get("handle") or "")))
        if _expired(item, now=current):
            if str(item.get("kind") or "") == "offer":
                # The canonical transaction state decides expiry.  Projection-only status
                # must never reactivate an expired draft.
                if str(item.get("draft_state") or "") not in {"COMMITTED", "FAILED_FINAL", "REVOKED", "EXPIRED"}:
                    item = transition_draft(item, "EXPIRED", reason="ttl_expired")
            elif str(item.get("status") or "active") in {"active", "ready", "needs_input", "pending_confirmation", "eligible", "authorized", "committing"}:
                item["status"] = "expired"
        handle = str(item["handle"])
        previous = deduped.get(handle)
        if previous is None or int(item.get("version") or 0) >= int(previous.get("version") or 0):
            deduped[handle] = item
    projection_rank = {"pending_confirmation": 9, "authorized": 8, "ready": 7, "needs_input": 6, "eligible": 5, "active": 4, "executed": 2, "declined": 1, "expired": 0, "superseded": 0}
    draft_rank = {"AWAITING_AUTHORIZATION": 12, "AUTHORIZED": 11, "READY": 10, "NEEDS_INPUT": 9, "COMMITTING": 8, "SUBMISSION_UNKNOWN": 7, "FAILED_RETRYABLE": 6, "RECONCILIATION_REQUIRED": 5, "COMMITTED": 2, "FAILED_FINAL": 1, "REVOKED": 0, "EXPIRED": 0}
    def _rank(row: dict[str, Any]) -> int:
        if str(row.get("kind") or "") == "offer":
            return draft_rank.get(str(row.get("draft_state") or ""), 0)
        return projection_rank.get(str(row.get("status") or "active"), 1)
    return sorted(
        deduped.values(),
        key=lambda row: (_rank(row), int(row.get("updated_turn") or 0), float(row.get("updated_at") or 0)),
        reverse=True,
    )[:MAX_LEDGER_ENTRIES]


def active_entries(
    entries: list[dict[str, Any]] | None,
    *,
    scope: dict[str, str],
    kind: str | None = None,
    resource_type: str | None = None,
    statuses: set[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in normalize_ledger(entries):
        if not _same_scope(item.get("scope"), scope):
            continue
        if kind and str(item.get("kind")) != kind:
            continue
        if resource_type and str(item.get("resource_type") or "") != resource_type:
            continue
        if statuses:
            current_state = str(item.get("draft_state") or "") if str(item.get("kind") or "") == "offer" else str(item.get("status") or "")
            if current_state not in statuses:
                continue
        rows.append(item)
    return rows


def find_handle(
    entries: list[dict[str, Any]] | None,
    handle: str,
    *,
    scope: dict[str, str],
    allowed_kinds: set[str] | None = None,
    allowed_resource_types: set[str] | None = None,
    active_only: bool = True,
) -> dict[str, Any] | None:
    for item in normalize_ledger(entries):
        if str(item.get("handle") or "") != str(handle or ""):
            continue
        if not _same_scope(item.get("scope"), scope):
            return None
        if allowed_kinds and str(item.get("kind") or "") not in allowed_kinds:
            return None
        if allowed_resource_types and str(item.get("resource_type") or "") not in allowed_resource_types:
            return None
        if active_only:
            if str(item.get("kind") or "") == "offer":
                if str(item.get("draft_state") or "") in {"COMMITTED", "FAILED_FINAL", "REVOKED", "EXPIRED"}:
                    return None
            elif str(item.get("status") or "active") in {"expired", "declined", "superseded"}:
                return None
        return deepcopy(item)
    return None


def _base(*, kind: str, handle: str, scope: dict[str, str], turn: int, status: str, ttl_seconds: int, label: str) -> dict[str, Any]:
    now = _now()
    return {
        "kind": kind,
        "handle": handle,
        "scope": dict(scope),
        "label": str(label),
        "status": status,
        "created_turn": int(turn),
        "updated_turn": int(turn),
        "created_at": now,
        "updated_at": now,
        "expires_at": now + int(ttl_seconds),
        "version": 1,
    }


def artifact_entry(*, resource_type: str, resource_id: str, label: str, facts: dict[str, Any], scope: dict[str, str], turn: int, source: str, freshness_version: int | None = None, handle: str | None = None) -> dict[str, Any]:
    row = _base(kind="artifact", handle=handle or create_handle(f"h_{resource_type}"), scope=scope, turn=turn, status="active", ttl_seconds=ARTIFACT_SECONDS, label=label)
    row.update({
        "resource_type": str(resource_type),
        "resource_id": str(resource_id),
        "facts": _clean(dict(facts or {})),
        "source": str(source),
        "freshness_version": freshness_version,
    })
    return row


def view_entry(*, view_type: str, member_handles: list[str], labels: list[str], scope: dict[str, str], turn: int, source: str, query: dict[str, Any] | None = None, handle: str | None = None) -> dict[str, Any]:
    unique = list(dict.fromkeys(str(v) for v in member_handles if str(v)))
    row = _base(kind="view", handle=handle or create_handle("h_view"), scope=scope, turn=turn, status="active", ttl_seconds=VIEW_SECONDS, label=f"{view_type}（{len(unique)}项）")
    row.update({
        "view_type": str(view_type),
        "member_handles": unique,
        "labels": list(labels or []),
        "source": str(source),
        "query": _clean(dict(query or {})),
    })
    return row


def result_entry(*, capability: str, member_handles: list[str], labels: list[str], scope: dict[str, str], turn: int, source_target: dict[str, Any], handle: str | None = None) -> dict[str, Any]:
    """Record exactly which verified members the user-facing query covered.

    Result coverage is intentionally separate from a base View.  It enables a
    model-selected generic set expression such as ``difference(view, result)``
    without any source-code branch for a particular natural-language pronoun.
    """
    unique = list(dict.fromkeys(str(v) for v in member_handles if str(v)))
    row = _base(kind="result", handle=handle or create_handle("h_result"), scope=scope, turn=turn, status="active", ttl_seconds=RESULT_SECONDS, label=f"{capability}结果（{len(unique)}项）")
    row.update({
        "capability": str(capability),
        "member_handles": unique,
        "labels": list(labels or []),
        "source_target": _clean(dict(source_target or {})),
    })
    return row


def offer_entry(*, action_id: str, operation: str, target_handle: str, input_values: dict[str, Any], preview: dict[str, Any], scope: dict[str, str], turn: int, label: str, handle: str | None = None) -> dict[str, Any]:
    """Create the canonical single-target TransactionDraft carrier.

    Ledger creates and normalizes the data carrier only.  The operation
    preparation owner must attach any immutable capability snapshot before the
    Draft is persisted or authorized; Ledger never queries the capability
    registry or imports transaction execution services.
    """
    row = _base(kind="offer", handle=handle or create_handle("h_offer"), scope=scope, turn=turn, status="draft", ttl_seconds=ACTIVE_OFFER_SECONDS, label=label)
    # Offer status is a public projection only. Do not persist it in the
    # canonical transaction draft carrier.
    row.pop("status", None)
    row.update({
        "action_id": str(action_id),
        "operation": str(operation),
        "target_handle": str(target_handle),
        "input_values": _clean(dict(input_values or {})),
        "preview": _clean(dict(preview or {})),
        "draft_id": str(row["handle"]),
        "draft_revision": 1,
        "draft_state": "READY",
        "transaction_contract_version": 1,
    })
    return ensure_transaction_draft(row)


def receipt_entry(*, action_id: str, result: dict[str, Any], scope: dict[str, str], turn: int, label: str, draft_id: str | None = None, attempt_id: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
    success = bool((result or {}).get("success"))
    receipt_state = "SUCCESS" if success else "FAILED"
    row = _base(kind="receipt", handle=create_handle("h_receipt"), scope=scope, turn=turn, status="committed" if success else "failed", ttl_seconds=ARTIFACT_SECONDS, label=label)
    row.update({
        "action_id": str(action_id),
        "result": _clean(dict(result or {})),
        "receipt_state": receipt_state,
        "draft_id": str(draft_id or ""),
        "attempt_id": str(attempt_id or ""),
        "idempotency_key": str(idempotency_key or ""),
    })
    return row


def authority_entry(*, authority: dict[str, Any], scope: dict[str, str], turn: int, label: str) -> dict[str, Any]:
    """Persist an explicit, independently supplied UI authority.

    This is audit evidence, not model output.  A model can create an offer but
    cannot create this ledger kind because the only producer is the structured
    `/action-authority` resume payload.
    """
    status = "authorized" if str(authority.get("authority_type") or "") == "ui_confirmed" else "rejected"
    row = _base(kind="authority", handle=str(authority.get("grant_id") or create_handle("h_authority")), scope=scope, turn=turn, status=status, ttl_seconds=ARTIFACT_SECONDS, label=label)
    row.update(_clean(dict(authority or {})))
    row.setdefault("grant_id", str(row["handle"]))
    row.setdefault("grant_state", "ISSUED" if status == "authorized" else "REVOKED")
    return row


def eligibility_entry(*, action_id: str, operation: str, target_handle: str, input_values: dict[str, Any], preview: dict[str, Any], scope: dict[str, str], turn: int, label: str, handle: str | None = None) -> dict[str, Any]:
    row = _base(kind="eligibility", handle=handle or create_handle("h_eligibility"), scope=scope, turn=turn, status="eligible", ttl_seconds=ACTIVE_OFFER_SECONDS, label=label)
    row.update({
        "action_id": str(action_id),
        "operation": str(operation),
        "target_handle": str(target_handle),
        "input_values": _clean(dict(input_values or {})),
        "preview": _clean(dict(preview or {})),
    })
    return row


def append_entries(existing: list[dict[str, Any]] | None, additions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {str(item.get("handle")): item for item in normalize_ledger(existing) if item.get("handle")}
    for raw in additions or []:
        if not isinstance(raw, dict) or not raw.get("handle"):
            continue
        current = merged.get(str(raw["handle"]))
        candidate = deepcopy(raw)
        if str(candidate.get("kind") or "") == "offer":
            candidate = ensure_transaction_draft(candidate, previous=current if isinstance(current, dict) else None)
        if current is not None:
            candidate["version"] = max(int(current.get("version") or 0) + 1, int(candidate.get("version") or 0))
            candidate["created_at"] = current.get("created_at", candidate.get("created_at"))
            candidate["created_turn"] = current.get("created_turn", candidate.get("created_turn"))
            # A refreshed Artifact keeps the same stable resource identity.
            # Preserve proof that this exact identity crossed the customer
            # release boundary; otherwise the first read in a multi-goal turn
            # makes the second read incorrectly treat the same order as hidden.
            # Never apply this to Result/View entries: reusing visibility after
            # their member set changes would expose an unseen collection.
            same_artifact_identity = (
                str(current.get("kind") or "") == "artifact"
                and str(candidate.get("kind") or "") == "artifact"
                and str(current.get("resource_type") or "") == str(candidate.get("resource_type") or "")
                and str(current.get("resource_id") or "") == str(candidate.get("resource_id") or "")
            )
            if same_artifact_identity and "presentation_origin" not in candidate and isinstance(current.get("presentation_origin"), dict):
                candidate["presentation_origin"] = deepcopy(current["presentation_origin"])
        candidate["updated_at"] = _now()
        merged[str(candidate["handle"])] = candidate
    return normalize_ledger(list(merged.values()))


def ledger_cards(entries: list[dict[str, Any]] | None, *, scope: dict[str, str]) -> dict[str, Any]:
    rows = active_entries(entries, scope=scope)
    artifacts = [
        {"handle": item["handle"], "type": item.get("resource_type"), "label": item.get("label"), "version": item.get("freshness_version"), "updated_turn": item.get("updated_turn")}
        for item in rows if item.get("kind") == "artifact"
    ][:20]
    views = [
        {"handle": item["handle"], "type": item.get("view_type"), "label": item.get("label"), "count": len(item.get("member_handles") or []), "labels": list(item.get("labels") or [])[:10], "updated_turn": item.get("updated_turn")}
        for item in rows if item.get("kind") == "view"
    ][:10]
    results = [
        {"handle": item["handle"], "capability": item.get("capability"), "label": item.get("label"), "count": len(item.get("member_handles") or []), "labels": list(item.get("labels") or [])[:10], "updated_turn": item.get("updated_turn")}
        for item in rows if item.get("kind") == "result"
    ][:12]
    offers = [
        {
            "handle": item["handle"],
            "draft_id": item.get("draft_id") or item.get("handle"),
            "draft_revision": item.get("draft_revision"),
            "draft_state": item.get("draft_state"),
            "command_digest": item.get("command_digest"),
            "action_id": item.get("action_id"),
            "label": item.get("label"),
            "status": display_projection(str(item.get("draft_state") or "READY"))[0],
            "required_inputs": list((item.get("preview") or {}).get("required_inputs") or item.get("required_inputs") or []),
            "updated_turn": item.get("updated_turn"),
        }
        for item in rows
        if item.get("kind") == "offer" and str(item.get("draft_state") or "") in {"NEEDS_INPUT", "READY", "AWAITING_AUTHORIZATION", "AUTHORIZED", "COMMITTING", "SUBMISSION_UNKNOWN"}
    ][:8]
    eligibilities = [
        {"handle": item["handle"], "action_id": item.get("action_id"), "label": item.get("label"), "status": item.get("status"), "updated_turn": item.get("updated_turn")}
        for item in rows if item.get("kind") == "eligibility" and item.get("status") == "eligible"
    ][:8]
    authorities = [
        {"handle": item["handle"], "offer_handle": item.get("offer_handle"), "action_id": item.get("action_id"), "authority_type": item.get("authority_type"), "actor_id": item.get("actor_id"), "status": item.get("status"), "updated_turn": item.get("updated_turn")}
        for item in rows if item.get("kind") == "authority"
    ][:12]
    return {"artifacts": artifacts, "views": views, "results": results, "offers": offers, "eligibilities": eligibilities, "authorities": authorities}
