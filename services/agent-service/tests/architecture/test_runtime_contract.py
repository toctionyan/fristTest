from __future__ import annotations

from tests.support.paths import agent_root

from pathlib import Path

from agent_core.composition import get_runtime_registry
from agent_core.context import ContextBundleBuilder
from agent_core.ledger import result_entry, artifact_entry, offer_entry
from agent_core.operations.capability import OperationCapability
from agent_core.persistence.action_lifecycle_store import TransactionLifecycleStore
from agent_core.runtime.outcomes import fail_closed_outcome, outcome
from agent_core.storage.repositories.base import TransactionScope
from agent_core.resources.targets import TargetResolver, resolved_target_set
from agent_core.transaction import DRAFT_REQUIRES_REVIEW, transition_draft
from agent_core.transaction.capability_snapshot import attach_snapshot, has_complete_snapshot
from agent_core.transaction.lifecycle_query import TransactionLifecycleQuery
from agent_core.transaction.operation_preparation import OperationPreparationRuntime
from app.services.response_projector import ResponseProjector


def _scope() -> dict[str, str]:
    return {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-a"}


def _state(*, ledger=None, active_draft_id=None, text="两个都退", turn=8) -> dict:
    return {
        "current_tenant_id": "tenant-a",
        "current_user_id": "u001",
        "current_thread_id": "thread-a",
        "turn_index": turn,
        "current_user_input": text,
        "artifact_ledger": list(ledger or []),
        "active_draft_id": active_draft_id,
        "task_board": [],
        "tool_trace": [],
        "messages": [],
    }


def _store(tmp_path: Path) -> TransactionLifecycleStore:
    store = TransactionLifecycleStore(tmp_path / "transactions.db")
    store.init_db()
    return store


def _persist_draft(store: TransactionLifecycleStore, *, draft_id: str, thread_id: str, state: str = "AWAITING_AUTHORIZATION") -> None:
    store.create_draft(
        draft_id=draft_id,
        tenant_id="tenant-a",
        user_id="u001",
        thread_id=thread_id,
        draft_revision=1,
        draft_state=state,
        action_id="create_refund",
        command_digest="digest",
        command_envelope={"action_id": "create_refund"},
        projection={"label": f"退款 {draft_id}"},
    )


def test_every_registered_action_declares_single_target_capability():
    registry = get_runtime_registry().operations
    assert registry.all()
    for plugin in registry.all():
        capability = plugin.operation_capability
        assert isinstance(capability, OperationCapability)
        assert capability.target_cardinality == "exactly_one"
        assert capability.max_targets == 1
        assert capability.execution_mode == "single"
        assert capability.supports_lifecycle_query is True


def test_operation_preparation_rejects_two_targets_before_preview_or_draft():
    target_set = TargetResolver(get_runtime_registry().resources).from_verified_members(
        resource_type="order",
        handles=["artifact:order:10002", "artifact:order:10004"],
        source="collection",
        evidence_handles=["view:delivered"],
        resolution_basis="collection",
        resolved_at_turn=8,
    )
    prepared, result = OperationPreparationRuntime(outcome_factory=outcome).prepare(action_id="create_refund", target_set=target_set)
    assert prepared is None
    assert result is not None
    assert result.outcome_type == "unsupported_cardinality"
    assert result.effects == "none"
    assert result.payload["target_count"] == 2
    assert result.next_interaction == "need_selection"


def test_operation_preparation_rejects_unverified_target_before_preview():
    target_set = resolved_target_set(
        resource_type="order",
        handles=["artifact:order:10002"],
        source="untrusted",
        scope_verified=False,
        evidence_handles=[],
        resolution_basis="explicit_handle",
        resolved_at_turn=8,
    )
    prepared, result = OperationPreparationRuntime(outcome_factory=outcome).prepare(action_id="create_refund", target_set=target_set)
    assert prepared is None
    assert result is not None
    assert result.outcome_type == "failure"
    assert "未创建或提交" in result.customer_safe_summary


def test_new_draft_freezes_complete_capability_snapshot():
    scope = _scope()
    offer = attach_snapshot(
        offer_entry(
            action_id="create_refund",
            operation="APPLY_REFUND",
            target_handle="artifact:order:10002",
            input_values={"expected_version": 1},
            preview={"decision": "ALLOWED"},
            scope=scope,
            turn=1,
            label="退款申请",
            handle="offer:v17",
        )
    )
    offer = transition_draft(offer, "READY")
    assert has_complete_snapshot(offer)
    assert offer["operation_capability_id"] == "operation.create_refund"
    assert offer["operation_capability_snapshot"]["max_targets"] == 1


def test_unknown_runtime_outcome_is_forced_to_fail_closed():
    value = outcome("surprise_success", customer_safe_summary="pretend success")
    assert value.outcome_type == "failure"
    assert value.effects == "none"
    assert "未确认创建或提交" in value.customer_safe_summary
    assert value.payload["reason"] == "unknown_runtime_outcome"
    assert fail_closed_outcome().outcome_type == "failure"




def test_lifecycle_query_uses_cross_thread_unique_user_record(tmp_path: Path):
    store = _store(tmp_path)
    _persist_draft(store, draft_id="offer:cross-thread", thread_id="older-thread", state="COMMITTED")
    result = TransactionLifecycleQuery(store, outcome_factory=outcome).query(state=_state(active_draft_id=None, text="成功了吗"))
    assert result.outcome_type == "transaction_status"
    assert result.payload["draft"]["draft_id"] == "offer:cross-thread"
    assert result.payload["reference_mode"] == "user_recent_unique"


def test_lifecycle_query_requires_selection_for_multiple_recent_records(tmp_path: Path):
    store = _store(tmp_path)
    _persist_draft(store, draft_id="offer:refund", thread_id="thread-a")
    _persist_draft(store, draft_id="offer:invoice", thread_id="other-thread")
    result = TransactionLifecycleQuery(store, outcome_factory=outcome).query(state=_state(active_draft_id=None, text="成功了吗"))
    assert result.outcome_type == "clarification"
    assert result.next_interaction == "need_selection"
    assert len(result.payload["candidates"]) == 2


def test_context_bundle_exposes_only_customer_visible_result_refs_without_automatic_focus_binding():
    scope = _scope()
    order = artifact_entry(
        resource_type="order",
        resource_id="10002",
        label="机械键盘（订单 10002）",
        facts={"order_id": "10002"},
        scope=scope,
        turn=8,
        source="test",
        handle="artifact:order:10002",
    )
    result = result_entry(
        capability="orders.list",
        member_handles=[order["handle"]],
        labels=[order["label"]],
        scope=scope,
        turn=8,
        source_target={"mode": "all"},
        handle="result:delivered",
    )
    result["presentation_origin"] = {
        "origin": "customer_final_response", "source_turn": 8, "source_result_handle": "result:delivered"
    }

    class Transactions:
        def list_drafts_for_scope(self, **_kwargs):
            return []

    bundle = ContextBundleBuilder(transactions=Transactions()).build(_state(ledger=[order, result]))
    assert bundle["visible_result_refs"]
    assert bundle["visible_result_refs"][0]["result_ref"] == "result:delivered"
    assert bundle["semantic_owner"] == "llm"
    assert bundle["runtime_auto_select_target"] is False
    assert bundle["runtime_auto_switch_target"] is False
    assert "referent_candidates" not in bundle
    assert "current_focus" not in bundle
    assert "default_target" not in bundle
    assert "resolved_pronoun" not in bundle


def test_presentation_projects_one_primary_expression_without_duplicate_text():
    class MustNotRunForRuntimeNotice:
        def verify(self, **_kwargs):
            raise AssertionError("canonical runtime notice must not call the answer model")

    response = ResponseProjector(message_store=None).normalize(
        "thread-a",
        {
            "runtime_outcome": outcome(
                "unsupported_cardinality",
                customer_safe_summary="当前一次只能处理一笔订单。",
                next_interaction="need_selection",
            ).as_dict(),
            "answer_alignment_verifier": MustNotRunForRuntimeNotice(),
            "sources": [],
        },
    )
    assert response.presentation_mode == "notice"
    assert response.answer == "当前一次只能处理一笔订单。"
    assert response.blocks == []


def test_contextual_multi_target_request_is_rejected_before_preview_or_draft(monkeypatch):
    """The chat action entry cannot promise a multi-target write then discover it later."""
    from agent_modules.ecommerce.shared import prepare_actions as ecommerce_execution

    target_info = {
        "member_handles": ["artifact:order:10002", "artifact:order:10004"],
        "members": [],
        "entries": [],
        "mode": "collection",
        "target": {"mode": "collection", "left_handle": "view:delivered"},
    }
    monkeypatch.setattr(ecommerce_execution, "_target_members", lambda *_args, **_kwargs: (target_info, None))
    monkeypatch.setattr(
        ecommerce_execution,
        "_prepare_order_offer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("preview/draft must not run")),
    )
    result = ecommerce_execution._prepare_ecommerce_operation(
        _state(text="这两个都退", turn=9),
        {
            "target": target_info["target"],
            "reference_span": "这两个",
            "action_span": "都退",
        },
        action_id="create_refund",
        tool_name="prepare_refund",
    )
    assert result["ok"] is False
    assert result["code"] == "UNSUPPORTED_TARGET_CARDINALITY"
    runtime = result["runtime_outcome"]
    assert runtime["outcome_type"] == "unsupported_cardinality"
    assert runtime["effects"] == "none"
    assert result.get("ledger_entries") == []


def test_chat_reason_is_suggested_but_not_persisted_transaction_input():
    from agent_modules.ecommerce.shared import prepare_actions as ecommerce_execution

    plugin = get_runtime_registry().operations.get("create_refund")
    values, suggestions, error = ecommerce_execution._contextual_inputs_for_plugin(
        plugin,
        _state(text="质量不好，不想要了", turn=9),
        {"reason_span": "质量不好，不想要了"},
    )
    assert error is None
    assert values == {}
    assert suggestions["reason"]["value"] == "质量不好，不想要了"


def test_repository_unavailable_is_a_system_outcome_not_empty_history():
    from agent_core.transaction.availability import check_transaction_repository_available
    from agent_core.storage.repositories.base import TransactionScope

    class BrokenTransactions:
        def list_drafts_for_scope(self, **_kwargs):
            raise RuntimeError("database down")

    result = check_transaction_repository_available(
        BrokenTransactions(),
        scope=TransactionScope(tenant_id="tenant-a", user_id="u001", thread_id="thread-a"),
        correlation_id="corr-a",
        outcome_factory=outcome,
    )
    assert result is not None
    assert result.outcome_type == "system_unavailable"
    assert result.effects == "none"
    assert "未创建或提交" in result.customer_safe_summary


def test_malformed_runtime_outcome_cannot_be_rendered_as_success():
    response = ResponseProjector(message_store=None).normalize(
        "thread-a",
        {
            "runtime_outcome": {"outcome_type": "made_up", "effects": "committed"},
            "sources": [],
        },
    )
    assert response.presentation_mode == "notice"
    assert response.blocks == []
    assert response.answer and "未确认创建或提交" in response.answer


def test_current_contract_has_no_retired_transaction_runtime_terms():
    root = agent_root(__file__)
    current_runtime = [
        root / "src" / "agent_core" / "lifecycle",
        root / "src" / "agent_core" / "context",
        root / "src" / "agent_core" / "transaction" / "gateway_runtime.py",
        root / "src" / "agent_core" / "transaction" / "interaction_runtime.py",
        root / "src" / "agent_core" / "transaction" / "commit_runtime.py",
    ]
    forbidden = ("_offer_public", "_execute_offer_call", "provide_offer_input", "ISSUE_REASON_CODE_BY_LABEL")
    for location in current_runtime:
        paths = list(location.rglob("*.py")) if location.is_dir() else [location]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            assert not any(term in text for term in forbidden), path
    assert "pending_offer_handle" not in (root / "src" / "agent_core" / "transaction" / "gateway_runtime.py").read_text(encoding="utf-8")

