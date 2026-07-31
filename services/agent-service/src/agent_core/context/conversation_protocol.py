from __future__ import annotations

"""Compile durable checkpoint messages into provider-safe exchanges.

An AI tool call and all of its tool results form one atomic protocol segment.
Windowing raw messages can split that segment and create provider-invalid
orphan tool results.  This module is the single owner of message selection and
pre-call protocol validation.
"""

from dataclasses import dataclass
import json
from typing import Any, Iterable, Sequence


_SUPPORTED_MESSAGE_TYPES = {"HumanMessage", "AIMessage", "ToolMessage"}


@dataclass(frozen=True)
class ProtocolVerdict:
    ok: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledProviderContext:
    messages: tuple[Any, ...]
    omitted_message_count: int
    selected_exchange_count: int
    total_exchange_count: int
    diagnostics: tuple[str, ...] = ()
    budget_overflow_for_latest_exchange: bool = False


def _message_name(message: Any) -> str:
    return message.__class__.__name__


def _tool_call_ids(message: Any) -> tuple[str, ...]:
    if _message_name(message) != "AIMessage":
        return ()
    rows = getattr(message, "tool_calls", None)
    if not isinstance(rows, list):
        additional = getattr(message, "additional_kwargs", None)
        rows = additional.get("tool_calls") if isinstance(additional, dict) else []
    ids: list[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        call_id = str(row.get("id") or row.get("tool_call_id") or "").strip()
        if call_id:
            ids.append(call_id)
    return tuple(dict.fromkeys(ids))


def _tool_result_id(message: Any) -> str:
    return str(getattr(message, "tool_call_id", "") or "").strip()


def _message_size(message: Any) -> int:
    content = getattr(message, "content", "")
    try:
        content_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    except Exception:
        content_text = str(content)
    calls = getattr(message, "tool_calls", None)
    try:
        call_text = json.dumps(calls or [], ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        call_text = str(calls or "")
    return len(content_text) + len(call_text) + 16


def validate_provider_protocol(messages: Sequence[Any] | Iterable[Any]) -> ProtocolVerdict:
    """Validate OpenAI-style tool-call linkage without invoking a provider."""
    pending: set[str] = set()
    errors: list[str] = []
    for message in messages:
        name = _message_name(message)
        if name not in _SUPPORTED_MESSAGE_TYPES:
            errors.append("unsupported_message_type")
            continue
        if name == "ToolMessage":
            result_id = _tool_result_id(message)
            if not pending:
                errors.append("orphan_tool_result")
            elif not result_id or result_id not in pending:
                errors.append("unexpected_tool_result")
            else:
                pending.remove(result_id)
            continue
        if pending:
            errors.append("incomplete_tool_exchange")
            pending.clear()
        if name == "AIMessage":
            pending.update(_tool_call_ids(message))
    if pending:
        errors.append("incomplete_tool_exchange")
    normalized = tuple(dict.fromkeys(errors))
    return ProtocolVerdict(ok=not normalized, errors=normalized)


def _raw_exchanges(messages: Sequence[Any]) -> list[list[Any]]:
    exchanges: list[list[Any]] = []
    current: list[Any] = []
    for message in messages:
        if _message_name(message) not in _SUPPORTED_MESSAGE_TYPES:
            continue
        if _message_name(message) == "HumanMessage" and current:
            exchanges.append(current)
            current = []
        current.append(message)
    if current:
        exchanges.append(current)
    return exchanges


def _sanitize_exchange(exchange: Sequence[Any], *, exchange_index: int) -> tuple[list[Any], list[str]]:
    """Remove corrupted protocol segments without synthesizing observations."""
    output: list[Any] = []
    diagnostics: list[str] = []
    index = 0
    while index < len(exchange):
        message = exchange[index]
        name = _message_name(message)
        if name == "ToolMessage":
            diagnostics.append(f"exchange:{exchange_index}:orphan_tool_result_dropped")
            index += 1
            continue
        if name != "AIMessage":
            output.append(message)
            index += 1
            continue
        call_ids = _tool_call_ids(message)
        if not call_ids:
            output.append(message)
            index += 1
            continue
        expected = set(call_ids)
        segment = [message]
        seen: set[str] = set()
        cursor = index + 1
        while cursor < len(exchange) and _message_name(exchange[cursor]) == "ToolMessage":
            tool_message = exchange[cursor]
            result_id = _tool_result_id(tool_message)
            if result_id in expected and result_id not in seen:
                segment.append(tool_message)
                seen.add(result_id)
            else:
                diagnostics.append(f"exchange:{exchange_index}:unexpected_tool_result_dropped")
            cursor += 1
        if seen == expected:
            output.extend(segment)
        else:
            diagnostics.append(f"exchange:{exchange_index}:incomplete_tool_exchange_dropped")
        index = cursor
    return output, diagnostics


def compile_provider_context(
    messages: Sequence[Any] | Iterable[Any],
    *,
    max_messages: int = 12,
    max_chars: int = 48_000,
    max_exchanges: int | None = None,
    compact_completed_history: bool = False,
) -> CompiledProviderContext:
    """Select newest complete exchanges under soft protocol-safe budgets.

    When requested, completed historical turns retain the exact user message
    and the final assistant answer while their already-consumed tool protocol
    is omitted.  The newest (currently executing) exchange is never compacted,
    so tool-call/result linkage remains intact for the next model step.
    """
    supported = [message for message in messages if _message_name(message) in _SUPPORTED_MESSAGE_TYPES]
    raw_exchanges = _raw_exchanges(supported)
    sanitized: list[list[Any]] = []
    diagnostics: list[str] = []
    for index, exchange in enumerate(raw_exchanges, start=1):
        clean, exchange_diagnostics = _sanitize_exchange(exchange, exchange_index=index)
        diagnostics.extend(exchange_diagnostics)
        if clean:
            sanitized.append(clean)

    if compact_completed_history and len(sanitized) > 1:
        compacted: list[list[Any]] = []
        for exchange in sanitized[:-1]:
            humans = [message for message in exchange if _message_name(message) == "HumanMessage"]
            final_answers = [
                message for message in exchange
                if _message_name(message) == "AIMessage"
                and not _tool_call_ids(message)
                and str(getattr(message, "content", "") or "").strip()
            ]
            compacted.append([*humans, *final_answers[-1:]] if humans and final_answers else exchange)
        sanitized = [*compacted, sanitized[-1]]

    selected_reversed: list[list[Any]] = []
    selected_messages = 0
    selected_chars = 0
    overflow = False
    message_budget = max(1, int(max_messages))
    char_budget = max(1, int(max_chars))
    exchange_budget = max(1, int(max_exchanges)) if max_exchanges is not None else None
    for exchange in reversed(sanitized):
        exchange_messages = len(exchange)
        exchange_chars = sum(_message_size(message) for message in exchange)
        fits = (
            selected_messages + exchange_messages <= message_budget
            and selected_chars + exchange_chars <= char_budget
            and (exchange_budget is None or len(selected_reversed) + 1 <= exchange_budget)
        )
        if selected_reversed and not fits:
            break
        if not selected_reversed and not fits:
            overflow = True
            diagnostics.append("latest_exchange_exceeds_soft_budget")
        selected_reversed.append(exchange)
        selected_messages += exchange_messages
        selected_chars += exchange_chars

    selected = [message for exchange in reversed(selected_reversed) for message in exchange]
    verdict = validate_provider_protocol(selected)
    if not verdict.ok:
        raise ValueError("conversation protocol compiler emitted invalid provider messages: " + ",".join(verdict.errors))
    return CompiledProviderContext(
        messages=tuple(selected),
        omitted_message_count=max(0, len(supported) - len(selected)),
        selected_exchange_count=len(selected_reversed),
        total_exchange_count=len(raw_exchanges),
        diagnostics=tuple(diagnostics),
        budget_overflow_for_latest_exchange=overflow,
    )


__all__ = ["CompiledProviderContext", "ProtocolVerdict", "compile_provider_context", "validate_provider_protocol"]
