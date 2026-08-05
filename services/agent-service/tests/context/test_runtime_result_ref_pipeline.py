from __future__ import annotations

from copy import deepcopy

from agent_core.composition import get_runtime_registry
from agent_core.context.visible_result_refs import (
    mark_visible_result_refs,
    validate_runtime_result_ref,
)
from agent_core.ledger import append_entries, artifact_entry, result_entry
from agent_core.runtime.capability_gate import issue_execution_permit


SCOPE = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-chain"}


def _state(*, turn: int, ledger: list[dict], trace: list[dict] | None = None) -> dict:
    class ExactVerifier:
        def verify(self, **_kwargs):
            return {
                "verdict": "exact",
                "evidence_span": "其中最贵的是哪个",
                "reason_code": "reference_pipeline_test",
            }

    return {
        "current_tenant_id": SCOPE["tenant_id"],
        "current_user_id": SCOPE["user_id"],
        "current_thread_id": SCOPE["thread_id"],
        "current_role": "customer",
        "current_user_input": "其中最贵的是哪个？再查一下它的物流",
        "turn_index": turn,
        "artifact_ledger": ledger,
        "tool_trace": list(trace or []),
        # Keep this protocol test deterministic even when a developer .env
        # enables the independent real-model semantic verifier.
        "semantic_capability_verifier": ExactVerifier(),
    }


def _result(*, turn: int, handle: str = "result:sorted") -> tuple[list[dict], dict]:
    order = artifact_entry(
        resource_type="order",
        resource_id="10002",
        label="机械键盘（订单 10002）",
        facts={"amount": 499.0},
        scope=SCOPE,
        turn=turn,
        source="test",
        handle="artifact:order:10002",
    )
    result = result_entry(
        capability="ecommerce.orders.list",
        member_handles=[order["handle"]],
        labels=[order["label"]],
        scope=SCOPE,
        turn=turn,
        source_target={"mode": "set_operation", "operator": "sort"},
        handle=handle,
    )
    return [order, result], result


def _verified_trace(result_handle: str, *, turn: int = 9, ok: bool = True) -> list[dict]:
    effect_id = f"turn-plan:{turn}:effect:1"
    permit = {
        "permit_id": "permit:verified",
        "capability_id": "ecommerce.orders.list",
        "tool_name": "list_orders",
        "effect_id": effect_id,
        "turn": turn,
        "scope": deepcopy(SCOPE),
    }
    proof = {"exact_match": True}
    return [{
        "name": "list_orders",
        "classification": "observation",
        "effect_id": effect_id,
        "execution_permit": permit,
        "match_proof": proof,
        "result": {
            "ok": ok,
            "data": {"result_handle": result_handle},
            "execution_permit": permit,
            "match_proof": proof,
        },
    }]


def test_current_turn_permit_backed_observation_can_feed_next_set_operation() -> None:
    ledger, result = _result(turn=9)
    state = _state(turn=9, ledger=ledger, trace=_verified_trace(result["handle"]))

    checked, error = validate_runtime_result_ref(
        state=state,
        result_ref=result["handle"],
        expected_shape="collection",
    )

    assert error is None
    assert checked and checked["reference_kind"] == "current_turn_verified_observation"
    decision = issue_execution_permit(
        state=state,
        tool_name="list_orders",
        args={
            "target": {
                "mode": "set_operation",
                "operator": "take",
                "left_handle": result["handle"],
                "limit": 1,
            },
            "expected_shape": "collection",
            "reference_span": "其中最贵的是哪个",
        },
        effect_id="turn-plan:9:effect:2",
        capability_registry=get_runtime_registry().capabilities,
    )
    assert decision.permitted is True, decision.match_proof
    check = decision.match_proof["visible_result_reference"]["checks"][0]
    assert check["validated_ref"]["reference_kind"] == "current_turn_verified_observation"


