from __future__ import annotations

from agent_modules.ecommerce.shared import consultation
from agent_modules.ecommerce.shared.context import _require_current_or_resumed_span


def _state(text: str) -> dict:
    return {
        "current_tenant_id": "default",
        "current_user_id": "u001",
        "current_thread_id": "thread-general-policy",
        "current_role": "customer",
        "current_user_input": text,
        "turn_index": 1,
        "artifact_ledger": [],
    }


def test_general_policy_consultation_does_not_require_one_order(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_retrieve(query: str, *, top_k: int, filters: dict) -> list[dict]:
        calls.append({"query": query, "top_k": top_k, "filters": filters})
        return [{
            "doc_id": "policy_refund_general",
            "chunk_id": "policy_refund_general:1",
            "title": "退款政策",
            "source": "测试知识库",
            "content": "符合退货条件的订单可按退款政策申请处理。",
            "score": 0.99,
            "metadata": {"policy_domain": "refund"},
        }]

    monkeypatch.setattr(consultation, "retrieve", fake_retrieve)
    result = consultation.execute_consult_refund_policy(
        _state("一般退款规则是什么？"),
        {
            "target": {"mode": "all_orders"},
            "reference_span": "一般退款规则",
            "issue_span": "",
            "question_span": "一般退款规则是什么",
        },
    )

    assert result["ok"] is True
    assert result["data"]["policy_scope"] == "general"
    assert result["data"]["order"] == {
        "order_id": "policy:refund",
        "product_name": "通用退款政策",
        "status": "",
    }
    assert result["data"]["knowledge_available"] is True
    assert result["data"]["result_handle"].startswith("h_result:")
    assert len(result["ledger_entries"]) == 1
    assert result["ledger_entries"][0]["member_handles"] == []
    assert calls and all(call["filters"]["policy_domain"] == "refund" for call in calls)


def test_resumed_clarification_may_reuse_only_the_suspended_question_span() -> None:
    state = {
        **_state("改回鼠标"),
        "state_schema_version": 2,
        "goal_blockers": [{
            "blocker_id": "blocker:refund-target:target",
            "goal_id": "goal:refund-target",
            "status": "OPEN",
            "missing_kind": "target",
            "source_user_request": "它能退吗？",
        }],
    }

    assert _require_current_or_resumed_span(state, "它能退吗", field="退款资格询问") is None
    error = _require_current_or_resumed_span(state, "帮我提交退款", field="退款资格询问")
    assert error and error["code"] == "SOURCE_SPAN_NOT_IN_CURRENT_USER_MESSAGE"
