from __future__ import annotations

import json
from types import SimpleNamespace

from agent_core.kernel.capability import ToolCapabilityContract
from agent_core.runtime import semantic_capability_verifier as verifier_module
from agent_modules.ecommerce.shared.context import _match_orders


def test_entity_match_resolves_catalog_token_inside_a_predicate_phrase() -> None:
    rows = [
        {"order_id": "10004", "product_name": "定制马克杯"},
        {"order_id": "10003", "product_name": "无线鼠标"},
        {"order_id": "10002", "product_name": "机械键盘"},
        {"order_id": "10001", "product_name": "蓝牙耳机"},
    ]

    assert [row["order_id"] for row in _match_orders(rows, "键盘售后政策")] == ["10002"]
    assert [row["order_id"] for row in _match_orders(rows, "耳机退款资格")] == ["10001"]
    assert [row["order_id"] for row in _match_orders(rows, "杯子发票政策")] == ["10004"]
    assert _match_orders(rows, "售后政策") == []


def test_visible_eligibility_result_can_feed_a_followup_action_target() -> None:
    from agent_core.context.visible_result_refs import mark_visible_result_refs
    from agent_core.ledger import artifact_entry, eligibility_entry
    from agent_modules.ecommerce.shared.context import _target_members

    scope = {"tenant_id": "default", "user_id": "u001", "thread_id": "thread-eligibility-lineage"}
    order = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",
        facts={"order_id": "10002"}, scope=scope, turn=2, source="test",
        handle="h_order:10002",
    )
    eligibility = eligibility_entry(
        action_id="create_refund", operation="refund", target_handle=order["handle"],
        input_values={}, preview={"decision": "ALLOWED"}, scope=scope, turn=2,
        label="机械键盘退款资格", handle="h_eligibility:10002",
    )
    state = {
        "current_tenant_id": scope["tenant_id"], "current_user_id": scope["user_id"],
        "current_thread_id": scope["thread_id"], "turn_index": 3,
        "current_user_input": "帮我准备退款，但先不要提交。",
        "artifact_ledger": [order, eligibility],
    }
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"], state={**state, "turn_index": 2},
        evidence_handles=[eligibility["handle"]],
    )

    target, error = _target_members(
        state,
        {"mode": "collection", "left_handle": eligibility["handle"]},
        expected_shape="collection",
        allowed_resource_types={"order"},
    )

    assert error is None
    assert target and target["member_handles"] == [order["handle"]]


def test_verified_context_exposes_visible_scope_recency_and_opaque_ref() -> None:
    from agent_core.context.visible_result_refs import mark_visible_result_refs
    from agent_core.ledger import artifact_entry, result_entry

    scope = {"tenant_id": "default", "user_id": "u001", "thread_id": "thread-scope-verifier"}
    order = artifact_entry(
        resource_type="order", resource_id="10001", label="蓝牙耳机（订单 10001）",
        facts={"order_id": "10001"}, scope=scope, turn=2, source="test", handle="h_order:10001",
    )
    visible = result_entry(
        capability="ecommerce.order.logistics", member_handles=[order["handle"]], labels=[order["label"]],
        scope=scope, turn=2, source_target={"mode": "collection"}, handle="h_result:in-transit",
    )
    state = {
        "current_tenant_id": scope["tenant_id"], "current_user_id": scope["user_id"],
        "current_thread_id": scope["thread_id"], "turn_index": 3, "artifact_ledger": [order, visible],
    }
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"], state={**state, "turn_index": 2}, evidence_handles=[visible["handle"]],
    )

    rows = verifier_module._verified_context(state)
    ref = next(row for row in rows if row.get("kind") == "visible_result_ref")

    assert ref["result_ref"] == "h_result:in-transit"
    assert ref["is_latest_visible_turn"] is True
    assert ref["discourse_recency_rank"] == 1


