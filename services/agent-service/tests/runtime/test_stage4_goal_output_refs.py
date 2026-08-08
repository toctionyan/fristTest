from __future__ import annotations

from copy import deepcopy


def _state_and_output():
    from tests.runtime.test_pretool_execution_policy import _contract, _goal, _registry
    from agent_core.ledger import append_entries, artifact_entry, eligibility_entry, scope_for_state
    from agent_core.lifecycle.goal_outputs import record_goal_outputs_from_tool_result

    registry = _registry()
    eligibility_goal = _goal("eligibility", domain="refund", operation="evaluate_eligibility")
    eligibility_goal["target_candidate"] = {"order_id": "10002"}
    refund_goal = _goal("refund", domain="refund", operation="create", depends_on=("eligibility",))
    refund_goal["target_candidate"] = {"order_id": "10002"}
    contract = _contract([eligibility_goal, refund_goal])
    state = {
        "current_tenant_id": "tenant-a",
        "current_user_id": "u001",
        "current_thread_id": "thread-1",
        "turn_index": 1,
        "frozen_semantic_contract": contract,
        "goal_records": [
            {"goal_id": "eligibility", "lifecycle": "COMPLETED"},
            {"goal_id": "refund", "lifecycle": "ACTIVE"},
        ],
    }
    scope = scope_for_state(state)
    order = artifact_entry(
        resource_type="order",
        resource_id="10002",
        facts={"order_id": "10002", "product_name": "机械键盘"},
        scope=scope,
        turn=1,
        source="test",
        label="机械键盘",
    )
    eligibility = eligibility_entry(
        action_id="create_refund",
        operation="APPLY_REFUND",
        target_handle=order["handle"],
        input_values={},
        preview={"decision": "ALLOWED"},
        scope=scope,
        turn=1,
        label="退款资格核验",
    )
    ledger = append_entries([], [order, eligibility])
    state["artifact_ledger"] = ledger
    refs = record_goal_outputs_from_tool_result(
        [],
        state=state,
        capability_registry=registry,
        tool_name="evaluate_refund_eligibility",
        goal_ids=["eligibility"],
        effect_id="effect:eligibility",
        result={"ok": True, "data": {"eligibility_handle": eligibility["handle"]}},
        ledger_additions=[order, eligibility],
        merged_ledger=ledger,
    )
    state["goal_output_refs"] = refs
    return state, registry, order, eligibility


def test_completed_dependency_reuses_verified_typed_goal_output() -> None:
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    state, registry, _order, _eligibility = _state_and_output()
    policy = build_pretool_execution_policy(state=state, capability_registry=registry)
    by_goal = {row["goal_id"]: row for row in policy["goal_policies"]}

    assert policy["allowed_capability_tools"] == ["prepare_refund_from_eligibility"]
    assert by_goal["refund"]["completed_tools"] == ["evaluate_refund_eligibility"]
    assert len(by_goal["refund"]["reused_goal_output_ref_ids"]) == 1
    assert by_goal["refund"]["goal_output_evidence_errors"] == []


def test_goal_output_ref_is_bound_to_active_ledger_artifact_and_target() -> None:
    state, registry, order, eligibility = _state_and_output()
    from agent_core.lifecycle.goal_outputs import validate_goal_output_ref

    ref = state["goal_output_refs"][0]
    check = validate_goal_output_ref(ref, state=state, capability_registry=registry)

    assert check["ok"] is True
    assert ref["artifact_ref"] == eligibility["handle"]
    assert ref["target_binding"] == {
        "target_handle": order["handle"],
        "resource_type": "order",
        "resource_id": "10002",
    }


def test_wrong_scope_goal_output_is_not_reused() -> None:
    from agent_core.lifecycle.goal_outputs import _with_digest
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    state, registry, _order, _eligibility = _state_and_output()
    bad = deepcopy(state["goal_output_refs"][0])
    bad["scope"]["thread_id"] = "other-thread"
    state["goal_output_refs"] = [_with_digest(bad)]

    policy = build_pretool_execution_policy(state=state, capability_registry=registry)
    refund = {row["goal_id"]: row for row in policy["goal_policies"]}["refund"]

    assert "prepare_refund_from_eligibility" not in refund["allowed_tools"]
    assert "GOAL_OUTPUT_REF_SCOPE_MISMATCH" in refund["goal_output_evidence_errors"]


