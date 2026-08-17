from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def patch_once(path: str, old: str, new: str, *, label: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} patch anchor mismatch: {count}")
    target.write_text(text.replace(old, new), encoding="utf-8")


# Persist only stable target identity/label with the Draft.  This is recovery
# provenance, not a second copy of Business Service state.
patch_once(
    "services/agent-service/src/agent_core/transaction/coordinator.py",
    '        "action_id", "operation", "target_handle", "input_values", "preview",\n',
    '        "action_id", "operation", "target_handle", "target_reference", "input_values", "preview",\n',
    label="durable target reference projection",
)

patch_once(
    "services/agent-service/src/agent_core/transaction/gateway_runtime.py",
    '''    next_offer["confirmation_version"] = int(offer.get("confirmation_version") or 0) + 1\n    next_offer["updated_turn"] = int(state.get("turn_index") or 0)\n    persist_draft_from_offer(state=state, offer=next_offer, draft_state="AWAITING_AUTHORIZATION")\n''',
    '''    next_offer["confirmation_version"] = int(offer.get("confirmation_version") or 0) + 1\n    next_offer["updated_turn"] = int(state.get("turn_index") or 0)\n\n    # Persist a locator for the already-verified target so a lost Workflow\n    # checkpoint can rebuild the card and commit preflight boundary.  Do not\n    # copy mutable business facts here: Business Service remains authoritative\n    # and commit-time preflight must re-read current state.\n    existing_reference = offer.get("target_reference") if isinstance(offer.get("target_reference"), dict) else None\n    if existing_reference is not None:\n        next_offer["target_reference"] = deepcopy(existing_reference)\n    else:\n        target = find_handle(\n            ledger,\n            str(offer.get("target_handle") or ""),\n            scope=scope_for_state(state),\n            allowed_kinds={"artifact"},\n            active_only=False,\n        )\n        if target is not None:\n            next_offer["target_reference"] = {\n                "handle": str(target.get("handle") or ""),\n                "resource_type": str(target.get("resource_type") or ""),\n                "resource_id": str(target.get("resource_id") or ""),\n                "label": str(target.get("label") or ""),\n                "scope": deepcopy(target.get("scope") or scope_for_state(state)),\n            }\n    persist_draft_from_offer(state=state, offer=next_offer, draft_state="AWAITING_AUTHORIZATION")\n''',
    label="awaiting-authority target locator",
)

patch_once(
    "services/agent-service/src/agent_core/transaction/interaction_recovery.py",
    "from agent_core.ledger import append_entries\n",
    "from agent_core.ledger import append_entries, artifact_entry, find_handle, scope_for_state\n",
    label="recovery ledger imports",
)

patch_once(
    "services/agent-service/src/agent_core/transaction/interaction_recovery.py",
    '''def restore_awaiting_authority_projection(\n    state: dict[str, Any], *, transactions: Any\n) -> dict[str, Any] | None:\n''',
    '''def _restore_target_reference(\n    state: dict[str, Any], ledger: list[dict[str, Any]], offer: dict[str, Any]\n) -> list[dict[str, Any]] | None:\n    """Restore only stable target identity, never mutable business facts."""\n    handle = str(offer.get("target_handle") or "")\n    if not handle:\n        return None\n    if find_handle(\n        ledger,\n        handle,\n        scope=scope_for_state(state),\n        allowed_kinds={"artifact"},\n        active_only=False,\n    ) is not None:\n        return ledger\n\n    reference = offer.get("target_reference") if isinstance(offer.get("target_reference"), dict) else {}\n    if str(reference.get("handle") or "") != handle:\n        return None\n    resource_type = str(reference.get("resource_type") or "")\n    resource_id = str(reference.get("resource_id") or "")\n    if not resource_type or not resource_id:\n        return None\n\n    expected_scope = scope_for_state(state)\n    stored_scope = reference.get("scope") if isinstance(reference.get("scope"), dict) else {}\n    for key in ("tenant_id", "user_id", "thread_id"):\n        stored = str(stored_scope.get(key) or "")\n        expected = str(expected_scope.get(key) or "")\n        if stored and stored != expected:\n            return None\n\n    restored_target = artifact_entry(\n        resource_type=resource_type,\n        resource_id=resource_id,\n        label=str(reference.get("label") or f"{resource_type}:{resource_id}"),\n        facts={},\n        scope=expected_scope,\n        turn=int(state.get("turn_index") or 0),\n        source="transaction_repository_target_reference",\n        freshness_version=1,\n        handle=handle,\n    )\n    return append_entries(ledger, [restored_target])\n\n\ndef restore_awaiting_authority_projection(\n    state: dict[str, Any], *, transactions: Any\n) -> dict[str, Any] | None:\n''',
    label="target reference recovery helper",
)

