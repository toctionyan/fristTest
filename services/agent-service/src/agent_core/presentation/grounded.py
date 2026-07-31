"""Generic narrative projection from verified RuntimeOutcome; domain cards are module adapters."""
from __future__ import annotations
from typing import Any

def grounded_tool_summary(row: dict[str, Any]) -> str:
    result=row.get("result") if isinstance(row.get("result"),dict) else {}
    if not result.get("ok"):
        return str(result.get("message") or "当前操作未完成。")
    data=result.get("data") if isinstance(result.get("data"),dict) else {}
    outcome=result.get("runtime_outcome") if isinstance(result.get("runtime_outcome"),dict) else {}
    return str(outcome.get("customer_safe_summary") or data.get("message") or "已完成已验证处理。")

def grounded_answer(tool_trace: list[dict[str, Any]]) -> str:
    rows=[grounded_tool_summary(row) for row in tool_trace if isinstance(row,dict)]
    return "\n".join(row for row in rows if row)


def render_grounded_tool_answer(state: dict[str, Any]) -> str:
    """Render a safe fallback answer from already-classified tool observations.

    This is deliberately domain-neutral: rich ecommerce, ticket, SaaS, or
    other domain cards are released by module presentation adapters.  The
    Kernel fallback may use only the verified summary/message already present
    in each result and must never reconstruct domain facts from arbitrary
    payload fields.
    """
    trace = [row for row in list(state.get("tool_trace") or []) if isinstance(row, dict)]
    if trace:
        parts = [
            render_single_grounded_tool_result(str(row.get("name") or "tool"), row.get("result") if isinstance(row.get("result"), dict) else {})
            for row in trace
        ]
        rendered = [part for part in parts if part]
        if rendered:
            return "\n".join(rendered)
    text = str(state.get("current_user_input") or "").strip()
    if text in {"你好", "您好", "嗨", "hi", "hello"}:
        return "您好。我可以协助查询已启用模块提供的服务；涉及具体业务时会先核验再回答。"
    return "我会基于当前已启用的服务能力协助处理；涉及具体业务时会先核验再回答。"


def render_single_grounded_tool_result(tool_name: str, result: dict[str, Any]) -> str:
    """Return a generic, fact-preserving narration for one verified tool result.

    Domain modules own rich cards; Kernel only uses explicit message/summary data
    and never infers a domain field such as order, ticket, subscription or refund.
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return str((result or {}).get("message") or "当前操作未完成。")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    for field in ("message", "summary", "customer_safe_summary"):
        value = str(data.get(field) or result.get(field) or "").strip()
        if value:
            return value
    count = data.get("count")
    if isinstance(count, int):
        return f"已完成已验证查询，共 {count} 项。"
    return "已完成已验证处理。"
