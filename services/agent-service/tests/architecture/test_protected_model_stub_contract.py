from __future__ import annotations

import json

from tests.integration.model_stub import _message_for


def _user(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def test_stub_declares_goals_before_emitting_a_business_candidate() -> None:
    planning = _message_for(_user("请取消订单10003"), {"declare_turn_goals"})
    call = planning["tool_calls"][0]["function"]
    assert call["name"] == "declare_turn_goals"
    goal = json.loads(call["arguments"])["goals"][0]
    assert goal["evidence_span"] == "请取消订单10003"
    assert goal["requested_effect"] == {
        "domain": "order",
        "operation": "cancel",
        "object_type": "order",
        "requested_outputs": [
            {
                "output_id": "order.cancellation",
                "evidence_span": "请取消订单10003",
            }
        ],
        "raw_description": "请取消订单10003",
    }

    greeting = _message_for(_user("你好"), {"declare_turn_goals"})
    greeting_goal = json.loads(greeting["tool_calls"][0]["function"]["arguments"])["goals"][0]
    assert greeting_goal["requested_effect"] == {
        "domain": "conversation",
        "operation": "respond",
        "object_type": "message",
        "requested_outputs": [
            {
                "output_id": "open",
                "evidence_span": "你好",
                "open_description": "你好",
            }
        ],
        "raw_description": "你好",
    }

    execution = _message_for(_user("请取消订单10003"), {"prepare_cancel_order"})
    candidate = json.loads(execution["tool_calls"][0]["function"]["arguments"])
    assert candidate["goal_ids"] == [goal["goal_id"]]


def test_stub_implements_all_protected_verifier_protocols() -> None:
    goal_prompt = json.dumps(
        {"role": "turn_goal_alignment_verifier", "USER_TEXT_UNTRUSTED": "你好"},
        ensure_ascii=False,
    )
    goal = json.loads(_message_for([{"role": "user", "content": goal_prompt}])["content"])
    assert goal == {
        "verdict": "exact",
        "evidence_spans": ["你好"],
        "missing_spans": [],
        "reason_code": "deterministic_ci_goal_complete",
    }

    answer_prompt = json.dumps(
        {"role": "answer_release_alignment_verifier", "USER_TEXT_UNTRUSTED": "你好"},
        ensure_ascii=False,
    )
    answer = json.loads(_message_for([{"role": "user", "content": answer_prompt}])["content"])
    assert answer["decision"] == "pass"