def test_expired_goal_output_is_not_reused() -> None:
    from agent_core.lifecycle.goal_outputs import _with_digest
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    state, registry, _order, _eligibility = _state_and_output()
    bad = deepcopy(state["goal_output_refs"][0])
    bad["expires_at"] = 1
    state["goal_output_refs"] = [_with_digest(bad)]

    policy = build_pretool_execution_policy(state=state, capability_registry=registry)
    refund = {row["goal_id"]: row for row in policy["goal_policies"]}["refund"]

    assert "prepare_refund_from_eligibility" not in refund["allowed_tools"]
    assert "GOAL_OUTPUT_REF_EXPIRED" in refund["goal_output_evidence_errors"]


def test_missing_ledger_artifact_invalidates_goal_output() -> None:
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    state, registry, order, _eligibility = _state_and_output()
    state["artifact_ledger"] = [order]

    policy = build_pretool_execution_policy(state=state, capability_registry=registry)
    refund = {row["goal_id"]: row for row in policy["goal_policies"]}["refund"]

    assert "prepare_refund_from_eligibility" not in refund["allowed_tools"]
    assert "GOAL_OUTPUT_REF_ARTIFACT_INVALID" in refund["goal_output_evidence_errors"]


def test_output_from_non_dependency_goal_is_not_reused() -> None:
    from agent_core.lifecycle.goal_outputs import _with_digest
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    state, registry, _order, _eligibility = _state_and_output()
    ref = deepcopy(state["goal_output_refs"][0])
    ref["producer_goal_id"] = "unrelated"
    state["goal_records"].append({"goal_id": "unrelated", "lifecycle": "COMPLETED"})
    state["goal_output_refs"] = [_with_digest(ref)]

    policy = build_pretool_execution_policy(state=state, capability_registry=registry)
    refund = {row["goal_id"]: row for row in policy["goal_policies"]}["refund"]

    assert "prepare_refund_from_eligibility" not in refund["allowed_tools"]
    assert refund["reused_goal_output_ref_ids"] == []


def test_new_turn_clears_prior_semantic_goal_outputs() -> None:
    from agent_core.lifecycle.context_runtime import prepare_agent_loop_turn_node

    state, _registry, _order, _eligibility = _state_and_output()
    update = prepare_agent_loop_turn_node(state)

    assert update["goal_output_refs"] == []


def test_multi_goal_tool_records_primary_completion_output_for_each_bound_goal() -> None:
    from tests.runtime.test_global_goal_capability_coverage import _registry as overview_registry
    from tests.runtime.test_pretool_execution_policy import _contract, _goal
    from agent_core.ledger import append_entries, artifact_entry, scope_for_state
    from agent_core.lifecycle.goal_outputs import record_goal_outputs_from_tool_result

    registry = overview_registry()
    g1 = _goal("g1", domain="order", operation="query_details")
    g1["target_candidate"] = {"order_id": "10002"}
    g2 = _goal("g2", domain="order", operation="query_logistics")
    g2["target_candidate"] = {"order_id": "10002"}
    contract = _contract([g1, g2])
    state = {
        "current_tenant_id": "tenant-a",
        "current_user_id": "u001",
        "current_thread_id": "thread-1",
        "turn_index": 1,
        "frozen_semantic_contract": contract,
        "goal_records": [
            {"goal_id": "g1", "lifecycle": "COMPLETED"},
            {"goal_id": "g2", "lifecycle": "COMPLETED"},
        ],
    }
    scope = scope_for_state(state)
    overview = artifact_entry(
        resource_type="order_overview",
        resource_id="10002-overview",
        facts={
            "order_id": "10002",
            "product_name": "机械键盘",
            "delivery_status": "运输中",
        },
        scope=scope,
        turn=1,
        source="test",
        label="订单概览",
    )
    ledger = append_entries([], [overview])
    state["artifact_ledger"] = ledger

    refs = record_goal_outputs_from_tool_result(
        [],
        state=state,
        capability_registry=registry,
        tool_name="get_order_overview",
        goal_ids=["g1", "g2"],
        effect_id="effect:overview",
        result={"ok": True, "data": {"overview": overview["handle"]}},
        ledger_additions=[overview],
        merged_ledger=ledger,
    )

    assert len(refs) == 2
    by_goal = {row["producer_goal_id"]: row for row in refs}
    assert by_goal["g1"]["output_name"] == "overview"
    assert by_goal["g1"]["artifact_ref"] == overview["handle"]
    assert by_goal["g1"]["completion_proof"] is True
    assert by_goal["g1"]["completion_proof_output_name"] == "overview"
    assert by_goal["g2"]["output_name"] == "overview"
    assert by_goal["g2"]["artifact_ref"] == overview["handle"]
    assert by_goal["g2"]["completion_proof"] is True
    assert by_goal["g2"]["completion_proof_output_name"] == "overview"


