from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def _exchange(turn: int) -> list[object]:
    call_id = f"call-{turn}"
    return [
        HumanMessage(content=f"用户第 {turn} 轮"),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"turn": turn}, "id": call_id}]),
        ToolMessage(content=f"result-{turn}", tool_call_id=call_id, name="lookup"),
        AIMessage(content=f"回答第 {turn} 轮"),
    ]


def test_generated_windows_preserve_complete_tool_exchanges() -> None:
    from agent_core.context.conversation_protocol import compile_provider_context, validate_provider_protocol

    for turns in (1, 3, 12, 13, 50, 100):
        messages = [message for turn in range(1, turns + 1) for message in _exchange(turn)]
        for limit in (1, 2, 3, 4, 11, 12, 13, 31):
            compiled = compile_provider_context(messages, max_messages=limit, max_chars=200_000)
            verdict = validate_provider_protocol(compiled.messages)
            assert verdict.ok, (turns, limit, verdict)
            assert any(isinstance(message, HumanMessage) and str(turns) in str(message.content) for message in compiled.messages)
            assert compiled.selected_exchange_count >= 1


def test_naive_tail_mutation_is_rejected_as_orphan_tool_result() -> None:
    from agent_core.context.conversation_protocol import validate_provider_protocol

    messages = _exchange(1)
    mutated = messages[2:]
    verdict = validate_provider_protocol(mutated)
    assert verdict.ok is False
    assert "orphan_tool_result" in verdict.errors


def test_tool_heavy_history_is_bounded_by_complete_user_exchanges_not_raw_message_count() -> None:
    from agent_core.context.conversation_protocol import compile_provider_context, validate_provider_protocol

    completed = [message for turn in range(1, 9) for message in _exchange(turn)]
    current = [
        HumanMessage(content="回到第 2 轮的对象"),
        AIMessage(content="", tool_calls=[{"name": "declare", "args": {}, "id": "current-goal"}]),
        ToolMessage(content="declared", tool_call_id="current-goal", name="declare"),
    ]
    compiled = compile_provider_context(
        [*completed, *current],
        max_messages=96,
        max_chars=200_000,
        max_exchanges=12,
        compact_completed_history=True,
    )

    assert validate_provider_protocol(compiled.messages).ok
    content = [str(getattr(message, "content", "")) for message in compiled.messages]
    assert "用户第 2 轮" in content
    assert "回答第 2 轮" in content
    assert "回到第 2 轮的对象" in content
    assert "declared" in content
    assert not any(value.startswith("result-") for value in content)
    assert compiled.selected_exchange_count == 9
