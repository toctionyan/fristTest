from __future__ import annotations

from tests.support.paths import agent_root

from pathlib import Path

from agent_core.context import ContextBundleBuilder
from agent_core.ledger import append_entries, artifact_entry, offer_entry
from agent_core.storage.repositories.base import TransactionScope
from agent_core.transaction import transition_draft
from app.services.sse_stream_adapter import SseStreamAdapter


def _state(*, ledger, active_draft_id: str | None = None, turn: int = 20) -> dict:
    return {
        "current_tenant_id": "tenant-a",
        "current_user_id": "u001",
        "current_thread_id": "thread-a",
        "turn_index": turn,
        "artifact_ledger": ledger,
        "active_draft_id": active_draft_id,
        "tool_trace": [],
        "task_board": [],
        "messages": [],
    }


class _Transactions:
    def __init__(self, rows=None, error: Exception | None = None):
        self.rows = list(rows or [])
        self.error = error

    def list_drafts_for_scope(self, *, scope, states=None, limit=50, cursor=None):
        assert isinstance(scope, TransactionScope)
        if self.error:
            raise self.error
        return self.rows


def test_context_bundle_exposes_bounded_verified_fact_summary_without_target_selection():
    scope = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-a"}
    # Higher updated_turn means higher ledger priority after normalization.
    ledger = [
        artifact_entry(
            resource_type="order",
            resource_id=str(index),
            label=f"订单 {index}",
            facts={"order_id": str(index)},
            scope=scope,
            turn=100 - index,
            source="test",
            handle=f"artifact:{index}",
        )
        for index in range(20)
    ]
    pack = ContextBundleBuilder(transactions=_Transactions()).build(_state(ledger=ledger))
    handles = [row["handle"] for row in pack["verified_fact_summary"]]
    assert handles == [f"artifact:{index}" for index in range(12)]
    assert "active_facts" not in pack


def test_context_bundle_does_not_pin_active_draft_target_as_hidden_focus():
    scope = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-a"}
    high = [
        artifact_entry(
            resource_type="order", resource_id=str(index), label=f"订单 {index}",
            facts={"order_id": str(index)}, scope=scope, turn=50-index,
            source="test", handle=f"artifact:high:{index}",
        )
        for index in range(20)
    ]
    target = artifact_entry(
        resource_type="order", resource_id="target", label="当前退款目标",
        facts={"order_id": "target"}, scope=scope, turn=1, source="test", handle="artifact:target",
    )
    offer = transition_draft(
        offer_entry(
            action_id="create_refund", operation="APPLY_REFUND", target_handle=target["handle"],
            input_values={"expected_version": 1}, preview={"decision": "ALLOWED"},
            scope=scope, turn=1, label="退款申请", handle="offer:target",
        ),
        "READY",
    )
    pack = ContextBundleBuilder(transactions=_Transactions()).build(
        _state(ledger=append_entries([*high, target], [offer]), active_draft_id="offer:target")
    )
    assert "artifact:target" not in [row["handle"] for row in pack["verified_fact_summary"]]
    assert "current_focus" not in pack


def test_context_bundle_transaction_repository_failure_is_explicit_not_empty_semantics():
    pack = ContextBundleBuilder(transactions=_Transactions(error=RuntimeError("db unavailable"))).build(_state(ledger=[]))
    assert pack["context_health"]["transactions"] == "unavailable"
    assert pack["active_transaction_state"] == []


def test_context_bundle_does_not_inject_soft_task_board_as_semantic_authority():
    state = _state(ledger=[])
    state["task_board"] = [
        {"task_id": "old", "title": "old", "status": "active", "updated_turn": 1, "updated_at": 1000},
        {"task_id": "new", "title": "new", "status": "active", "updated_turn": 9, "updated_at": 1},
    ]
    pack = ContextBundleBuilder(transactions=_Transactions()).build(state)
    assert "soft_task_summary" not in pack