patch_once(
    "services/agent-service/src/agent_core/transaction/interaction_recovery.py",
    '''    draft_id = str(offer.get("draft_id") or offer.get("handle") or "")\n    ledger = append_entries(list(state.get("artifact_ledger") or []), [offer])\n    return {\n''',
    '''    draft_id = str(offer.get("draft_id") or offer.get("handle") or "")\n    ledger = list(state.get("artifact_ledger") or [])\n    ledger = _restore_target_reference(state, ledger, offer)\n    if ledger is None:\n        # A card without a trustworthy target locator would be actionable but\n        # impossible to preflight safely. Fail closed instead of inventing a\n        # target from conversation text or creating a replacement Draft.\n        return None\n    ledger = append_entries(ledger, [offer])\n    return {\n''',
    label="restore target before offer",
)

patch_once(
    "services/agent-service/tests/context/test_transaction_commit_reference_continuity.py",
    "from agent_core.context.visible_result_refs import mark_visible_result_refs, visible_result_refs_from_ledger\n",
    "from agent_core.context.visible_result_refs import mark_visible_result_refs, validate_runtime_result_ref, visible_result_refs_from_ledger\n",
    label="Stage 1 next-turn validator import",
)

patch_once(
    "services/agent-service/tests/context/test_transaction_commit_reference_continuity.py",
    '''    assert released_target["facts"]["status"] == "已取消"\n    assert released_target["presentation_origin"]["origin"] == "customer_final_response"\n''',
    '''    assert released_target["facts"]["status"] == "已取消"\n    assert released_target["presentation_origin"]["origin"] == "customer_final_response"\n\n    # Prove the next user turn can bind "它" to the released cancelled order.\n    next_turn_state = {\n        **state,\n        **patch,\n        "turn_index": 6,\n        "current_user_input": "它现在什么状态？",\n        "artifact_ledger": released,\n        "tool_trace": [],\n    }\n    checked, error = validate_runtime_result_ref(\n        state=next_turn_state,\n        result_ref=target["handle"],\n        expected_shape="one",\n    )\n    assert error is None\n    assert checked is not None\n    assert checked["reference_kind"] == "customer_visible"\n    assert checked["member_handles"] == [target["handle"]]\n''',
    label="Stage 1 next-turn reference proof",
)

patch_once(
    "services/agent-service/tests/transactions/test_stage2_transaction_projection_recovery.py",
    '''    assert durable_before["draft_state"] == "AWAITING_AUTHORIZATION"\n    assert durable_before["projection"]["confirmation_id"] == original_confirmation_id\n\n    resumed_state = {\n''',
    '''    assert durable_before["draft_state"] == "AWAITING_AUTHORIZATION"\n    assert durable_before["projection"]["confirmation_id"] == original_confirmation_id\n    target_reference = durable_before["projection"]["target_reference"]\n    assert target_reference["handle"] == order["handle"]\n    assert target_reference["resource_type"] == "order"\n    assert target_reference["resource_id"] == "10002"\n    assert "facts" not in target_reference\n\n    resumed_state = {\n''',
    label="Stage 2 durable target reference assertions",
)

patch_once(
    "services/agent-service/tests/transactions/test_stage2_transaction_projection_recovery.py",
    '''        "messages": [HumanMessage(content="继续")],\n        "artifact_ledger": [order],\n        "action_queue": [],\n''',
    '''        "messages": [HumanMessage(content="继续")],\n        # Harder crash/restart case: both the ephemeral Draft/card and target\n        # artifact are gone from Workflow state. Only Transaction Repository\n        # survives.\n        "artifact_ledger": [],\n        "action_queue": [],\n''',
    label="Stage 2 lost target crash scenario",
)

