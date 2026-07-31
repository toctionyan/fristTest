"""Contract tests for the deterministic OpenAI-compatible integration model."""
from __future__ import annotations

import json

import pytest

from tests.integration.model_stub import chat_completions


def _assistant_message(payload: dict) -> dict:
    response = chat_completions(payload)
    choice = response["choices"][0]
    assert isinstance(choice, dict)
    message = choice["message"]
    assert isinstance(message, dict)
    return message


@pytest.mark.integration
def test_model_stub_emits_only_a_cancel_draft_candidate_for_disposable_order() -> None:
    message = _assistant_message(
        {
            "model": "deterministic-ci-model",
            "messages": [{"role": "user", "content": "请取消订单10003"}],
        }
    )

    calls = message.get("tool_calls")
    assert isinstance(calls, list) and len(calls) == 1
    call = calls[0]
    assert call["function"]["name"] == "prepare_cancel_order"
    args = json.loads(call["function"]["arguments"])
    assert args == {
        "target": {"mode": "entity_match", "attribute_span": "订单10003"},
        "reference_span": "订单10003",
        "action_span": "取消",
        "goal_ids": ["goal:primary"],
    }
    # The deterministic server may propose a Draft only; transaction input,
    # authority and command execution remain lifecycle/API responsibilities.
    assert "commit" not in call["function"]["name"]
    assert "authority" not in call["function"]["name"]


@pytest.mark.integration
def test_model_stub_returns_literal_evidence_for_semantic_verifier() -> None:
    user_text = "请取消订单10003"
    verifier_prompt = json.dumps(
        {
            "role": "capability_exactness_verifier",
            "USER_TEXT_UNTRUSTED": user_text,
        },
        ensure_ascii=False,
    )
    message = _assistant_message(
        {
            "messages": [{"role": "user", "content": verifier_prompt}],
        }
    )

    verdict = json.loads(message["content"])
    assert verdict == {
        "verdict": "exact",
        "evidence_span": user_text,
        "reason_code": "deterministic_ci_exact",
    }
