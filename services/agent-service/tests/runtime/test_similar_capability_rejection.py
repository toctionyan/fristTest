from __future__ import annotations

from typing import Any

import pytest

from agent_core.composition import get_runtime_registry
from agent_core.runtime.capability_gate import issue_execution_permit


class UnsupportedVerifier:
    def __init__(self, span: str):
        self.span = span

    def verify(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "verdict": "unsupported",
            "evidence_span": self.span,
            "reason_code": "nearby_capability_is_not_exact",
            "source": "test",
            "independent": True,
        }


class ClarifyVerifier:
    def __init__(self, span: str):
        self.span = span

    def verify(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "verdict": "clarify",
            "evidence_span": self.span,
            "reason_code": "target_or_effect_ambiguous",
            "source": "test",
            "independent": True,
        }


def _state(text: str, verifier: Any) -> dict[str, Any]:
    return {
        "current_user_input": text,
        "current_thread_id": "thread-similar",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "turn_index": 9,
        "artifact_ledger": [],
        "semantic_capability_verifier": verifier,
    }


def test_courier_phone_cannot_be_satisfied_by_logistics_tool():
    state = _state("快递员电话是多少？", UnsupportedVerifier("快递员电话"))
    decision = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args={
            "target": {"mode": "entity_match", "attribute_span": "快递员"},
            "expected_shape": "collection",
            "reference_span": "快递员",
            "query": {},
        },
        effect_id="effect:similar:1",
        capability_registry=get_runtime_registry().capabilities,
    )
    assert decision.permitted is False
    assert decision.rejection["code"] == "CAPABILITY_UNAVAILABLE"
    assert "相近工具" in decision.rejection["message"]
    assert decision.match_proof["semantic_verdict"]["verdict"] == "unsupported"


def test_refund_arrival_question_cannot_create_refund_draft():
    state = _state("退款什么时候到账？", UnsupportedVerifier("退款什么时候到账"))
    decision = issue_execution_permit(
        state=state,
        tool_name="prepare_refund",
        args={
            "target": {"mode": "entity_match", "attribute_span": "退款"},
            "reference_span": "退款",
            "action_span": "退款",
            "reason_span": "到账时间咨询",
        },
        effect_id="effect:similar:2",
        capability_registry=get_runtime_registry().capabilities,
    )
    assert decision.permitted is False
    assert decision.rejection["code"] == "CAPABILITY_UNAVAILABLE"
    assert decision.match_proof["candidate_tool"] == "prepare_refund"
    assert decision.match_proof["semantic_verdict"]["verdict"] == "unsupported"


def test_ambiguous_short_action_requires_clarification_not_guessing_target():
    state = _state("退了吧", ClarifyVerifier("退了"))
    decision = issue_execution_permit(
        state=state,
        tool_name="prepare_refund",
        args={
            "target": {"mode": "entity_match", "attribute_span": "这个"},
            "reference_span": "这个",
            "action_span": "退了",
            "reason_span": "用户要求退",
        },
        effect_id="effect:similar:3",
        capability_registry=get_runtime_registry().capabilities,
    )
    assert decision.permitted is False
    assert decision.rejection["code"] == "CAPABILITY_SEMANTIC_CLARIFICATION_REQUIRED"
    assert "澄清" in decision.rejection["message"]


def test_registry_narrows_similar_capabilities_before_model_selection():
    registry = get_runtime_registry().capabilities

    logistics = registry.discover_surface([{
        "goal_id": "goal-logistics",
        "goal_type": "query",
        "evidence_span": "哪些在路上",
        "description": "查询哪些订单还在路上",
    }])
    refund = registry.discover_surface([{
        "goal_id": "goal-refund",
        "goal_type": "consult",
        "evidence_span": "可以退货退款吗",
        "description": "判断当前订单能否退款",
    }])

    assert logistics["tool_names"] == ["get_order_logistics"]
    assert refund["tool_names"] == ["evaluate_refund_eligibility"]
    assert not ({"consult_invoice_policy", "consult_refund_policy", "consult_after_sales_policy", "consult_warranty_policy"} & set(refund["tool_names"]))
    assert "prepare_refund" not in refund["tool_names"]


def test_model_authored_goal_description_cannot_hijack_capability_discovery():
    surface = get_runtime_registry().capabilities.discover_surface([{
        "goal_id": "goal-refund",
        "goal_type": "consult",
        "evidence_span": "可以退货退款吗",
        # This nearby-capability wording is deliberately model-authored and
        # therefore must not override the literal user evidence above.
        "description": "咨询退款政策并解释退货规则",
    }])

    assert surface["tool_names"] == ["evaluate_refund_eligibility"]
    assert "consult_refund_policy" not in surface["tool_names"]


def test_registry_reports_unsupported_instead_of_nearest_action_substitution():
    surface = get_runtime_registry().capabilities.discover_surface([{
        "goal_id": "goal-address",
        "goal_type": "action",
        "evidence_span": "修改收货地址",
        "description": "修改订单收货地址",
    }])

    assert surface["tool_names"] == ["report_unsupported_request"]
    assert surface["unsupported_goal_ids"] == ["goal-address"]
    assert surface["goals"][0]["status"] == "unsupported"


def test_validated_elliptical_correction_uses_semantic_goal_for_discovery_only() -> None:
    surface = get_runtime_registry().capabilities.discover_surface([{
        "goal_id": "goal-correct-order",
        "goal_type": "query",
        "evidence_span": "不对，改成10003",
        "description": "查询订单10003的状态",
    }])

    assert surface["tool_names"] == ["list_orders"]
    goal = surface["goals"][0]
    assert goal["status"] == "semantic_goal_candidates"
    selected = next(row for row in goal["ranked_candidates"] if row["tool_name"] == "list_orders")
    assert selected["semantic_exact_markers"]