def test_execution_permit_scope_remains_actor_thread_bound_when_subject_differs() -> None:
    ledger, result = _result(turn=9)
    state = _state(turn=9, ledger=ledger, trace=_verified_trace(result["handle"]))
    state["current_subject"] = "u002"

    checked, error = validate_runtime_result_ref(
        state=state,
        result_ref=result["handle"],
        expected_shape="collection",
    )

    assert error is None
    assert checked and checked["reference_kind"] == "current_turn_verified_observation"


def test_execution_permit_scope_mismatch_is_still_rejected() -> None:
    ledger, result = _result(turn=9)
    trace = _verified_trace(result["handle"])
    trace[0]["execution_permit"]["scope"]["tenant_id"] = "tenant-b"
    trace[0]["result"]["execution_permit"]["scope"]["tenant_id"] = "tenant-b"

    checked, error = validate_runtime_result_ref(
        state=_state(turn=9, ledger=ledger, trace=trace),
        result_ref=result["handle"],
        expected_shape="collection",
    )

    assert checked is None
    assert error == "runtime_result_ref_not_verified_current_turn_observation"


def test_current_turn_result_member_can_feed_an_exact_artifact_target() -> None:
    ledger, result = _result(turn=9)
    state = _state(turn=9, ledger=ledger, trace=_verified_trace(result["handle"]))

    checked, error = validate_runtime_result_ref(
        state=state,
        result_ref="artifact:order:10002",
        expected_shape="one",
    )

    assert error is None
    assert checked and checked["reference_kind"] == "current_turn_verified_observation"
    assert checked["source_output_handle"] == result["handle"]


def test_current_turn_artifact_outside_verified_result_members_is_rejected() -> None:
    ledger, result = _result(turn=9)
    other = artifact_entry(
        resource_type="order",
        resource_id="10003",
        label="无线鼠标（订单 10003）",
        facts={"amount": 99.0},
        scope=SCOPE,
        turn=9,
        source="test",
        handle="artifact:order:10003",
    )

    checked, error = validate_runtime_result_ref(
        state=_state(turn=9, ledger=[*ledger, other], trace=_verified_trace(result["handle"])),
        result_ref=other["handle"],
        expected_shape="one",
    )

    assert checked is None
    assert error == "runtime_result_ref_not_verified_current_turn_observation"


def test_same_turn_ledger_entry_without_verified_trace_is_rejected() -> None:
    ledger, result = _result(turn=9)

    checked, error = validate_runtime_result_ref(
        state=_state(turn=9, ledger=ledger),
        result_ref=result["handle"],
        expected_shape="collection",
    )

    assert checked is None
    assert error == "runtime_result_ref_not_verified_current_turn_observation"


def test_failed_or_unpermitted_observation_does_not_authorize_reference() -> None:
    ledger, result = _result(turn=9)
    failed = _verified_trace(result["handle"], ok=False)
    unpermitted = _verified_trace(result["handle"])
    unpermitted[0]["match_proof"] = {"exact_match": False}
    unpermitted[0]["result"]["match_proof"] = {"exact_match": False}

    assert validate_runtime_result_ref(
        state=_state(turn=9, ledger=ledger, trace=failed),
        result_ref=result["handle"],
        expected_shape="collection",
    )[0] is None
    assert validate_runtime_result_ref(
        state=_state(turn=9, ledger=ledger, trace=unpermitted),
        result_ref=result["handle"],
        expected_shape="collection",
    )[0] is None


def test_invisible_prior_turn_entry_remains_rejected_even_with_old_trace() -> None:
    ledger, result = _result(turn=8)

    checked, error = validate_runtime_result_ref(
        state=_state(turn=9, ledger=ledger, trace=_verified_trace(result["handle"], turn=8)),
        result_ref=result["handle"],
        expected_shape="collection",
    )

    assert checked is None
    assert error == "visible_result_ref_not_customer_visible"


def test_customer_visible_prior_turn_reference_remains_valid_without_live_trace() -> None:
    ledger, result = _result(turn=8, handle="result:released")
    released = mark_visible_result_refs(
        ledger,
        state=_state(turn=8, ledger=ledger),
        evidence_handles=[result["handle"]],
    )

    checked, error = validate_runtime_result_ref(
        state=_state(turn=9, ledger=released),
        result_ref=result["handle"],
        expected_shape="collection",
    )

    assert error is None
    assert checked and checked["reference_kind"] == "customer_visible"