def test_older_visible_scope_requires_formal_explicit_return_binding() -> None:
    from agent_core.context.visible_result_refs import mark_visible_result_refs
    from agent_core.ledger import artifact_entry, result_entry
    from agent_core.runtime.capability_gate import _visible_reference_proof

    scope = {"tenant_id": "default", "user_id": "u001", "thread_id": "thread-old-scope"}
    older_order = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",
        facts={"order_id": "10002"}, scope=scope, turn=1, source="test", handle="h_order:10002",
    )
    latest_order = artifact_entry(
        resource_type="order", resource_id="10001", label="蓝牙耳机（订单 10001）",
        facts={"order_id": "10001"}, scope=scope, turn=2, source="test", handle="h_order:10001",
    )
    older = result_entry(
        capability="ecommerce.orders.list", member_handles=[older_order["handle"], latest_order["handle"]],
        labels=[older_order["label"], latest_order["label"]], scope=scope, turn=1,
        source_target={"mode": "all_orders"}, handle="h_result:all",
    )
    latest = result_entry(
        capability="ecommerce.order.logistics", member_handles=[latest_order["handle"]], labels=[latest_order["label"]],
        scope=scope, turn=2, source_target={"mode": "collection"}, handle="h_result:in-transit",
    )
    state = {
        "current_tenant_id": scope["tenant_id"], "current_user_id": scope["user_id"],
        "current_thread_id": scope["thread_id"], "turn_index": 3, "current_user_input": "其中最贵的是哪个？",
        "artifact_ledger": [older_order, latest_order, older, latest],
    }
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"], state={**state, "turn_index": 1}, evidence_handles=[older["handle"]],
    )
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"], state={**state, "turn_index": 2}, evidence_handles=[latest["handle"]],
    )

    proof = _visible_reference_proof(
        state,
        {"target": {"mode": "set_operation", "operator": "sort", "left_handle": older["handle"]}},
    )

    assert proof["complete"] is False
    assert "older_visible_result_requires_explicit_return_binding" in proof["errors"]
    assert proof["discourse_binding"]["latest_visible_result_refs"] == [latest["handle"]]

    forged = _visible_reference_proof(
        state,
        {
            "target": {"mode": "set_operation", "operator": "sort", "left_handle": older["handle"]},
            "context_binding": {"reference_kind": "explicit_return", "source_span": "其中"},
        },
    )

    assert forged["complete"] is False
    assert "explicit_return_binding_not_literal_member_label" in forged["errors"]


def test_older_collection_accepts_literal_return_to_its_verified_filter_condition() -> None:
    from agent_core.context.visible_result_refs import mark_visible_result_refs
    from agent_core.ledger import artifact_entry, result_entry
    from agent_core.runtime.capability_gate import _visible_reference_proof

    scope = {"tenant_id": "default", "user_id": "u001", "thread_id": "thread-condition-return"}
    keyboard = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",
        facts={"order_id": "10002"}, scope=scope, turn=1, source="test", handle="h_order:10002",
    )
    cup = artifact_entry(
        resource_type="order", resource_id="10004", label="定制马克杯（订单 10004）",
        facts={"order_id": "10004"}, scope=scope, turn=1, source="test", handle="h_order:10004",
    )
    signed = result_entry(
        capability="ecommerce.orders.list",
        member_handles=[keyboard["handle"], cup["handle"]],
        labels=[keyboard["label"], cup["label"]],
        scope=scope,
        turn=1,
        source_target={
            "mode": "set_operation",
            "target": {
                "mode": "set_operation",
                "operator": "filter",
                "status": "已签收",
            },
        },
        handle="h_result:signed",
    )
    latest = result_entry(
        capability="ecommerce.invoices.list",
        member_handles=[], labels=[], scope=scope, turn=2,
        source_target={"mode": "entity_match"}, handle="h_result:empty-invoices",
    )
    state = {
        "current_tenant_id": scope["tenant_id"], "current_user_id": scope["user_id"],
        "current_thread_id": scope["thread_id"], "turn_index": 3,
        "current_user_input": "总结签收集合里这两个订单", "artifact_ledger": [keyboard, cup, signed, latest],
    }
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"], state={**state, "turn_index": 1}, evidence_handles=[signed["handle"]],
    )
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"], state={**state, "turn_index": 2}, evidence_handles=[latest["handle"]],
    )

    proof = _visible_reference_proof(
        state,
        {"target": {"mode": "collection", "left_handle": signed["handle"]}},
    )

    assert proof["complete"] is True
    assert proof["discourse_binding"]["selected_structural_return_result_refs"] == [signed["handle"]]
    assert proof["discourse_binding"]["selected_older_visible_result_refs"] == []


def test_older_visible_single_artifact_accepts_its_literal_label_as_explicit_return() -> None:
    from agent_core.context.visible_result_refs import mark_visible_result_refs
    from agent_core.ledger import artifact_entry
    from agent_core.runtime.capability_gate import _visible_reference_proof

    scope = {"tenant_id": "default", "user_id": "u001", "thread_id": "thread-old-artifact"}
    keyboard = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",
        facts={"order_id": "10002"}, scope=scope, turn=1, source="test", handle="h_order:10002",
    )
    mouse = artifact_entry(
        resource_type="order", resource_id="10003", label="无线鼠标（订单 10003）",
        facts={"order_id": "10003"}, scope=scope, turn=2, source="test", handle="h_order:10003",
    )
    state = {
        "current_tenant_id": scope["tenant_id"], "current_user_id": scope["user_id"],
        "current_thread_id": scope["thread_id"], "turn_index": 3,
        "current_user_input": "不是无线鼠标，回到机械键盘那个", "artifact_ledger": [keyboard, mouse],
    }
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"], state={**state, "turn_index": 1}, evidence_handles=[keyboard["handle"]],
    )
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"], state={**state, "turn_index": 2}, evidence_handles=[mouse["handle"]],
    )

    proof = _visible_reference_proof(
        state,
        {
            "target": {"mode": "artifact", "left_handle": keyboard["handle"]},
            "context_binding": {"reference_kind": "explicit_return", "source_span": "机械键盘"},
        },
    )

    assert proof["complete"] is True
    assert proof["discourse_binding"]["selected_older_visible_result_refs"] == [keyboard["handle"]]