def _state_and_output_for_targets(producer_order_id: str, consumer_order_id: str):
    from tests.runtime.test_pretool_execution_policy import _contract, _goal, _registry
    from agent_core.ledger import append_entries, artifact_entry, eligibility_entry, scope_for_state
    from agent_core.lifecycle.goal_outputs import record_goal_outputs_from_tool_result

    registry = _registry()
    eligibility_goal = _goal("eligibility", domain="refund", operation="evaluate_eligibility")
    eligibility_goal["target_candidate"] = {"order_id": producer_order_id}
    refund_goal = _goal("refund", domain="refund", operation="create", depends_on=("eligibility",))
    refund_goal["target_candidate"] = {"order_id": consumer_order_id}
    contract = _contract([eligibility_goal, refund_goal])
    state = {
        "current_tenant_id": "tenant-a",
        "current_user_id": "u001",
        "current_thread_id": "thread-1",
        "turn_index": 1,
        "frozen_semantic_contract": contract,
        "goal_records": [
            {"goal_id": "eligibility", "lifecycle": "COMPLETED"},
            {"goal_id": "refund", "lifecycle": "ACTIVE"},
        ],
    }
    scope = scope_for_state(state)
    order = artifact_entry(
        resource_type="order",
        resource_id=producer_order_id,
        facts={"order_id": producer_order_id},
        scope=scope, turn=1, source="test", label=producer_order_id,
    )
    eligibility = eligibility_entry(
        action_id="create_refund", operation="APPLY_REFUND",
        target_handle=order["handle"], input_values={}, preview={"decision": "ALLOWED"},
        scope=scope, turn=1, label="退款资格核验",
    )
    ledger = append_entries([], [order, eligibility])
    state["artifact_ledger"] = ledger
    state["goal_output_refs"] = record_goal_outputs_from_tool_result(
        [], state=state, capability_registry=registry,
        tool_name="evaluate_refund_eligibility", goal_ids=["eligibility"],
        effect_id="effect:eligibility",
        result={"ok": True, "data": {"eligibility_handle": eligibility["handle"]}},
        ledger_additions=[order, eligibility], merged_ledger=ledger,
    )
    return state, registry


def test_dependency_goal_output_is_not_reused_across_different_explicit_targets() -> None:
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    state, registry = _state_and_output_for_targets("10002", "10003")
    policy = build_pretool_execution_policy(state=state, capability_registry=registry)
    refund = {row["goal_id"]: row for row in policy["goal_policies"]}["refund"]

    assert "prepare_refund_from_eligibility" not in refund["allowed_tools"]
    assert refund["reused_goal_output_ref_ids"] == []
    assert "GOAL_OUTPUT_REF_TARGET_MISMATCH" in refund["goal_output_evidence_errors"]


def test_multi_goal_completion_recording_rejects_different_explicit_targets() -> None:
    from tests.runtime.test_global_goal_capability_coverage import _registry as overview_registry
    from tests.runtime.test_pretool_execution_policy import _contract, _goal
    from agent_core.ledger import append_entries, artifact_entry, scope_for_state
    from agent_core.lifecycle.goal_outputs import record_goal_outputs_from_tool_result

    registry = overview_registry()
    g1 = _goal("g1", domain="order", operation="query_details")
    g1["target_candidate"] = {"order_id": "10001"}
    g2 = _goal("g2", domain="order", operation="query_logistics")
    g2["target_candidate"] = {"order_id": "10002"}
    contract = _contract([g1, g2])
    state = {
        "current_tenant_id": "tenant-a", "current_user_id": "u001",
        "current_thread_id": "thread-1", "turn_index": 1,
        "frozen_semantic_contract": contract,
        "goal_records": [
            {"goal_id": "g1", "lifecycle": "COMPLETED"},
            {"goal_id": "g2", "lifecycle": "COMPLETED"},
        ],
    }
    scope = scope_for_state(state)
    overview = artifact_entry(
        resource_type="order_overview", resource_id="10001-overview",
        facts={"order_id": "10001"}, scope=scope, turn=1, source="test", label="订单概览",
    )
    ledger = append_entries([], [overview])
    state["artifact_ledger"] = ledger

    refs = record_goal_outputs_from_tool_result(
        [], state=state, capability_registry=registry, tool_name="get_order_overview",
        goal_ids=["g1", "g2"], effect_id="effect:overview",
        result={"ok": True, "data": {"overview": overview["handle"]}},
        ledger_additions=[overview], merged_ledger=ledger,
    )

    assert refs == []
