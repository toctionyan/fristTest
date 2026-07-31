from agent_core.lifecycle.finalizer import _answer_from_terminal_tool


def test_goal_declaration_trace_does_not_require_business_evidence_handle():
    state = {
        "tool_trace": [
            {
                "name": "declare_turn_goals",
                "result": {"ok": True, "code": "TURN_GOALS_DECLARED", "data": {"goal_count": 1}},
            },
            {
                "name": "report_unsupported_request",
                "result": {"ok": True, "code": "UNSUPPORTED_CAPABILITY", "data": {"supported": False}},
            },
        ],
        "artifact_ledger": [],
        "current_tenant_id": "tenant-a",
        "current_user_id": "u001",
        "current_thread_id": "thread-a",
    }
    answer, error, handles = _answer_from_terminal_tool(
        state,
        {
            "name": "respond_to_user",
            "args": {"answer": "当前没有查询快递员手机号的精确能力。", "evidence_handles": []},
        },
    )
    assert error is None
    assert answer == "当前没有查询快递员手机号的精确能力。"
    assert handles == []