def test_validated_goal_description_normalizes_question_modal_without_losing_intent() -> None:
    surface = get_runtime_registry().capabilities.discover_surface([{
        "goal_id": "goal-return-to-invoice-policy",
        "goal_type": "query",
        # A valid return span can identify only the previously visible member;
        # the independently validated description still owns the current
        # question predicate.
        "evidence_span": "便宜的杯子",
        "description": "查询定制马克杯（订单10004）能否开发票",
    }])

    assert surface["tool_names"] == ["consult_invoice_policy"]
    goal = surface["goals"][0]
    assert goal["status"] == "semantic_goal_candidates"
    selected = next(
        row for row in goal["ranked_candidates"]
        if row["tool_name"] == "consult_invoice_policy"
    )
    assert selected["semantic_exact_markers"] == ["能开发票吗"]


@pytest.mark.parametrize("text", ["能否修改收货地址", "能不能改收货地址", "可否改收货地址"])
def test_question_modal_normalization_does_not_substitute_nearby_unknown_capability(text: str) -> None:
    surface = get_runtime_registry().capabilities.discover_surface([{
        "goal_id": "goal-unsupported-address",
        "goal_type": "query",
        "evidence_span": "这个",
        "description": text,
    }])

    assert surface["tool_names"] == ["report_unsupported_request"]
    assert surface["unsupported_goal_ids"] == ["goal-unsupported-address"]


def test_capability_gate_rejects_tool_outside_current_goal_surface():
    registry = get_runtime_registry().capabilities
    surface = registry.discover_surface([{
        "goal_id": "goal-logistics",
        "goal_type": "query",
        "evidence_span": "哪些在路上",
        "description": "查询哪些订单还在路上",
    }])
    state = {
        **_state("哪些在路上", UnsupportedVerifier("哪些在路上")),
        "capability_surface": surface,
        "current_turn_plan": {
            "effects": [{"effect_id": "effect:surface", "goal_ids": ["goal-logistics"]}],
        },
    }
    decision = issue_execution_permit(
        state=state,
        tool_name="list_orders",
        args={
            "target": {"mode": "all_orders"},
            "expected_shape": "collection",
            "reference_span": "哪些在路上",
        },
        effect_id="effect:surface",
        capability_registry=registry,
    )

    assert decision.permitted is False
    assert decision.rejection["code"] == "CAPABILITY_NOT_AVAILABLE_IN_GOAL_SURFACE"
    assert decision.match_proof["capability_surface"]["allowed"] is False


@pytest.mark.parametrize(
    ("goal_type", "text", "expected_tools"),
    [
        ("query", "我都买了什么", {"list_orders"}),
        ("query", "哪些在路上", {"get_order_logistics"}),
        ("consult", "可以退货退款吗", {"evaluate_refund_eligibility"}),
        ("consult", "退款规则是什么", {"consult_refund_policy"}),
        ("action", "帮我申请退款", {"prepare_refund"}),
        ("action", "帮我准备退款但先不要提交", {"prepare_refund"}),
        ("query", "退款什么时候到账", {"list_refunds"}),
        ("query", "订单10004的发票进度", {"list_invoices"}),
        ("consult", "订单10004能开发票吗，我只问发票，不要退款售后", {"consult_invoice_policy"}),
        # Target qualifiers and a one-character modal variant must not hide an
        # otherwise registered capability from the bounded surface.
        ("consult", "查订单10004能不能开发票", {"consult_invoice_policy"}),
        # Goal taxonomies are model-authored; an exact read-only policy
        # question remains available when declared as query instead of consult.
        ("query", "回到便宜的杯子，它能开发票吗", {"consult_invoice_policy"}),
        ("action", "修改收货地址", {"report_unsupported_request"}),
        ("consult", "可以换货吗", {"report_unsupported_request"}),
        # Selection is a prerequisite, eligibility is the actual consultation.
        ("consult", "其中最便宜的可以退款吗", {"list_orders", "evaluate_refund_eligibility"}),
    ],
)
def test_capability_confusion_matrix_exposes_only_semantically_valid_surface(
    goal_type: str,
    text: str,
    expected_tools: set[str],
) -> None:
    surface = get_runtime_registry().capabilities.discover_surface([{
        "goal_id": "goal-matrix",
        "goal_type": goal_type,
        "evidence_span": text,
        "description": text,
    }])

    assert set(surface["tool_names"]) == expected_tools
    assert len(surface["tool_names"]) <= 4


def test_capability_discovery_keeps_multi_intent_goals_separate() -> None:
    surface = get_runtime_registry().capabilities.discover_surface([
        {
            "goal_id": "orders",
            "goal_type": "query",
            "evidence_span": "查一下我的订单",
            "description": "查询订单列表",
        },
        {
            "goal_id": "logistics",
            "goal_type": "query",
            "evidence_span": "再查物流到哪了",
            "description": "查询物流",
        },
    ])

    assert surface["tool_names"] == ["list_orders", "get_order_logistics"]
    assert [row["candidate_tools"] for row in surface["goals"]] == [
        ["list_orders"],
        ["get_order_logistics"],
    ]


def test_prerequisite_support_does_not_gain_goal_completion_authority() -> None:
    contract = get_runtime_registry().capabilities.contract_for_tool("list_orders")

    assert contract is not None
    assert "consult" in contract.goal_support_types
    assert "consult" not in contract.goal_completion_types