def test_sse_adapter_unwraps_node_delta_and_preserves_public_shape():
    adapter = SseStreamAdapter(lambda delta: {key: delta[key] for key in ("phase", "active_draft_id") if key in delta})
    payload = adapter.project_public_update({"action_gateway": {"phase": "offer_confirmation", "active_draft_id": "x"}})
    assert payload == {"node": "action_gateway", "phase": "offer_confirmation", "active_draft_id": "x"}


def test_runtime_layers_use_only_canonical_draft_lifecycle_fields():
    root = agent_root(__file__) / "src" / "agent_core"
    forbidden = ('offer.get("status")', 'offer.get("action_state")', 'offer["status"]', 'offer["action_state"]')
    allowed = {
        root / "transaction" / "model.py",
        root / "ledger" / "ledger.py",
        root / "transaction" / "interaction.py",
    }
    offenders: list[str] = []
    for path in [
        root / "transaction" / "gateway_runtime.py",
        root / "transaction" / "interaction_runtime.py",
        root / "transaction" / "commit_runtime.py",
        root / "lifecycle" / "tool_execution_runtime.py",
        root / "context" / "context_bundle.py",
    ]:
        text = path.read_text(encoding="utf-8")
        if any(pattern in text for pattern in forbidden):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_transaction_context_unavailable_blocks_model_and_new_draft():
    from agent_core.lifecycle.context_runtime import build_context_bundle_node
    from agent_core.lifecycle.dialogue_runtime import agent_loop_node

    state = _state(ledger=[], active_draft_id="offer:missing")
    state["transaction_context_hint"] = True
    bundle_update = build_context_bundle_node(
        state,
        context_bundle_builder=ContextBundleBuilder(transactions=_Transactions(error=RuntimeError("db unavailable"))),
    )
    update = agent_loop_node(
        {**state, **bundle_update},
        context_bundle_builder=ContextBundleBuilder(transactions=_Transactions(error=RuntimeError("db unavailable"))),
        capability_registry=object(),
        model_resolver=lambda: (_ for _ in ()).throw(AssertionError("model must not run")),
    )
    assert update["status"] == "TransactionContextUnavailable"
    assert "不会创建新的业务申请" in update["current_final_answer"]


def test_runtime_offer_keeps_only_canonical_draft_state_but_ledger_projects_display_status():
    from agent_core.ledger import ledger_cards, offer_entry

    scope = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-a"}
    offer = transition_draft(
        offer_entry(
            action_id="create_refund",
            operation="APPLY_REFUND",
            target_handle="artifact:order:1",
            input_values={"expected_version": 1},
            preview={"decision": "ALLOWED"},
            scope=scope,
            turn=1,
            label="退款申请",
            handle="offer:canonical",
        ),
        "READY",
    )
    assert offer["draft_state"] == "READY"
    assert "status" not in offer
    assert "action_state" not in offer
    cards = ledger_cards([offer], scope=scope)
    assert cards["offers"][0]["status"] == "ready"


def test_graph_requires_explicit_runtime_dependencies():
    import pytest
    from agent_core.lifecycle.graph import build_lifecycle_graph

    with pytest.raises(TypeError):
        build_lifecycle_graph()  # type: ignore[call-arg]


def test_clean_mainline_has_no_retired_mirror_calls_or_hidden_context_store_lookup():
    root = agent_root(__file__)
    coordinator = (root / "src" / "agent_core" / "transaction" / "coordinator.py").read_text(encoding="utf-8")
    reconciliation = (root / "src" / "agent_core" / "transaction" / "reconciliation.py").read_text(encoding="utf-8")
    context_bundle = (root / "src" / "agent_core" / "context" / "context_bundle.py").read_text(encoding="utf-8")
    assert "_safe_retired_start" not in coordinator
    assert "record_attempt_outcome" not in coordinator
    assert "record_attempt_outcome_fn" not in reconciliation
    assert "get_store_provider" not in context_bundle


def test_agent_service_uses_use_cases_and_not_graph_gateway_node_directly():
    root = agent_root(__file__)
    source = (root / "app" / "services" / "agent_service.py").read_text(encoding="utf-8")
    assert "TransactionStartUseCase" in source
    assert "InteractionSubmitUseCase" in source
    assert "ConversationTurnService" in source
    assert "action_gateway_node" not in source