def test_customer_visible_prior_turn_collection_member_is_an_exact_valid_target() -> None:
    ledger, result = _result(turn=8, handle="result:released-members")
    released = mark_visible_result_refs(
        ledger,
        state=_state(turn=8, ledger=ledger),
        evidence_handles=[result["handle"]],
    )

    checked, error = validate_runtime_result_ref(
        state=_state(turn=9, ledger=released),
        result_ref="artifact:order:10002",
        expected_shape="one",
    )

    assert error is None
    assert checked and checked["reference_kind"] == "customer_visible"
    assert checked["presentation_origin"] == "customer_visible_result_member"
    assert checked["source_collection_ref"] == result["handle"]
    assert checked["member_handles"] == ["artifact:order:10002"]
    assert checked["member_labels"] == ["机械键盘（订单 10002）"]


def _two_recent_released_results() -> tuple[list[dict], dict, dict]:
    earphone = artifact_entry(
        resource_type="order",
        resource_id="10001",
        label="蓝牙耳机（订单 10001）",
        facts={"status": "运输中"},
        scope=SCOPE,
        turn=1,
        source="test",
        handle="artifact:order:10001",
    )
    first = result_entry(
        capability="ecommerce.order.logistics",
        member_handles=[earphone["handle"]],
        labels=[earphone["label"]],
        scope=SCOPE,
        turn=1,
        source_target={"mode": "entity_match", "attribute_span": "蓝牙耳机"},
        handle="result:earphone-logistics",
    )
    ledger = mark_visible_result_refs(
        [earphone, first],
        state=_state(turn=1, ledger=[earphone, first]),
        evidence_handles=[first["handle"]],
    )
    mouse = artifact_entry(
        resource_type="order",
        resource_id="10003",
        label="无线鼠标（订单 10003）",
        facts={"status": "待发货"},
        scope=SCOPE,
        turn=2,
        source="test",
        handle="artifact:order:10003",
    )
    second = result_entry(
        capability="ecommerce.order.logistics",
        member_handles=[mouse["handle"]],
        labels=[mouse["label"]],
        scope=SCOPE,
        turn=2,
        source_target={"mode": "entity_match", "attribute_span": "无线鼠标"},
        handle="result:mouse-logistics",
    )
    ledger = mark_visible_result_refs(
        [*ledger, mouse, second],
        state=_state(turn=2, ledger=[*ledger, mouse, second]),
        evidence_handles=[second["handle"]],
    )
    return ledger, first, second


def test_explicit_group_reference_can_union_the_two_most_recent_visible_results() -> None:
    class GroupExactVerifier:
        def verify(self, **_kwargs):
            return {
                "verdict": "exact",
                "evidence_span": "刚才两个哪个已经发出",
                "reason_code": "explicit_recent_group_test",
            }

    ledger, first, second = _two_recent_released_results()
    state = {
        **_state(turn=3, ledger=ledger),
        "current_user_input": "刚才两个哪个已经发出？",
        "semantic_capability_verifier": GroupExactVerifier(),
    }
    decision = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args={
            "target": {
                "mode": "set_operation",
                "operator": "union",
                "left_handle": first["handle"],
                "right_handle": second["handle"],
            },
            "expected_shape": "collection",
            "reference_span": "刚才两个",
            "context_binding": {
                "reference_kind": "explicit_group_reference",
                "source_span": "刚才两个",
                "group_size": 2,
            },
            "query": {"delivery_status": "运输中"},
            "constraint_bindings": [{
                "source_span": "已经发出",
                "kind": "condition",
                "parameter_path": "query.delivery_status",
                "normalized_value": "运输中",
            }],
        },
        effect_id="turn-plan:3:effect:recent-group",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is True, decision.match_proof
    discourse = decision.match_proof["visible_result_reference"]["discourse_binding"]
    assert discourse["explicit_group_binding_complete"] is True
    assert discourse["selected_older_visible_result_refs"] == [first["handle"]]


