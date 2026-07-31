from __future__ import annotations

from langchain_core.messages import ToolMessage

from tests.support.scripted_chat_model import ScriptedChatModel


def test_scripted_model_can_chain_an_opaque_handle_returned_by_previous_tool() -> None:
    model = ScriptedChatModel([
        {
            "tool_calls": [{
                "name": "list_orders",
                "args": {
                    "target": {
                        "mode": "set_operation",
                        "operator": "take",
                        "left_handle": "$last_tool.data.result_handle",
                        "limit": 1,
                    }
                },
                "id": "take-one",
            }]
        }
    ])
    model.bind_tools([{"type": "function", "function": {"name": "list_orders", "parameters": {}}}])

    response = model.invoke([
        ToolMessage(
            content='{"ok": true, "data": {"result_handle": "h_result:runtime-generated"}}',
            tool_call_id="sort-orders",
        ),
        ToolMessage(
            content='{"ok": true, "data": {"goal_count": 1}}',
            tool_call_id="declare-goals",
        ),
    ])

    assert response.tool_calls[0]["args"]["target"]["left_handle"] == "h_result:runtime-generated"


def test_scripted_model_can_chain_a_visible_handle_from_the_previous_turn() -> None:
    model = ScriptedChatModel(
        [{
            "tool_calls": [{
                "name": "evaluate_refund_eligibility",
                "args": {
                    "target": {
                        "mode": "collection",
                        "left_handle": "$previous_turn.data.result_handle",
                    }
                },
                "id": "eligibility",
            }]
        }],
        previous_turn_data={"result_handle": "h_result:previous-visible"},
    )
    model.bind_tools([{
        "type": "function",
        "function": {"name": "evaluate_refund_eligibility", "parameters": {}},
    }])

    response = model.invoke([])

    assert response.tool_calls[0]["args"]["target"]["left_handle"] == "h_result:previous-visible"
