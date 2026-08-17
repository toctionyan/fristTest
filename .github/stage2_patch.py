from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def patch_once(path: str, old: str, new: str, *, label: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{label} patch anchor mismatch: {text.count(old)}")
    target.write_text(text.replace(old, new), encoding="utf-8")


patch_once(
    "services/agent-service/src/agent_core/transaction/coordinator.py",
    '    keys=("kind","handle","draft_id","draft_revision","draft_state","label","action_id","operation","target_handle","input_values","preview","required_inputs","scope","expires_at","created_at","updated_at","transaction_schema_version","transaction_contract_version","operation_capability_id","operation_capability_version","operation_capability_digest","operation_capability_snapshot","command_id","command_digest","business_command_envelope")\n',
    '''    keys = (\n        "kind", "handle", "draft_id", "draft_revision", "draft_state", "label",\n        "action_id", "operation", "target_handle", "input_values", "preview",\n        "required_inputs", "input_schema", "input_form_id", "input_form_version",\n        "input_step", "interaction_revision", "input_errors", "suggested_input",\n        "authority_protocol", "authority_requirement", "authority_revision",\n        "confirmation_id", "confirmation_version", "scope", "expires_at",\n        "created_at", "updated_at", "transaction_schema_version",\n        "transaction_contract_version", "operation_capability_id",\n        "operation_capability_version", "operation_capability_digest",\n        "operation_capability_snapshot", "command_id", "command_digest",\n        "business_command_envelope",\n    )\n''',
    label="coordinator projection",
)

patch_once(
    "services/agent-service/src/agent_core/transaction/gateway_runtime.py",
    "from agent_core.transaction.coordinator import persist_draft_from_offer\n",
    "from agent_core.transaction.coordinator import persist_draft_from_offer, transaction_store\nfrom agent_core.transaction.interaction_recovery import restore_awaiting_authority_projection\n",
    label="gateway import",
)

patch_once(
    "services/agent-service/src/agent_core/transaction/gateway_runtime.py",
    '''def advance_transaction_gateway(state: dict[str, Any], *, deps: TransactionExecutionDeps) -> dict[str, Any]:\n    # The response contract wins over every normal loop/fallback path.  This\n    # makes a card recoverable even if a transport loses the interrupt delta.\n    existing_interaction = interaction_response_contract(state)\n    if existing_interaction is not None:\n        return {\n            "response_contract": existing_interaction,\n            "current_final_answer": None,\n            "phase": "offer_confirmation",\n            "status": "TransactionInteractionRequired",\n        }\n    queue = list(state.get("action_queue") or [])\n''',
    '''def advance_transaction_gateway(state: dict[str, Any], *, deps: TransactionExecutionDeps) -> dict[str, Any]:\n    # The response contract wins over every normal loop/fallback path.  This\n    # makes a card recoverable even if a transport loses the interrupt delta.\n    existing_interaction = interaction_response_contract(state)\n    if existing_interaction is not None:\n        return {\n            "response_contract": existing_interaction,\n            "current_final_answer": None,\n            "phase": "offer_confirmation",\n            "status": "TransactionInteractionRequired",\n        }\n\n    # A resumed Workflow checkpoint may have lost the ephemeral offer/card\n    # projection while the durable transaction repository still owns an\n    # AWAITING_AUTHORIZATION Draft. Restore only that projection; never\n    # prepare another Draft and never infer Grant authority from chat text.\n    restored = restore_awaiting_authority_projection(state, transactions=transaction_store(state))\n    if restored is not None:\n        restored_state = {**state, **restored}\n        restored_interaction = interaction_response_contract(restored_state)\n        if restored_interaction is not None:\n            restored_interaction = {**restored_interaction, "source": "transaction_repository_projection"}\n            return {\n                **restored,\n                "ledger_snapshot": ledger_cards(\n                    restored_state.get("artifact_ledger") or [],\n                    scope=scope_for_state(restored_state),\n                ),\n                "response_contract": restored_interaction,\n                "commit_authority": None,\n                "current_final_answer": None,\n                "phase": "offer_confirmation",\n                "status": "TransactionInteractionRestored",\n            }\n\n    queue = list(state.get("action_queue") or [])\n''',
    label="gateway recovery boundary",
)

interaction_recovery = '''from __future__ import annotations

"""Read-through projection recovery for durable pending transactions.

The transaction repository remains the only authority for Draft lifecycle.
This module may restore a lost Workflow/UI projection, but it never creates a
Draft, Grant or Attempt and never interprets user language as authorization.
"""

from typing import Any

from agent_core.ledger import append_entries
from agent_core.storage.repositories.base import TransactionScope
from agent_core.transaction.active_draft import active_draft_patch, get_active_draft_id

_AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"


def _scope(state: dict[str, Any]) -> TransactionScope:
    return TransactionScope(
        tenant_id=str(state.get("current_tenant_id") or "default"),
        user_id=str(state.get("current_user_id") or ""),
        thread_id=str(state.get("current_thread_id") or "") or None,
    )


def _authoritative_offer(row: dict[str, Any]) -> dict[str, Any] | None:
    projection = row.get("projection") if isinstance(row.get("projection"), dict) else {}
    draft_id = str(row.get("draft_id") or "")
    if not draft_id or not projection:
        return None
    offer = dict(projection)
    offer.update(
        {
            "kind": "offer",
            "handle": draft_id,
            "draft_id": draft_id,
            "draft_revision": int(row.get("draft_revision") or projection.get("draft_revision") or 1),
            "draft_state": str(row.get("draft_state") or ""),
            "action_id": str(row.get("action_id") or projection.get("action_id") or ""),
            "command_digest": str(row.get("command_digest") or projection.get("command_digest") or ""),
        }
    )
    if isinstance(row.get("command_envelope"), dict):
        offer["business_command_envelope"] = dict(row["command_envelope"])
    return offer


def _recoverable_authority_offer(row: dict[str, Any]) -> dict[str, Any] | None:
    if str(row.get("draft_state") or "").upper() != _AWAITING_AUTHORIZATION:
        return None
    offer = _authoritative_offer(row)
    if offer is None:
        return None
    # Reuse the exact persisted UI challenge. Missing challenge metadata fails
    # closed rather than minting a new token during Workflow recovery.
    if not str(offer.get("authority_protocol") or ""):
        return None
    if not str(offer.get("confirmation_id") or ""):
        return None
    if int(offer.get("confirmation_version") or 0) < 1:
        return None
    if int(offer.get("authority_revision") or 0) < 1:
        return None
    return offer


def restore_awaiting_authority_projection(
    state: dict[str, Any], *, transactions: Any
) -> dict[str, Any] | None:
    """Restore one unambiguous pending-authority card from its source of truth.

    A focused Draft is resolved exactly. If the ephemeral focus was also lost,
    recovery is allowed only when this thread has exactly one durable
    ``AWAITING_AUTHORIZATION`` Draft. Multiple candidates are never guessed.
    """
    scope = _scope(state)
    if not scope.user_id:
        return None
    focused = str(get_active_draft_id(state) or "")
    try:
        if focused:
            row = transactions.get_draft_for_scope(scope=scope, draft_id=focused)
            candidates = [row] if isinstance(row, dict) else []
        else:
            candidates = transactions.list_drafts_for_scope(
                scope=scope,
                states={_AWAITING_AUTHORIZATION},
                limit=2,
            )
    except Exception:
        return None
    if len(candidates) != 1:
        return None
    offer = _recoverable_authority_offer(dict(candidates[0]))
    if offer is None:
        return None
    draft_id = str(offer.get("draft_id") or offer.get("handle") or "")
    ledger = append_entries(list(state.get("artifact_ledger") or []), [offer])
    return {
        "artifact_ledger": ledger,
        **active_draft_patch(draft_id),
        "pending_confirmation_id": offer.get("confirmation_id"),
        "pending_confirmation_version": offer.get("confirmation_version"),
    }
'''
(ROOT / "services/agent-service/src/agent_core/transaction/interaction_recovery.py").write_text(
    interaction_recovery,
    encoding="utf-8",
)

stage2_test = '''from __future__ import annotations

from langchain_core.messages import HumanMessage

from agent_core.ledger import artifact_entry, offer_entry
from agent_core.persistence.action_lifecycle_store import TransactionLifecycleStore
from agent_core.runtime.outcomes import outcome
from agent_core.storage.repositories.base import TransactionScope
from agent_core.transaction.deps import TransactionExecutionDeps
from agent_core.transaction.gateway_runtime import _mark_offer_awaiting_authority, advance_transaction_gateway


SCOPE = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-refund-resume"}


class _NoBusinessCalls:
    def __getattr__(self, name: str):
        raise AssertionError(f"Workflow recovery must not call Business Service: {name}")


def test_resume_projects_existing_refund_draft_without_new_draft_grant_or_attempt(tmp_path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    order = artifact_entry(
        resource_type="order",
        resource_id="10002",
        label="机械键盘（订单 10002）",
        facts={"order_id": "10002", "status": "已签收", "version": 1},
        scope=SCOPE,
        turn=7,
        source="test",
        freshness_version=1,
        handle="artifact:order:10002",
    )
    offer = offer_entry(
        action_id="create_refund",
        operation="APPLY_REFUND",
        target_handle=order["handle"],
        input_values={"reason": "质量问题", "expected_version": 1},
        preview={"decision": "ALLOWED", "snapshot": {"version": 1}, "message": "可申请退款"},
        scope=SCOPE,
        turn=7,
        label="退款申请",
        handle="draft:refund:10002",
    )
    creating_state = {
        "current_tenant_id": SCOPE["tenant_id"],
        "current_user_id": SCOPE["user_id"],
        "current_thread_id": SCOPE["thread_id"],
        "turn_index": 7,
        "artifact_ledger": [order, offer],
        "_transaction_repository": store,
    }
    _ledger, pending = _mark_offer_awaiting_authority(creating_state, offer, [order, offer])
    draft_id = str(pending["draft_id"])
    original_confirmation_id = str(pending["confirmation_id"])
    original_revision = int(pending["draft_revision"])

    durable_before = store.get_draft(draft_id)
    assert durable_before is not None
    assert durable_before["draft_state"] == "AWAITING_AUTHORIZATION"
    assert durable_before["projection"]["confirmation_id"] == original_confirmation_id

    resumed_state = {
        "current_tenant_id": SCOPE["tenant_id"],
        "current_user_id": SCOPE["user_id"],
        "current_thread_id": SCOPE["thread_id"],
        "current_role": "customer",
        "turn_index": 8,
        "messages": [HumanMessage(content="继续")],
        "artifact_ledger": [order],
        "action_queue": [],
        "_transaction_repository": store,
    }
    patch = advance_transaction_gateway(
        resumed_state,
        deps=TransactionExecutionDeps(business_port=_NoBusinessCalls(), outcome_factory=outcome),
    )

    assert patch["status"] == "TransactionInteractionRestored"
    assert patch["focused_draft_id"] == draft_id
    assert patch["active_draft_id"] == draft_id
    assert patch["commit_authority"] is None
    contract = patch["response_contract"]
    assert contract["source"] == "transaction_repository_projection"
    interaction = contract["interaction"]
    assert interaction["interaction_id"] == draft_id
    assert interaction["lifecycle"] == "awaiting_authority"
    assert interaction["control"]["confirmation_id"] == original_confirmation_id
    assert interaction["control"]["authority_type_required"] == "ui_confirmed"

    tx_scope = TransactionScope(**SCOPE)
    drafts = store.list_drafts_for_scope(scope=tx_scope, states=None, limit=20)
    assert len(drafts) == 1
    assert drafts[0]["draft_id"] == draft_id
    assert drafts[0]["draft_revision"] == original_revision
    assert drafts[0]["draft_state"] == "AWAITING_AUTHORIZATION"
    assert store.list_grants_by_thread(**SCOPE) == []
    assert store.list_attempts_for_draft(scope=tx_scope, draft_id=draft_id) == []
'''
(ROOT / "services/agent-service/tests/transactions/test_stage2_transaction_projection_recovery.py").write_text(
    stage2_test,
    encoding="utf-8",
)
