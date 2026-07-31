from __future__ import annotations

"""Small, dependency-free helpers shared by node runtimes."""

import os
from typing import Any

from agent_core.kernel.loop_contract import MAX_AGENT_LOOP_STEPS_DEFAULT, MAX_SAME_CALLS_PER_TURN_DEFAULT
from agent_core.business import get_business_port as _get_business_port
from agent_core.kernel.decision_trace import append_decision

try:
    from langchain_core.messages import AIMessage
except Exception:  # pragma: no cover
    AIMessage = None  # type: ignore

def latest_human_text(state: dict[str, Any]) -> str:
    for message in reversed(state.get("messages") or []):
        if message.__class__.__name__ == "HumanMessage":
            return str(getattr(message, "content", "") or "").strip()
    return str(state.get("current_user_input") or "").strip()

def last_human_index(messages: list[Any]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].__class__.__name__ == "HumanMessage":
            return index
    return -1

def tool_calls(message: Any) -> list[dict[str, Any]]:
    raw = getattr(message, "tool_calls", None) or []
    return [dict(row) for row in raw if isinstance(row, dict) and row.get("name")]

def as_ai_message(response: Any, raw_calls: list[dict[str, Any]]) -> Any:
    if AIMessage is None:
        return response
    if response.__class__.__name__ == "AIMessage":
        return response
    return AIMessage(content=str(getattr(response, "content", "") or ""), tool_calls=raw_calls)

def max_loop_steps() -> int:
    raw = os.getenv("AGENT_LOOP_MAX_STEPS", str(MAX_AGENT_LOOP_STEPS_DEFAULT))
    try:
        return max(4, min(6, int(raw)))
    except ValueError:
        return MAX_AGENT_LOOP_STEPS_DEFAULT

def max_same_calls() -> int:
    raw = os.getenv("AGENT_LOOP_MAX_SAME_CALLS", str(MAX_SAME_CALLS_PER_TURN_DEFAULT))
    try:
        return max(1, min(3, int(raw)))
    except ValueError:
        return MAX_SAME_CALLS_PER_TURN_DEFAULT

def get_business_port():
    """Return the Composition-registered domain-neutral BusinessPort."""
    return _get_business_port()