def test_latest_derived_set_can_consume_its_visible_source_by_ledger_lineage() -> None:
    from agent_core.context.visible_result_refs import mark_visible_result_refs
    from agent_core.ledger import artifact_entry, result_entry
    from agent_core.runtime.capability_gate import _visible_reference_proof

    scope = {"tenant_id": "default", "user_id": "u001", "thread_id": "thread-lineage"}
    order_a = artifact_entry(
        resource_type="order", resource_id="10001", label="蓝牙耳机（订单 10001）",
        facts={"order_id": "10001"}, scope=scope, turn=1, source="test", handle="h_order:10001",
    )
    order_b = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",
        facts={"order_id": "10002"}, scope=scope, turn=1, source="test", handle="h_order:10002",
    )
    source = result_entry(
        capability="ecommerce.orders.list", member_handles=[order_a["handle"], order_b["handle"]],
        labels=[order_a["label"], order_b["label"]], scope=scope, turn=1,
        source_target={"mode": "all_orders", "target": {"mode": "all_orders"}}, handle="h_result:all",
    )
    latest = result_entry(
        capability="ecommerce.orders.list", member_handles=[order_a["handle"]], labels=[order_a["label"]],
        scope=scope, turn=2,
        source_target={
            "mode": "set_operation",
            "target": {"mode": "set_operation", "operator": "filter", "left_handle": source["handle"]},
        },
        handle="h_result:signed",
    )
    state = {
        "current_tenant_id": scope["tenant_id"], "current_user_id": scope["user_id"],
        "current_thread_id": scope["thread_id"], "turn_index": 3,
        "current_user_input": "其余的呢？", "artifact_ledger": [order_a, order_b, source, latest],
    }
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"], state={**state, "turn_index": 1}, evidence_handles=[source["handle"]],
    )
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"], state={**state, "turn_index": 2}, evidence_handles=[latest["handle"]],
    )

    proof = _visible_reference_proof(
        state,
        {"target": {
            "mode": "set_operation", "operator": "difference",
            "left_handle": source["handle"], "right_handle": latest["handle"],
        }},
    )

    assert proof["complete"] is True
    assert proof["discourse_binding"]["selected_latest_lineage_result_refs"] == [source["handle"]]


def test_model_verifier_classifies_exact_target_grounding_as_action_prerequisite(
    monkeypatch,
) -> None:
    captured: dict = {}

    def fake_invoke_model(*, purpose, model, payload):
        for message in payload:
            captured.update(json.loads(message.content))
        return (
            SimpleNamespace(
                content=json.dumps(
                    {
                        "verdict": "exact",
                        "evidence_span": "订单10003",
                        "reason_code": "exact_target_grounding_prerequisite",
                    },
                    ensure_ascii=False,
                )
            ),
            {"purpose": purpose},
        )

    monkeypatch.setattr(verifier_module, "invoke_model", fake_invoke_model)
    monkeypatch.setattr("agent_core.config.get_model", lambda: object())
    contract = ToolCapabilityContract(
        key="ecommerce.order.details",
        tool_name="get_order_details",
        category="query",
        writes_business_data=False,
        evidence_sources=("business_service",),
        planner_rule="Query one verified order as grounding for a downstream action.",
        unavailable_response="unavailable",
        execution_kind="grounding_read",
    )

    verdict = verifier_module.ModelSemanticCapabilityVerifier().verify(
        user_text="请取消订单10003",
        tool_name="get_order_details",
        args={
            "target": {"mode": "entity_match", "attribute_span": "10003"},
            "expected_shape": "one",
            "reference_span": "订单10003",
        },
        contract=contract,
        verified_context=[],
        step_context={
            "effect_id": "turn-plan:1:effect:1",
            "goal_ids": ["cancel-target"],
            "declared_goals": [{"goal_id": "cancel-target", "description": "定位要取消的订单"}],
        },
    )

    assert verdict.exact is True
    assert captured["CANDIDATE"]["execution_kind"] == "grounding_read"
    assert captured["DECLARED_WORKFLOW_STEP"]["goal_ids"] == ["cancel-target"]
    assert any(
        "exact-target prerequisite read" in rule
        for rule in captured["DECISION_RULES"]
    )
    assert "do not require that read to perform the downstream write" in captured[
        "instruction"
    ]
    assert "target.mode=all_orders is a scope expansion" in captured["instruction"]
    assert any(
        "reject target.mode=all_orders" in rule
        for rule in captured["DECISION_RULES"]
    )
