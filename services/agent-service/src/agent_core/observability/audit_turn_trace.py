from __future__ import annotations

"""Immutable per-turn audit trail for the Lifecycle Agent Loop.

This module deliberately stores historical model plans as *audit evidence*, not
as future semantic state.  A later turn may inspect a prior plan to understand
which answer / offer / tool call the user is correcting, but the current turn's
single planner remains the only component allowed to interpret new language.
"""

from copy import deepcopy
from hashlib import sha256
import json
from time import time
from typing import Any
from uuid import uuid4

TRACE_SCHEMA_VERSION = 1
MAX_TURN_TRACE_EVENTS = 80
MAX_AUDIT_DETAILED_CARDS = 12
MAX_AUDIT_CATALOG_EVENTS = MAX_TURN_TRACE_EVENTS


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, tuple):
        return [_clean(item) for item in value]
    return value


def create_plan_id() -> str:
    return f"plan:{uuid4().hex}"


def _event_digest(payload: dict[str, Any]) -> str:
    stable = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return sha256(stable.encode("utf-8")).hexdigest()[:24]


def plan_from_tool_calls(*, plan_id: str, turn: int, user_text: str, tool_calls: list[dict[str, Any]], raw_model_content: str, goal_declaration: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create the only semantic-plan record for a single user turn.

    It is immutable after creation.  The program never edits selected targets,
    referents or intent; it can only accept/reject execution by objective rules.
    """
    calls: list[dict[str, Any]] = []
    for index, call in enumerate(tool_calls):
        if not isinstance(call, dict):
            continue
        calls.append(
            {
                "index": index,
                "tool_call_id": str(call.get("id") or call.get("tool_call_id") or ""),
                "name": str(call.get("name") or ""),
                "args": _clean(dict(call.get("args") or {})),
            }
        )
    return {
        "plan_id": str(plan_id),
        "turn": int(turn),
        "user_text": str(user_text),
        "goal_declaration": _clean(dict(goal_declaration or {})),
        "tool_calls": calls,
        "raw_model_content": str(raw_model_content or ""),
        "created_at": time(),
        "authority": "current_turn_only",
        "immutable": True,
    }


def normalize_turn_trace(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Keep valid immutable events, de-duplicated by event/plan ID.

    The oldest events are trimmed only after the bounded audit history is
    exceeded.  No event is rewritten when a later user correction arrives.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for raw in events or []:
        if not isinstance(raw, dict):
            continue
        event_id = str(raw.get("event_id") or "")
        plan_id = str((raw.get("semantic_plan") or {}).get("plan_id") or "")
        if not event_id or not plan_id:
            continue
        existing = by_id.get(event_id)
        if existing is None:
            by_id[event_id] = deepcopy(raw)
    ordered = sorted(by_id.values(), key=lambda row: (int(row.get("turn") or 0), float(row.get("created_at") or 0)))
    return ordered[-MAX_TURN_TRACE_EVENTS:]


def append_turn_event(events: list[dict[str, Any]] | None, event: dict[str, Any]) -> list[dict[str, Any]]:
    current = normalize_turn_trace(events)
    plan_id = str((event.get("semantic_plan") or {}).get("plan_id") or "")
    if plan_id and any(str((row.get("semantic_plan") or {}).get("plan_id") or "") == plan_id for row in current):
        return current
    return normalize_turn_trace([*current, deepcopy(event)])


def _result_handles(result: dict[str, Any]) -> list[str]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    candidates = [
        data.get("view_handle"),
        data.get("result_handle"),
        data.get("offer_handle"),
        data.get("eligibility_handle"),
        data.get("dismissed_offer"),
    ]
    return [str(value) for value in candidates if value]


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    result = dict(result or {})
    public: dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "code": result.get("code"),
        "message": result.get("message"),
        "result_handles": _result_handles(result),
    }
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if data:
        # Preserve plan-relevant objective summaries only.  We do not copy a
        # free-form assistant answer into the result evidence.
        for key in ("capability", "count", "offer_label", "label", "eligible", "needs_input", "offer_completed", "supported"):
            if key in data:
                public[key] = _clean(data[key])
    return {key: value for key, value in public.items() if value not in (None, "", [], {})}


def build_turn_event(
    *,
    plan: dict[str, Any] | None,
    turn: int,
    user_text: str,
    tool_trace: list[dict[str, Any]] | None,
    answer: str | None,
    status: str | None,
    operation_handles: list[str] | None = None,
    answer_evidence_handles: list[str] | None = None,
) -> dict[str, Any]:
    semantic_plan = deepcopy(plan or {
        "plan_id": create_plan_id(),
        "turn": int(turn),
        "user_text": str(user_text),
        "goal_declaration": {},
        "tool_calls": [],
        "raw_model_content": "",
        "authority": "current_turn_only",
        "immutable": True,
    })
    trace_rows: list[dict[str, Any]] = []
    for index, item in enumerate(tool_trace or []):
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        trace_rows.append(
            {
                "index": index,
                "name": str(item.get("name") or ""),
                "args": _clean(dict(item.get("args") or {})),
                "result": _public_result(result),
            }
        )
    base = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "turn": int(turn),
        "user_text": str(user_text),
        "semantic_plan": semantic_plan,
        "tool_trace": trace_rows,
        "answer": str(answer or ""),
        # Only handles that actually crossed the customer-visible terminal
        # boundary may support a later explicit history recall. Tool result
        # handles in the audit trace are not automatically public evidence.
        "answer_evidence_handles": list(dict.fromkeys(
            str(value) for value in (answer_evidence_handles or []) if str(value)
        )),
        "status": str(status or ""),
        "operation_handles": [str(value) for value in (operation_handles or []) if value],
        "created_at": time(),
        "historical_only": True,
        "not_semantic_state": True,
    }
    base["event_id"] = f"trace:{_event_digest(base)}"
    return base


def _short_text(value: Any, *, max_chars: int = 180) -> str:
    text = str(value or "")
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def audit_cards(
    events: list[dict[str, Any]] | None,
    *,
    limit: int = MAX_AUDIT_DETAILED_CARDS,
    catalog_limit: int = MAX_AUDIT_CATALOG_EVENTS,
) -> list[dict[str, Any]]:
    """Return audit evidence without turning it into semantic state.

    Recent entries include full tool arguments.  Older entries remain visible
    as compact catalog rows so a user can return after many unrelated turns and
    refer to an old answer.  The Planner still decides *whether* and *how* the
    new user text corrects that historical event; the catalog never selects a
    current focus or task on its own.
    """
    history = normalize_turn_trace(events)[-max(1, int(catalog_limit)):]
    detailed_start = max(0, len(history) - max(1, int(limit)))
    cards: list[dict[str, Any]] = []
    for index, event in enumerate(history):
        plan = event.get("semantic_plan") if isinstance(event.get("semantic_plan"), dict) else {}
        calls = plan.get("tool_calls") if isinstance(plan.get("tool_calls"), list) else []
        full = index >= detailed_start
        tool_calls: list[dict[str, Any]] = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            row = {"name": str(call.get("name") or "")}
            if full:
                row["args"] = _clean(dict(call.get("args") or {}))
            tool_calls.append(row)
        result_handles: list[str] = []
        for item in event.get("tool_trace") or []:
            if not isinstance(item, dict):
                continue
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            result_handles.extend(str(handle) for handle in result.get("result_handles") or [] if handle)
        cards.append(
            {
                "trace_handle": str(event.get("event_id") or ""),
                "turn": int(event.get("turn") or 0),
                "user_text": _short_text(event.get("user_text"), max_chars=320 if full else 180),
                "plan_id": str(plan.get("plan_id") or ""),
                "tool_calls": tool_calls,
                "result_handles": list(dict.fromkeys(result_handles)),
                "operation_handles": list(event.get("operation_handles") or []),
                "answer": _short_text(event.get("answer"), max_chars=420 if full else 180),
                "status": str(event.get("status") or ""),
                "detail_level": "full" if full else "catalog",
                "historical_only": True,
            }
        )
    return cards
