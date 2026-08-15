from __future__ import annotations

import json
from types import SimpleNamespace

from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier
from agent_core.model_calls import structured_verifier_messages


USER_TEXT = "查下鼠标订单的退款记录，再看看键盘订单有没有发票"
GOALS = [
    {"goal_id": "g1", "evidence_span": "查下鼠标订单的退款记录"},
    {"goal_id": "g2", "evidence_span": "再看看键盘订单有没有发票"},
]


def _response(outcome_spans: object) -> SimpleNamespace:
    return SimpleNamespace(
        content=json.dumps(
            {
                "verdict": "exact",
                "outcome_spans": outcome_spans,
                "reason_code": "blind_inventory_exact",
            },
            ensure_ascii=False,
        )
    )


def test_release51_granularity_prompt_declares_strict_literal_string_array_contract() -> None:
    messages = structured_verifier_messages(
        role="turn_goal_granularity_inventory_verifier",
        instruction="inventory outcomes",
        decision_rules=("remain candidate blind",),
        payload={"USER_TEXT_UNTRUSTED": USER_TEXT},
    )

    policy = json.loads(messages[0].content)
    contract = policy["OUTPUT_CONTRACT"]
    schema = contract["schema"]
    assert schema["required"] == ["verdict", "outcome_spans", "reason_code"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["verdict"]["enum"] == ["exact", "clarify"]
    assert schema["properties"]["outcome_spans"]["type"] == "array"
    assert schema["properties"]["outcome_spans"]["items"] == {"type": "string"}
    assert any("verbatim" in rule for rule in contract["rules"])
    assert any("never return objects" in rule for rule in contract["rules"])
    assert any("Do not paraphrase" in rule for rule in contract["rules"])

    serialized_policy = json.dumps(policy, ensure_ascii=False, sort_keys=True)
    assert "退款" not in serialized_policy
    assert "发票" not in serialized_policy
    assert "tool_name" not in serialized_policy
    assert "capability" not in serialized_policy.lower()


def test_release51_real_verifier_boundary_repairs_paraphrase_without_candidate_authority(monkeypatch) -> None:
    responses = [
        _response(["查询鼠标订单退款情况", "查询键盘订单发票情况"]),
        _response(["查下鼠标订单的退款记录", "再看看键盘订单有没有发票"]),
    ]
    payloads: list[object] = []

    monkeypatch.setattr("agent_core.config.get_model", lambda: object())

    def invoke_model(**kwargs):
        payloads.append(kwargs["payload"])
        return responses.pop(0), {"purpose": kwargs["purpose"]}

    monkeypatch.setattr("agent_core.model_calls.invoke_model", invoke_model)

    verdict = ModelGoalGranularityVerifier().verify(
        user_text=USER_TEXT,
        goals=GOALS,
    )

    assert verdict.verdict == "exact"
    assert verdict.details["outcome_spans"] == [
        "查下鼠标订单的退款记录",
        "再看看键盘订单有没有发票",
    ]
    assert verdict.details["inventory_outcome_count"] == 2
    assert verdict.details["declared_goal_count"] == 2
    assert len(payloads) == 2

    first_policy = json.loads(payloads[0][0].content)
    second_policy = json.loads(payloads[1][0].content)
    for policy in (first_policy, second_policy):
        contract = policy["OUTPUT_CONTRACT"]
        assert contract["schema"]["properties"]["outcome_spans"]["items"] == {"type": "string"}
        assert any("exact contiguous characters" in rule for rule in contract["rules"])

    retry_request = json.loads(payloads[1][-1].content)
    assert "FORMAT_REPAIR" in retry_request
    serialized_payloads = json.dumps(
        [[message.content for message in payload] for payload in payloads],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "requested_effect" not in serialized_payloads
    assert '"g1"' not in serialized_payloads
    assert '"g2"' not in serialized_payloads


def test_release51_object_shaped_spans_remain_fail_closed(monkeypatch) -> None:
    responses = [
        _response([{"span": "查下鼠标订单的退款记录"}, {"span": "再看看键盘订单有没有发票"}]),
        _response([{"span": "查下鼠标订单的退款记录"}, {"span": "再看看键盘订单有没有发票"}]),
    ]

    monkeypatch.setattr("agent_core.config.get_model", lambda: object())
    monkeypatch.setattr(
        "agent_core.model_calls.invoke_model",
        lambda **kwargs: (responses.pop(0), {"purpose": kwargs["purpose"]}),
    )

    verdict = ModelGoalGranularityVerifier().verify(
        user_text=USER_TEXT,
        goals=GOALS,
    )

    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_granularity_inventory_missing_literal_spans"
    assert verdict.details["verifier_repair_attempted"] is True