def test_literal_recent_group_reference_does_not_require_duplicate_context_binding() -> None:
    class GroupExactVerifier:
        def verify(self, **_kwargs):
            return {
                "verdict": "exact",
                "evidence_span": "刚才两个哪个已经发出",
                "reason_code": "literal_recent_group_test",
            }

    ledger, first, second = _two_recent_released_results()
    state = {
        **_state(turn=3, ledger=ledger),
        "current_user_input": "刚才两个哪个已经发出？",
        "semantic_capability_verifier": GroupExactVerifier(),
    }
    decision = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args={
            "target": {
                "mode": "set_operation",
                "operator": "union",
                "left_handle": first["handle"],
                "right_handle": second["handle"],
            },
            "expected_shape": "collection",
            "reference_span": "刚才两个",
            "query": {"delivery_status": "运输中"},
            "constraint_bindings": [{
                "source_span": "已经发出",
                "kind": "condition",
                "parameter_path": "query.delivery_status",
                "normalized_value": "运输中",
            }],
        },
        effect_id="turn-plan:3:effect:recent-group-no-duplicate-binding",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is True, decision.match_proof
    discourse = decision.match_proof["visible_result_reference"]["discourse_binding"]
    assert discourse["explicit_group_binding_complete"] is True
    assert discourse["reference_kind"] is None
    assert discourse["group_source_span"] == "刚才两个"


def test_group_reference_cannot_skip_a_more_recent_visible_result() -> None:
    ledger, first, second = _two_recent_released_results()
    older_artifact = artifact_entry(
        resource_type="order",
        resource_id="10004",
        label="定制马克杯（订单 10004）",
        facts={"status": "已签收"},
        scope=SCOPE,
        turn=0,
        source="test",
        handle="artifact:order:10004",
    )
    older_result = result_entry(
        capability="ecommerce.order.logistics",
        member_handles=[older_artifact["handle"]],
        labels=[older_artifact["label"]],
        scope=SCOPE,
        turn=0,
        source_target={"mode": "entity_match", "attribute_span": "定制马克杯"},
        handle="result:cup-logistics",
    )
    # Release the old result first, then replay the two newer releases so the
    # ledger contains recency ranks 1, 2 and 3.
    old_ledger = mark_visible_result_refs(
        [older_artifact, older_result],
        state=_state(turn=0, ledger=[older_artifact, older_result]),
        evidence_handles=[older_result["handle"]],
    )
    combined = [*old_ledger, *ledger]
    state = {
        **_state(turn=3, ledger=combined),
        "current_user_input": "刚才两个哪个已经发出？",
    }
    decision = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args={
            "target": {
                "mode": "set_operation",
                "operator": "union",
                "left_handle": older_result["handle"],
                "right_handle": second["handle"],
            },
            "expected_shape": "collection",
            "reference_span": "刚才两个",
            "context_binding": {
                "reference_kind": "explicit_group_reference",
                "source_span": "刚才两个",
                "group_size": 2,
            },
            "query": {},
        },
        effect_id="turn-plan:3:effect:skipped-group",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is False
    assert "explicit_group_reference_must_select_recent_contiguous_visible_group" in decision.match_proof["visible_result_reference"]["errors"]


def _released_multi_member_result() -> tuple[list[dict], dict]:
    products = [
        ("10001", "蓝牙耳机"),
        ("10002", "机械键盘"),
        ("10003", "无线鼠标"),
        ("10004", "定制马克杯"),
    ]
    artifacts = [
        artifact_entry(
            resource_type="order",
            resource_id=order_id,
            label=f"{product_name}（订单 {order_id}）",
            facts={"amount": float(index + 1)},
            scope=SCOPE,
            turn=8,
            source="test",
            handle=f"artifact:order:{order_id}",
        )
        for index, (order_id, product_name) in enumerate(products)
    ]
    result = result_entry(
        capability="ecommerce.orders.list",
        member_handles=[row["handle"] for row in artifacts],
        labels=[row["label"] for row in artifacts],
        scope=SCOPE,
        turn=8,
        source_target={"mode": "all_orders"},
        handle="result:released-all-orders",
    )
    ledger = [*artifacts, result]
    return mark_visible_result_refs(
        ledger,
        state=_state(turn=8, ledger=ledger),
        evidence_handles=[result["handle"]],
    ), result


