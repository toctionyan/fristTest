from __future__ import annotations

from typing import Any

import pytest

from agent_modules.ecommerce.shared import context as context_module


ROWS = [
    {"order_id": "10002", "product_name": "机械键盘", "product_id": "kb-mech", "version": 1},
    {"order_id": "11003", "product_name": "键盘 Pro", "product_id": "kb-pro", "version": 1},
    {"order_id": "11004", "product_name": "键盘保护套", "product_id": "kb-cover", "version": 1},
]


def _state(text: str) -> dict[str, Any]:
    return {
        "current_tenant_id": "default",
        "current_user_id": "u001",
        "current_thread_id": "thread-stage3",
        "current_user_input": text,
        "turn_index": 3,
        "artifact_ledger": [],
        "context_health": {"transactions": "ok"},
    }


def _stub_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        context_module,
        "_list_orders",
        lambda state: ({"ok": True, "rows": [dict(row) for row in ROWS]}, []),
    )


def test_fuzzy_singleton_remains_available_as_read_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    target, error = context_module._target_members(
        _state("机械键帽坏了"),
        {"mode": "entity_match", "attribute_span": "机械键帽坏了"},
        expected_shape="one",
        allowed_resource_types={"order"},
        target_authority="read",
    )

    assert error is None
    assert target is not None
    assert len(target["member_handles"]) == 1
    assert target["match_proof"]["basis"] == "fuzzy_lexical_recall"
    assert target["match_proof"]["verified_for_write"] is False


def test_fuzzy_singleton_is_rejected_for_state_changing_action(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    target, error = context_module._target_members(
        _state("机械键帽坏了，帮我退"),
        {"mode": "entity_match", "attribute_span": "机械键帽坏了"},
        expected_shape="collection",
        allowed_resource_types={"order"},
        target_authority="write",
    )

    assert target is None
    assert error is not None
    assert error["code"] == "CONTEXT_TARGET_NOT_VERIFIED_FOR_WRITE"
    assert error["match_proof"]["basis"] == "fuzzy_lexical_recall"
    assert error["candidates"] == [
        {"order_id": "10002", "label": "机械键盘"},
    ]


def test_fuzzy_singleton_is_rejected_for_refund_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    target, error = context_module._target_members(
        _state("机械键帽那个能退吗"),
        {"mode": "entity_match", "attribute_span": "机械键帽"},
        expected_shape="one",
        allowed_resource_types={"order"},
        target_authority="decision",
    )

    assert target is None
    assert error is not None
    assert error["code"] == "CONTEXT_TARGET_NOT_VERIFIED_FOR_WRITE"


def test_exact_order_id_can_form_a_verified_write_target(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    target, error = context_module._target_members(
        _state("把订单 10002 退了"),
        {"mode": "entity_match", "attribute_span": "10002"},
        expected_shape="collection",
        allowed_resource_types={"order"},
        target_authority="write",
    )

    assert error is None
    assert target is not None
    assert len(target["member_handles"]) == 1
    assert target["match_proof"]["basis"] == "exact_order_id"
    assert target["match_proof"]["verified_for_write"] is True


def test_full_catalog_name_can_form_a_verified_write_target(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    target, error = context_module._target_members(
        _state("机械键盘坏了，帮我退"),
        {"mode": "entity_match", "attribute_span": "机械键盘"},
        expected_shape="collection",
        allowed_resource_types={"order"},
        target_authority="write",
    )

    assert error is None
    assert target is not None
    assert target["match_proof"]["basis"] in {"canonical_catalog_value", "scoped_catalog_substring"}
    assert target["match_proof"]["verified_for_write"] is True


def test_prepare_action_wires_write_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_modules.ecommerce.shared import prepare_actions

    observed: dict[str, Any] = {}

    def reject(*args: Any, **kwargs: Any):
        observed.update(kwargs)
        return None, {
            "ok": False,
            "code": "CONTEXT_TARGET_NOT_VERIFIED_FOR_WRITE",
            "message": "clarify",
            "data": {},
        }

    monkeypatch.setattr(prepare_actions, "_target_members", reject)
    result = prepare_actions.execute_prepare_refund(
        _state("机械键帽坏了，申请退款"),
        {
            "reference_span": "机械键帽",
            "action_span": "申请退款",
            "target": {"mode": "entity_match", "attribute_span": "机械键帽"},
        },
    )

    assert observed["target_authority"] == "write"
    assert result["code"] == "CONTEXT_TARGET_NOT_VERIFIED_FOR_WRITE"


def test_refund_eligibility_wires_decision_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_modules.ecommerce.shared import refund_eligibility

    observed: dict[str, Any] = {}

    def reject(*args: Any, **kwargs: Any):
        observed.update(kwargs)
        return None, {
            "ok": False,
            "code": "CONTEXT_TARGET_NOT_VERIFIED_FOR_WRITE",
            "message": "clarify",
            "data": {},
        }

    monkeypatch.setattr(refund_eligibility, "_target_members", reject)
    result = refund_eligibility.execute_evaluate_refund_eligibility(
        _state("机械键帽那个能退吗"),
        {
            "reference_span": "机械键帽",
            "question_span": "能退吗",
            "reason_span": "",
            "target": {"mode": "entity_match", "attribute_span": "机械键帽"},
        },
    )

    assert observed["target_authority"] == "decision"
    assert result["code"] == "CONTEXT_TARGET_NOT_VERIFIED_FOR_WRITE"


def test_classifier_alias_is_candidate_only_for_write(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"order_id": "10004", "product_name": "定制马克杯", "product_id": "mug", "version": 1},
    ]
    monkeypatch.setattr(
        context_module,
        "_list_orders",
        lambda state: ({"ok": True, "rows": [dict(row) for row in rows]}, []),
    )
    target, error = context_module._target_members(
        _state("杯子坏了，帮我退"),
        {"mode": "entity_match", "attribute_span": "杯子"},
        expected_shape="collection",
        allowed_resource_types={"order"},
        target_authority="write",
    )

    assert target is None
    assert error is not None
    assert error["code"] == "CONTEXT_TARGET_NOT_VERIFIED_FOR_WRITE"
    assert error["match_proof"]["basis"] == "classifier_alias_recall"


def test_explicit_shared_catalog_token_does_not_choose_one_of_multiple_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_orders(monkeypatch)
    target, error = context_module._target_members(
        _state("键盘坏了，帮我退"),
        {"mode": "entity_match", "attribute_span": "键盘"},
        expected_shape="one",
        allowed_resource_types={"order"},
        target_authority="write",
    )

    assert target is None
    assert error is not None
    assert error["code"] == "CONTEXT_TARGET_NOT_UNIQUE"
    assert len(error["candidates"]) == 3


def test_unverified_write_target_routes_to_clarification() -> None:
    from agent_core.lifecycle.execution_disposition import classify_execution_disposition

    result = {
        "ok": False,
        "code": "CONTEXT_TARGET_NOT_VERIFIED_FOR_WRITE",
        "message": "请确认对象",
        "candidates": [{"order_id": "10002", "label": "机械键盘"}],
    }
    disposition = classify_execution_disposition(
        state={"agent_loop_step": 1, "agent_loop_max_steps": 6},
        tool_name="prepare_refund",
        tool_signature="prepare_refund:fuzzy",
        result=result,
    )

    assert disposition["disposition"] == "needs_clarification"
    assert disposition["may_execute_user_effect"] is False
    assert disposition["runtime_action"] == "return_limited_observation"
