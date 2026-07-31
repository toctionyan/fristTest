from typing import Any


def message_to_debug(message: Any) -> dict[str, Any]:
    data = {
        "type": message.__class__.__name__,
        "content": getattr(message, "content", None),
    }
    message_id = getattr(message, "id", None)
    if message_id:
        data["id"] = message_id
    name = getattr(message, "name", None)
    if name:
        data["name"] = name
    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id:
        data["tool_call_id"] = tool_call_id
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        data["tool_calls"] = tool_calls
    invalid_tool_calls = getattr(message, "invalid_tool_calls", None)
    if invalid_tool_calls:
        data["invalid_tool_calls"] = invalid_tool_calls
    additional_kwargs = getattr(message, "additional_kwargs", None)
    if additional_kwargs:
        data["additional_kwargs"] = additional_kwargs
    response_metadata = getattr(message, "response_metadata", None)
    if response_metadata:
        data["response_metadata"] = response_metadata
    usage_metadata = getattr(message, "usage_metadata", None)
    if usage_metadata:
        data["usage_metadata"] = usage_metadata
    return data


def llm_call_to_debug(node: str, purpose: str, input_messages: list[Any], response: Any) -> dict[str, Any]:
    return {
        "node": node,
        "purpose": purpose,
        "input_messages": [message_to_debug(message) for message in input_messages],
        "raw_response": message_to_debug(response),
    }