def test_named_unique_member_cannot_be_replaced_by_its_multi_member_collection() -> None:
    ledger, result = _released_multi_member_result()
    state = {
        **_state(turn=9, ledger=ledger),
        "current_user_input": "那无线鼠标什么时候发货？",
    }

    decision = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args={
            "target": {"mode": "collection", "left_handle": result["handle"]},
            "expected_shape": "collection",
            "reference_span": "无线鼠标",
            "context_binding": {"reference_kind": "explicit_return", "source_span": "无线鼠标"},
            "query": {},
        },
        effect_id="turn-plan:9:effect:scope-broad",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is False
    assert decision.rejection and decision.rejection["code"] == "EXPLICIT_MEMBER_REQUIRES_SINGLE_MEMBER_TARGET"
    assert decision.match_proof["explicit_member_scope"]["matched_member_indexes"] == [2]
    assert decision.match_proof["semantic_verdict"]["verdict"] == "not_required"

    identity_bypass = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args={
            "target": {
                "mode": "set_operation",
                "operator": "identity",
                "left_handle": result["handle"],
            },
            "expected_shape": "collection",
            "reference_span": "无线鼠标",
            "context_binding": {"reference_kind": "explicit_return", "source_span": "无线鼠标"},
            "query": {},
        },
        effect_id="turn-plan:9:effect:scope-identity-bypass",
        capability_registry=get_runtime_registry().capabilities,
    )
    assert identity_bypass.permitted is False
    assert identity_bypass.rejection and identity_bypass.rejection["code"] == "EXPLICIT_MEMBER_REQUIRES_SINGLE_MEMBER_TARGET"


def test_named_unique_member_exact_visible_artifact_remains_permitted() -> None:
    class MouseExactVerifier:
        def verify(self, **_kwargs):
            return {
                "verdict": "exact",
                "evidence_span": "无线鼠标",
                "reason_code": "exact_visible_member_test",
            }

    ledger, _result = _released_multi_member_result()
    state = {
        **_state(turn=9, ledger=ledger),
        "current_user_input": "那无线鼠标什么时候发货？",
        "semantic_capability_verifier": MouseExactVerifier(),
    }

    decision = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args={
            "target": {"mode": "artifact", "left_handle": "artifact:order:10003"},
            "expected_shape": "one",
            "reference_span": "无线鼠标",
            "context_binding": {"reference_kind": "explicit_return", "source_span": "无线鼠标"},
            "query": {},
        },
        effect_id="turn-plan:9:effect:scope-exact",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is True, decision.match_proof
    assert decision.match_proof["explicit_member_scope"]["applies"] is False


