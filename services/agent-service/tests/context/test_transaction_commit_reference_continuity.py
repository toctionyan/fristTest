from __future__ import annotations

from agent_core.composition import get_runtime_registry
from agent_core.context.visible_result_refs import mark_visible_result_refs, visible_result_refs_from_ledger
from agent_core.ledger import artifact_entry, offer_entry
from agent_core.runtime.outcomes import outcome
from agent_core.transaction import transition_draft
from agent_core.transaction.commit_runtime import _transaction_commit_update
from agent_core.transaction.deps import TransactionExecutionDeps


SCOPE = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-cancel-continuity"}


def test_successful_cancel_commit_releases_refreshed_business_resource_as_next_turn_reference() -> None:
    """A terminal write may release only the verified business projection into discourse.

    Draft/Receipt remain transaction evidence. They must not become competing
    referent candidates merely because the UI commit path bypasses a model
    ``respond_to_user`` call.
    """
    get_runtime_registry()

    target = artifact_entry(
        resource_type="order",
        resource_id="10003",
        label="无线鼠标（订单 10003）",
        facts={"order_id": "10003", "status": "待发货", "version": 1},
        scope=SCOPE,
        turn=4,
        source="test",
        freshness_version=1,
        handle="artifact:order:10003",
    )
    offer = offer_entry(
        action_id="cancel_order",
        operation="CANCEL_ORDER",
        target_handle=target["handle"],
        input_values={"reason": "not_needed", "expected_version": 1},
        preview={"decision": "ALLOWED", "snapshot": {"version": 1}},
        scope=SCOPE,
        turn=4,
        label="取消订单",
        handle="draft:cancel:10003",
    )
    offer = transition_draft(offer, "COMMITTING")
    state = {
        "current_tenant_id": SCOPE["tenant_id"],
        "current_user_id": SCOPE["user_id"],
        "current_thread_id": SCOPE["thread_id"],
        "current_role": "customer",
        "turn_index": 5,
        "artifact_ledger": [target, offer],
        "action_queue": [],
        "tool_trace": [],
    }
    deps = TransactionExecutionDeps(business_port=object(), outcome_factory=outcome)  # type: ignore[arg-type]

    patch = _transaction_commit_update(
        state,
        [target, offer],
        offer,
        result={
            "success": True,
            "data": {"order_id": "10003", "resource_id": "10003", "status": "已取消", "version": 2},
        },
        draft_state="COMMITTED",
        attempt_id=None,
        idempotency_key=None,
        status="ActionCommitted",
        write_receipt=False,
        deps=deps,
    )

    # The public final-answer evidence is intentionally narrower than the
    # transaction RuntimeOutcome evidence: only the Business Service-derived
    # resource projection becomes an ordinary next-turn referent.
    assert patch["answer_evidence_handles"] == [target["handle"]]
    assert target["handle"] in patch["runtime_outcome"]["evidence_handles"]
    assert offer["handle"] in patch["runtime_outcome"]["evidence_handles"]

    released = mark_visible_result_refs(
        patch["artifact_ledger"],
        state={**state, **patch},
        evidence_handles=patch["answer_evidence_handles"],
    )
    refs = visible_result_refs_from_ledger(released, state={**state, **patch})

    assert len(refs) == 1
    assert refs[0]["result_ref"] == target["handle"]
    assert refs[0]["member_handles"] == [target["handle"]]
    released_target = next(row for row in released if row.get("handle") == target["handle"])
    assert released_target["resource_type"] == "order"
    assert released_target["facts"]["status"] == "已取消"
    assert released_target["presentation_origin"]["origin"] == "customer_final_response"
