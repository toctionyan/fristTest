from __future__ import annotations

from typing import Any


def build_audit_index(events: list[dict[str, Any]] | None, *, limit: int = 32) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in (events or [])[-max(1, limit):]:
        if not isinstance(item, dict):
            continue
        trace_handle = str(item.get("trace_id") or item.get("event_id") or item.get("plan_id") or "")
        if not trace_handle:
            continue
        user_text = str(item.get("user_text") or item.get("input") or item.get("current_user_input") or "")
        answer = str(item.get("answer") or item.get("final_answer") or "")
        tool_names = [str(row.get("name") or "") for row in item.get("tool_trace") or [] if isinstance(row, dict) and str(row.get("name") or "")]
        result_handles = [str(handle) for handle in item.get("answer_evidence_handles") or [] if str(handle)]
        rows.append({
            "trace_handle": trace_handle,
            "turn": item.get("turn_index") or item.get("turn"),
            "user_summary": user_text[:180],
            "answer_summary": answer[:220],
            "tool_names": list(dict.fromkeys(tool_names))[:8],
            "result_handles": list(dict.fromkeys(result_handles))[:12],
            "detail_level": "summary",
        })
    return rows


def inspect_audit_event(state: dict[str, Any], *, trace_handle: str, reason_span: str) -> dict[str, Any]:
    """Return a scoped, redacted history summary only on an indexed handle.

    Historical material is deliberately labelled non-authoritative: it can help
    explain an earlier answer but cannot silently become the current user's
    reference target or a business fact.
    """
    user_text = str(state.get("current_user_input") or "")
    if not reason_span or reason_span not in user_text:
        return {"ok": False, "code": "AUDIT_REASON_NOT_IN_CURRENT_TURN", "message": "历史审计读取必须引用当前用户原话。"}
    index = state.get("context_bundle") if isinstance(state.get("context_bundle"), dict) else {}
    allowed = {str(row.get("trace_handle") or "") for row in (index.get("omitted_context_audit") or {}).get("audit_index") or [] if isinstance(row, dict)}
    if trace_handle not in allowed:
        return {"ok": False, "code": "AUDIT_HANDLE_NOT_IN_CONTEXT_BUNDLE", "message": "只能读取当前上下文索引中的历史记录。"}
    for event in reversed(state.get("conversation_event_log") or []):
        if not isinstance(event, dict):
            continue
        candidate = str(event.get("trace_id") or event.get("event_id") or event.get("plan_id") or "")
        if candidate != trace_handle:
            continue
        return {
            "ok": True,
            "data": {
                "trace_handle": trace_handle,
                "historical_only": True,
                "not_current_semantic_authority": True,
                "user_summary": str(event.get("user_text") or event.get("input") or "")[:500],
                "answer_summary": str(event.get("answer") or event.get("final_answer") or "")[:700],
                "tool_names": [str(row.get("name") or "") for row in event.get("tool_trace") or [] if isinstance(row, dict) and str(row.get("name") or "")],
                "result_handles": [str(handle) for handle in event.get("answer_evidence_handles") or [] if str(handle)],
            },
        }
    return {"ok": False, "code": "AUDIT_EVENT_NOT_FOUND", "message": "该历史记录已不可用。"}