def test_named_unique_member_rejects_a_different_visible_artifact() -> None:
    ledger, _result = _released_multi_member_result()
    state = {
        **_state(turn=9, ledger=ledger),
        "current_user_input": "杯子只查退款资格。",
    }

    decision = issue_execution_permit(
        state=state,
        tool_name="evaluate_refund_eligibility",
        args={
            "target": {"mode": "artifact", "left_handle": "artifact:order:10002"},
            "reference_span": "杯子",
            "reason_span": "",
            "question_span": "杯子只查退款资格",
        },
        effect_id="turn-plan:9:effect:wrong-visible-member",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is False
    assert decision.rejection and decision.rejection["code"] == "EXPLICIT_MEMBER_REQUIRES_SINGLE_MEMBER_TARGET"
    proof = decision.match_proof["explicit_member_scope"]
    assert proof["named_member_handle"] == "artifact:order:10004"
    assert proof["selected_member_handles"] == ["artifact:order:10002"]
    assert proof["errors"] == ["explicit_unique_member_target_mismatch"]


def test_named_unique_member_rejects_fresh_all_orders_scope_expansion() -> None:
    ledger, _result = _released_multi_member_result()
    state = {
        **_state(turn=9, ledger=ledger),
        "current_user_input": "现在改回10003查物流。",
    }

    decision = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args={
            "target": {"mode": "all_orders"},
            "expected_shape": "collection",
            "reference_span": "10003",
            "query": {},
        },
        effect_id="turn-plan:9:effect:broad-explicit-id",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is False
    assert decision.rejection and decision.rejection["code"] == "EXPLICIT_MEMBER_REQUIRES_SINGLE_MEMBER_TARGET"
    assert decision.match_proof["explicit_member_scope"]["named_member_handle"] == "artifact:order:10003"


def test_comparison_reversal_requires_recorded_parent_collection() -> None:
    ledger, parent = _released_multi_member_result()
    singleton = result_entry(
        capability="ecommerce.orders.list",
        member_handles=["artifact:order:10002"],
        labels=["机械键盘（订单 10002）"],
        scope=SCOPE,
        turn=9,
        source_target={
            "mode": "set_operation",
            "target": {
                "mode": "set_operation",
                "operator": "take",
                "left_handle": parent["handle"],
                "limit": 1,
            },
        },
        handle="result:visible-most-expensive",
    )
    ledger = append_entries(ledger, [singleton])
    ledger = mark_visible_result_refs(
        ledger,
        state=_state(turn=9, ledger=ledger),
        evidence_handles=[singleton["handle"]],
    )
    state = {
        **_state(turn=10, ledger=ledger),
        "current_user_input": "便宜的那个呢？",
    }

    decision = issue_execution_permit(
        state=state,
        tool_name="list_orders",
        args={
            "target": {
                "mode": "set_operation",
                "operator": "sort",
                "left_handle": singleton["handle"],
                "sort_field": "amount",
                "sort_direction": "asc",
                "sort_span": "便宜的那个",
            },
            "expected_shape": "collection",
            "reference_span": "便宜的那个",
        },
        effect_id="turn-plan:10:effect:rerank-singleton",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is False
    assert decision.rejection and decision.rejection["code"] == "DERIVED_SINGLETON_REQUIRES_PARENT_SCOPE"
    proof = decision.match_proof["derived_collection_scope"]
    assert proof["source_operation"]["operator"] == "take"
    assert proof["lineage_result_refs"] == [parent["handle"]]


def test_visible_result_preserves_literal_operation_span_for_structural_return() -> None:
    from agent_core.context.visible_result_refs import visible_result_refs_from_ledger

    ledger, parent = _released_multi_member_result()
    sorted_result = result_entry(
        capability="ecommerce.orders.list",
        member_handles=list(parent["member_handles"]),
        labels=list(parent["labels"]),
        scope=SCOPE,
        turn=9,
        source_target={
            "mode": "set_operation",
            "target": {
                "mode": "set_operation",
                "operator": "sort",
                "left_handle": parent["handle"],
                "sort_field": "amount",
                "sort_direction": "asc",
                "sort_span": "便宜的那个",
            },
        },
        handle="result:visible-cheapest-ordering",
    )
    ledger = append_entries(ledger, [sorted_result])
    ledger = mark_visible_result_refs(
        ledger,
        state=_state(turn=9, ledger=ledger),
        evidence_handles=[sorted_result["handle"]],
    )

    refs = visible_result_refs_from_ledger(
        ledger,
        state=_state(turn=10, ledger=ledger),
    )
    visible = next(row for row in refs if row["result_ref"] == sorted_result["handle"])
    assert visible["source_operation"]["sort_span"] == "便宜的那个"


def test_comparison_reversal_can_use_transitive_parent_of_latest_singleton() -> None:
    class CheapExactVerifier:
        def verify(self, **_kwargs):
            return {"verdict": "exact", "evidence_span": "便宜的那个", "reason_code": "transitive_parent"}

    ledger, parent = _released_multi_member_result()
    sorted_result = result_entry(
        capability="ecommerce.orders.list",
        member_handles=list(parent["member_handles"]),
        labels=list(parent["labels"]),
        scope=SCOPE,
        turn=9,
        source_target={
            "mode": "set_operation",
            "target": {
                "mode": "set_operation",
                "operator": "sort",
                "left_handle": parent["handle"],
                "sort_field": "amount",
                "sort_direction": "desc",
            },
        },
        handle="result:invisible-sorted-parent",
    )
    singleton = result_entry(
        capability="ecommerce.orders.list",
        member_handles=["artifact:order:10004"],
        labels=["定制马克杯（订单 10004）"],
        scope=SCOPE,
        turn=9,
        source_target={
            "mode": "set_operation",
            "target": {
                "mode": "set_operation",
                "operator": "take",
                "left_handle": sorted_result["handle"],
                "limit": 1,
            },
        },
        handle="result:visible-derived-singleton",
    )
    ledger = append_entries(ledger, [sorted_result, singleton])
    ledger = mark_visible_result_refs(
        ledger,
        state=_state(turn=9, ledger=ledger),
        evidence_handles=[singleton["handle"]],
    )
    state = {
        **_state(turn=10, ledger=ledger),
        "current_user_input": "便宜的那个呢？",
        "semantic_capability_verifier": CheapExactVerifier(),
    }

    decision = issue_execution_permit(
        state=state,
        tool_name="list_orders",
        args={
            "target": {
                "mode": "set_operation",
                "operator": "sort",
                "left_handle": parent["handle"],
                "sort_field": "amount",
                "sort_direction": "asc",
                "sort_span": "便宜的那个",
            },
            "expected_shape": "collection",
            "reference_span": "便宜的那个",
        },
        effect_id="turn-plan:10:effect:transitive-parent",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is True, decision.match_proof
    lineage = decision.match_proof["visible_result_reference"]["discourse_binding"]
    assert parent["handle"] in lineage["selected_latest_lineage_result_refs"]


def test_set_difference_accepts_one_verified_visible_member_as_singleton_set() -> None:
    from agent_modules.ecommerce.shared.context import _target_members

    class DifferenceExactVerifier:
        def verify(self, **_kwargs):
            return {"verdict": "exact", "evidence_span": "定制马克杯", "reason_code": "singleton_difference"}

    ledger, parent = _released_multi_member_result()
    state = {
        **_state(turn=9, ledger=ledger),
        "current_user_input": "除了定制马克杯，签收集合还剩谁？",
        "semantic_capability_verifier": DifferenceExactVerifier(),
    }
    target = {
        "mode": "set_operation",
        "operator": "difference",
        "left_handle": parent["handle"],
        "right_handle": "artifact:order:10004",
    }

    decision = issue_execution_permit(
        state=state,
        tool_name="list_orders",
        args={
            "target": target,
            "expected_shape": "collection",
            "reference_span": "定制马克杯",
        },
        effect_id="turn-plan:9:effect:difference-singleton",
        capability_registry=get_runtime_registry().capabilities,
    )
    resolved, error = _target_members(
        state,
        target,
        expected_shape="collection",
        allowed_resource_types={"order"},
    )

    assert decision.permitted is True, decision.match_proof
    assert decision.match_proof["explicit_member_scope"]["typed_exclusion"] is True
    assert error is None
    assert resolved is not None
    assert {row["resource_id"] for row in resolved["members"]} == {"10001", "10002", "10003"}


def test_explicit_visible_member_binding_normalizes_only_label_typography() -> None:
    class InvoiceExactVerifier:
        def verify(self, **_kwargs):
            return {
                "verdict": "exact",
                "evidence_span": "订单10004",
                "reason_code": "normalized_literal_member_label_test",
            }

    ledger, _result = _released_multi_member_result()
    state = {
        **_state(turn=9, ledger=ledger),
        "current_user_input": "订单10004能开发票吗？",
        "semantic_capability_verifier": InvoiceExactVerifier(),
    }

    decision = issue_execution_permit(
        state=state,
        tool_name="consult_invoice_policy",
        args={
            "target": {"mode": "artifact", "left_handle": "artifact:order:10004"},
            "reference_span": "订单10004",
            "context_binding": {"reference_kind": "explicit_return", "source_span": "订单10004"},
            "issue_span": "发票",
            "question_span": "能开发票吗",
        },
        effect_id="turn-plan:9:effect:invoice-typography",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is True
    discourse = decision.match_proof["visible_result_reference"]["discourse_binding"]
    assert discourse["source_span"] == "订单10004"


def test_prior_turn_artifact_outside_released_collection_remains_rejected() -> None:
    ledger, result = _result(turn=8, handle="result:released-only")
    other = artifact_entry(
        resource_type="order",
        resource_id="10003",
        label="无线鼠标（订单 10003）",
        facts={"amount": 99.0},
        scope=SCOPE,
        turn=8,
        source="test",
        handle="artifact:order:10003",
    )
    released = mark_visible_result_refs(
        [*ledger, other],
        state=_state(turn=8, ledger=[*ledger, other]),
        evidence_handles=[result["handle"]],
    )

    checked, error = validate_runtime_result_ref(
        state=_state(turn=9, ledger=released),
        result_ref=other["handle"],
        expected_shape="one",
    )

    assert checked is None
    assert error == "visible_result_ref_not_customer_visible"


def test_refreshing_same_visible_artifact_preserves_identity_visibility() -> None:
    ledger, result = _result(turn=8, handle="result:released-refresh")
    released = mark_visible_result_refs(
        ledger,
        state=_state(turn=8, ledger=ledger),
        evidence_handles=[result["handle"], "artifact:order:10002"],
    )
    original = next(row for row in released if row.get("handle") == "artifact:order:10002")
    refreshed = artifact_entry(
        resource_type="order",
        resource_id="10002",
        label="机械键盘（订单 10002）",
        facts={"amount": 499.0, "status": "已签收"},
        scope=SCOPE,
        turn=9,
        source="business-refresh",
        handle=original["handle"],
    )

    merged = append_entries(released, [refreshed])
    checked, error = validate_runtime_result_ref(
        state=_state(turn=9, ledger=merged),
        result_ref=original["handle"],
        expected_shape="one",
    )

    assert error is None
    assert checked and checked["reference_kind"] == "customer_visible"
    saved = next(row for row in merged if row.get("handle") == original["handle"])
    assert saved["presentation_origin"] == original["presentation_origin"]


def test_current_turn_refresh_overrides_stale_visible_interpretation() -> None:
    ledger, result = _result(turn=8, handle="result:released-refresh-current")
    released = mark_visible_result_refs(
        ledger,
        state=_state(turn=8, ledger=ledger),
        evidence_handles=[result["handle"], "artifact:order:10002"],
    )
    original = next(row for row in released if row.get("handle") == "artifact:order:10002")
    refreshed = artifact_entry(
        resource_type="order",
        resource_id="10002",
        label="机械键盘（订单 10002）",
        facts={"amount": 499.0, "status": "已签收"},
        scope=SCOPE,
        turn=9,
        source="business-refresh",
        handle=original["handle"],
    )
    merged = append_entries(released, [refreshed])
    trace = _verified_trace(result["handle"], turn=9)
    trace[0]["result"]["data"] = {"result_handle": result["handle"]}
    # The prior read returned a collection whose refreshed stable member is the
    # exact artifact consumed by the next step.
    current_result = result_entry(
        capability="ecommerce.orders.list",
        member_handles=[refreshed["handle"]],
        labels=[refreshed["label"]],
        scope=SCOPE,
        turn=9,
        source_target={"mode": "all_orders"},
        handle=result["handle"],
    )
    merged = append_entries(merged, [current_result])

    checked, error = validate_runtime_result_ref(
        state=_state(turn=9, ledger=merged, trace=trace),
        result_ref=refreshed["handle"],
        expected_shape="one",
    )

    assert error is None
    assert checked and checked["reference_kind"] == "current_turn_verified_observation"
    assert checked["source_output_handle"] == result["handle"]
