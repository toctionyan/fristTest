"""Execute every strong-context catalog entry against public runtime contracts."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agent_core.context.visible_result_refs import mark_visible_result_refs, validate_visible_result_ref
from agent_core.ledger import artifact_entry, offer_entry, result_entry
from agent_core.transaction.active_draft import active_draft_patch
from agent_core.transaction.model import command_digest_for_offer, transition_draft


CATALOG = Path(__file__).parent / "strong_context_cases"


def _cases() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(CATALOG.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        # Schema-v3 runtime suites and semantic Goal Oracle suites have their
        # own full lifecycle runners.  This legacy contract test only executes
        # the small, direct public-contract catalogs whose execution_contract
        # is a string identifier.
        if payload.get("contract_scope") or int(payload.get("schema_version") or 0) >= 2:
            continue
        rows.extend(payload["cases"])
    return rows


def _visible_state() -> tuple[dict, list[dict], str]:
    scope = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-1"}
    first = artifact_entry(
        resource_type="order", resource_id="10001", label="订单 10001", facts={}, scope=scope, turn=1, source="test", handle="h:order:1"
    )
    second = artifact_entry(
        resource_type="order", resource_id="10002", label="订单 10002", facts={}, scope=scope, turn=1, source="test", handle="h:order:2"
    )
    result = result_entry(
        capability="list_orders", member_handles=[first["handle"], second["handle"]], labels=[first["label"], second["label"]],
        scope=scope, turn=1, source_target={"mode": "all_orders"}, handle="h:result:orders"
    )
    state = {"current_tenant_id": "tenant-a", "current_user_id": "u001", "current_thread_id": "thread-1", "turn_index": 1}
    ledger = mark_visible_result_refs([first, second, result], state=state, evidence_handles=[result["handle"]])
    return state, ledger, result["handle"]


@pytest.mark.parametrize("case", _cases(), ids=lambda row: row["id"])
def test_catalog_case_executes_declared_runtime_contract(case: dict) -> None:
    state, ledger, result_handle = _visible_state()
    contract = case["execution_contract"]

    if contract in {"visible_collection_ref_is_scoped", "visible_collection_requires_explicit_target"}:
        ref, error = validate_visible_result_ref(state={**state, "artifact_ledger": ledger}, result_ref=result_handle, expected_shape="collection")
        assert error is None
        assert ref and len(ref["member_handles"]) == 2
    elif contract == "active_draft_pointer_is_explicit":
        assert active_draft_patch("draft:replacement") == {"active_draft_id": "draft:replacement"}
    elif contract == "draft_state_remains_non_committing_before_authority":
        draft = offer_entry(
            action_id="create_refund", operation="APPLY_REFUND", target_handle="h:order:1", input_values={}, preview={},
            scope={"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-1"}, turn=1, label="退款"
        )
        assert transition_draft(draft, "READY")["draft_state"] == "READY"
    elif contract == "effect_payload_digest_changes_with_target":
        offer = {"scope": {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-1"}, "action_id": "create_refund", "operation": "APPLY_REFUND", "target_handle": "h:order:1", "input_values": {"reason": "坏了"}, "preview": {"snapshot": {"version": 1}}}
        changed = deepcopy(offer)
        changed["target_handle"] = "h:order:2"
        assert command_digest_for_offer(offer) != command_digest_for_offer(changed)
    elif contract == "submission_unknown_is_a_canonical_draft_state":
        draft = offer_entry(
            action_id="cancel_order", operation="CANCEL_ORDER", target_handle="h:order:1", input_values={}, preview={},
            scope={"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-1"}, turn=1, label="取消"
        )
        assert transition_draft(draft, "SUBMISSION_UNKNOWN")["draft_state"] == "SUBMISSION_UNKNOWN"
    elif contract == "unknown_tool_has_no_registered_capability":
        from agent_core.composition import get_runtime_registry

        assert get_runtime_registry().capabilities.contract_for_tool("look_up_delivery_person_phone") is None
    else:  # The catalog verifier keeps this branch unreachable unless a case is incomplete.
        raise AssertionError(f"unknown execution contract: {contract}")
