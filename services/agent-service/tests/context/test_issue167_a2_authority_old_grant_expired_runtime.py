from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from agent_core.persistence.store_provider import get_store_provider
from agent_core.transaction.authority import UI_CONFIRMED, build_ui_authority
from agent_core.transaction.coordinator import (
    issue_grant_for_authority,
    reserve_grant_and_start_attempt,
    stable_idempotency_key,
)
from tests.support.conversation_case_runner import run_conversation_case


_CASE_PATH = Path(__file__).with_name("issue167_cases") / "authority_old_grant_expired_a2.json"
_ORACLE_PATH = Path(__file__).with_name("semantic_goal_oracle_evidence") / "authority_old_grant_expired_v20_4.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _single_offer(result: dict) -> dict:
    offers = [
        dict(row)
        for row in list(result.get("artifact_ledger") or [])
        if isinstance(row, dict) and str(row.get("kind") or "") == "offer"
    ]
    assert len(offers) == 1, offers
    return offers[0]


def test_issue167_old_expired_grant_cannot_start_attempt_or_business_write() -> None:
    case = _load(_CASE_PATH)
    oracle = _load(_ORACLE_PATH)

    # A1 stays independent from Runtime output and does not let semantic or
    # capability layers become transaction-authority owners.
    assert oracle["case_id"] == case["id"] == "authority_old_grant_expired"
    assert oracle["authority"]["derived_from_runtime_output"] is False
    assert oracle["authority"]["capability_availability_is_semantic_input"] is False
    assert oracle["a2_promotion_gate"]["runtime_edit_authorized"] is False

    # A2 first runs the real lifecycle graph. Plain language may prepare the
    # cancellation Draft, but it must stop at structured authorization.
    executed = run_conversation_case(case)
    final = executed.final.result
    offer = _single_offer(final)
    assert offer["draft_state"] == "AWAITING_AUTHORIZATION"
    assert executed.port.count("preview_operation") >= 1
    assert executed.port.count("execute_command") == 0

    provider = get_store_provider()
    store = provider.transactions
    thread_id = executed.final.thread_id
    tenant_id = str(final.get("current_tenant_id") or "tenant-a")
    user_id = str(final.get("current_user_id") or "u001")
    assert store.list_grants_by_thread(
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id=thread_id,
    ) == []

    # Now introduce authority only through the real structured-UI/transaction
    # coordinator boundary. Its TTL is already in the past, representing the
    # old Grant from the Case 12 contract.
    expired_offer = deepcopy(offer)
    expired_offer["expires_at"] = 946684800.0  # 2000-01-01T00:00:00Z
    current_revision = int(final.get("turn_index") or 1)
    payload = {
        "authority_type": UI_CONFIRMED,
        "offer_handle": str(expired_offer.get("handle") or ""),
        "action_id": str(expired_offer.get("action_id") or ""),
        "target_handle": str(expired_offer.get("target_handle") or ""),
        "confirmation_id": str(expired_offer.get("confirmation_id") or ""),
        "confirmation_version": int(expired_offer.get("confirmation_version") or 0),
        "conversation_revision": int(expired_offer.get("authority_revision") or current_revision),
        "client_request_id": f"issue167-case12-{thread_id}",
    }
    authority = build_ui_authority(
        payload=payload,
        actor_id=user_id,
        actor_role="customer",
        current_revision=current_revision,
    )
    state = deepcopy(final)
    state["current_thread_id"] = thread_id
    state["current_tenant_id"] = tenant_id
    state["current_user_id"] = user_id
    state["_transaction_repository"] = store
    authority = issue_grant_for_authority(state=state, offer=expired_offer, authority=authority)
    grant_id = str(authority.get("grant_id") or "")
    assert grant_id
    issued = store.get_grant(grant_id)
    assert issued is not None and issued["state"] == "ISSUED"
    assert str(issued.get("expires_at") or "").startswith("2000-01-01T00:00:00")

    before_writes = executed.port.count("execute_command")
    idempotency_key = stable_idempotency_key(state, expired_offer)
    reservation, started = reserve_grant_and_start_attempt(
        state=state,
        offer=expired_offer,
        authority=authority,
    )
    assert reservation["reserved"] is False
    assert started["created"] is False
    assert started["attempt"] == {}
    assert store.get_attempt_by_idempotency_key(idempotency_key) is None
    persisted = store.get_grant(grant_id)
    assert persisted is not None and persisted["state"] == "EXPIRED"
    assert persisted["reason"] == "grant_expired"
    assert executed.port.count("execute_command") == before_writes == 0

    # A retry/"continue" cannot revive the same durable Grant. It remains
    # terminal and still cannot mint a CommitAttempt or reach Business Service.
    retry_reservation, retry_started = reserve_grant_and_start_attempt(
        state=state,
        offer=expired_offer,
        authority=authority,
    )
    assert retry_reservation["reserved"] is False
    assert retry_started["created"] is False
    assert retry_started["attempt"] == {}
    assert store.get_attempt_by_idempotency_key(idempotency_key) is None
    assert store.get_grant(grant_id)["state"] == "EXPIRED"
    assert executed.port.count("execute_command") == 0