patch_once(
    "services/agent-service/tests/transactions/test_stage2_transaction_projection_recovery.py",
    '''    assert interaction["interaction_id"] == draft_id\n    assert interaction["lifecycle"] == "awaiting_authority"\n    assert interaction["control"]["confirmation_id"] == original_confirmation_id\n    assert interaction["control"]["authority_type_required"] == "ui_confirmed"\n\n    tx_scope = TransactionScope(**SCOPE)\n''',
    '''    assert interaction["interaction_id"] == draft_id\n    assert interaction["lifecycle"] == "awaiting_authority"\n    assert interaction["target"] == "机械键盘（订单 10002）"\n    assert interaction["control"]["confirmation_id"] == original_confirmation_id\n    assert interaction["control"]["authority_type_required"] == "ui_confirmed"\n\n    recovered_target = next(\n        row for row in patch["artifact_ledger"] if row.get("handle") == order["handle"]\n    )\n    assert recovered_target["resource_type"] == "order"\n    assert recovered_target["resource_id"] == "10002"\n    assert recovered_target["facts"] == {}\n    assert recovered_target["source"] == "transaction_repository_target_reference"\n\n    tx_scope = TransactionScope(**SCOPE)\n''',
    label="Stage 2 recovered target assertions",
)

# Add an ambiguity counterexample: without a focused Draft, two durable pending
# Drafts must never be guessed between and no authority may be minted.
test_path = ROOT / "services/agent-service/tests/transactions/test_stage2_transaction_projection_recovery.py"
text = test_path.read_text(encoding="utf-8")
if "test_recovery_never_guesses_between_two_pending_authority_drafts" in text:
    raise SystemExit("ambiguity counterexample already present")
text += '''\n\ndef test_recovery_never_guesses_between_two_pending_authority_drafts(tmp_path) -> None:\n    store = TransactionLifecycleStore(tmp_path / "agent.db")\n    order_a = artifact_entry(\n        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",\n        facts={"order_id": "10002", "version": 1}, scope=SCOPE, turn=7, source="test",\n        freshness_version=1, handle="artifact:order:10002",\n    )\n    order_b = artifact_entry(\n        resource_type="order", resource_id="10004", label="定制马克杯（订单 10004）",\n        facts={"order_id": "10004", "version": 1}, scope=SCOPE, turn=7, source="test",\n        freshness_version=1, handle="artifact:order:10004",\n    )\n    offers = [\n        offer_entry(\n            action_id="create_refund", operation="APPLY_REFUND", target_handle=order_a["handle"],\n            input_values={"reason": "质量问题", "expected_version": 1},\n            preview={"decision": "ALLOWED", "snapshot": {"version": 1}},\n            scope=SCOPE, turn=7, label="退款申请 A", handle="draft:refund:10002",\n        ),\n        offer_entry(\n            action_id="create_refund", operation="APPLY_REFUND", target_handle=order_b["handle"],\n            input_values={"reason": "质量问题", "expected_version": 1},\n            preview={"decision": "ALLOWED", "snapshot": {"version": 1}},\n            scope=SCOPE, turn=7, label="退款申请 B", handle="draft:refund:10004",\n        ),\n    ]\n    creating_state = {\n        "current_tenant_id": SCOPE["tenant_id"], "current_user_id": SCOPE["user_id"],\n        "current_thread_id": SCOPE["thread_id"], "turn_index": 7,\n        "artifact_ledger": [order_a, order_b, *offers], "_transaction_repository": store,\n    }\n    ledger = list(creating_state["artifact_ledger"])\n    ledger, _ = _mark_offer_awaiting_authority(creating_state, offers[0], ledger)\n    ledger, _ = _mark_offer_awaiting_authority(creating_state, offers[1], ledger)\n\n    resumed_state = {\n        "current_tenant_id": SCOPE["tenant_id"], "current_user_id": SCOPE["user_id"],\n        "current_thread_id": SCOPE["thread_id"], "current_role": "customer",\n        "turn_index": 8, "messages": [HumanMessage(content="继续")],\n        "artifact_ledger": [], "action_queue": [], "_transaction_repository": store,\n    }\n    patch = advance_transaction_gateway(\n        resumed_state,\n        deps=TransactionExecutionDeps(business_port=_NoBusinessCalls(), outcome_factory=outcome),\n    )\n\n    assert patch["status"] == "NoActionProposal"\n    assert patch["response_contract"] is None\n    tx_scope = TransactionScope(**SCOPE)\n    drafts = store.list_drafts_for_scope(scope=tx_scope, states={"AWAITING_AUTHORIZATION"}, limit=20)\n    assert len(drafts) == 2\n    assert store.list_grants_by_thread(**SCOPE) == []\n    for draft in drafts:\n        assert store.list_attempts_for_draft(scope=tx_scope, draft_id=draft["draft_id"]) == []\n'''
test_path.write_text(text, encoding="utf-8")
