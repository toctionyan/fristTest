from __future__ import annotations

from tests.support.paths import agent_root

from copy import deepcopy
from pathlib import Path

import pytest


def _root() -> Path:
    return agent_root(__file__)


def test_customer_catalog_checks_draft_gateway_and_commit_lifecycle_surfaces():
    from agent_core.transaction.authority import registered_action_policy_ids
    from agent_core.presentation.actions import registered_action_ids, validate_catalog_integrity
    from agent_core.lifecycle.nodes import COMMITTABLE_TRANSACTION_ACTION_IDS
    from agent_core.composition import get_runtime_registry

    registry = get_runtime_registry()
    validate_catalog_integrity(
        action_ids=registry.preparable_action_ids(),
        gateway_policy_ids=registered_action_policy_ids(),
        commit_dispatcher_ids=COMMITTABLE_TRANSACTION_ACTION_IDS,
    )
    # Read the installed registry dynamically; do not restore a global action list.
    customer_actions = registered_action_ids()
    assert customer_actions <= registered_action_policy_ids()
    assert customer_actions <= set(get_runtime_registry().preparable_action_ids())
    assert customer_actions <= set(COMMITTABLE_TRANSACTION_ACTION_IDS)


def test_each_committable_action_commits_through_business_adapter(monkeypatch):
    from agent_core.lifecycle import nodes
    from agent_modules.ecommerce.business_port import EcommerceHttpBusinessPort

    calls: list[tuple[str, dict]] = []

    class FakeBusinessClient:
        def execute_operation_command(self, *, command, idempotency_key=None):
            action_id = str((command or {}).get("action_id") or "")
            calls.append((action_id, {"command": command, "idempotency_key": idempotency_key}))
            return {"success": True, "data": {"action_id": action_id, "receipt_id": f"r:{action_id}"}}


    from agent_core.runtime.outcomes import outcome
    from agent_core.transaction.deps import TransactionExecutionDeps

    transaction_execution = TransactionExecutionDeps(
        business_port=EcommerceHttpBusinessPort(FakeBusinessClient()),
        outcome_factory=outcome,
    )
    state = {"current_tenant_id": "tenant-a", "current_user_id": "u001", "current_thread_id": "thread-a"}
    target = {"resource_type": "order", "resource_id": "10003"}
    for action in sorted(nodes.COMMITTABLE_TRANSACTION_ACTION_IDS):
        offer = {
            "handle": f"h:{action}",
            "action_id": action,
            "input_values": {"expected_version": 1, "reason": "测试原因", "service_type": "return", "invoice_title": "张三"},
            "preview": {"snapshot": {"version": 1}},
        }
        envelope = nodes._build_business_command_envelope(state, offer, target)
        result = nodes._execute_business_command_envelope(state, envelope, idempotency_key=f"idem:{action}", transaction_execution=transaction_execution)
        assert result["success"] is True
    assert {name for name, _ in calls} == set(nodes.COMMITTABLE_TRANSACTION_ACTION_IDS)


def test_direct_freeform_turn_supersedes_live_form_before_any_stale_control_can_mutate():
    """The server remains safe even if a non-web client skips the UI guard."""
    from agent_core.ledger import artifact_entry, find_handle, offer_entry, scope_for_state
    from agent_core.lifecycle.nodes import prepare_agent_loop_turn_node

    scope = {"user_id": "u001", "thread_id": "thread-a", "tenant_id": "tenant-a"}
    order = artifact_entry(
        resource_type="order", resource_id="10003", label="无线鼠标（订单 10003）",
        facts={}, scope=scope, turn=1, source="test", handle="h:order",
    )
    offer = offer_entry(
        action_id="cancel_order", operation="CANCEL_ORDER", target_handle=order["handle"],
        input_values={}, preview={"message": "请补充取消原因。"}, scope=scope,
        turn=1, label="取消订单", handle="h:cancel",
    )
    from agent_core.transaction import transition_draft
    offer = transition_draft(offer, "NEEDS_INPUT")
    offer.update({"input_form_id": "form-1", "input_form_version": 1})
    state = {
        "current_user_id": "u001", "current_thread_id": "thread-a", "current_tenant_id": "tenant-a",
        "turn_index": 2, "active_draft_id": offer["handle"],
        "artifact_ledger": [order, offer], "task_board": [],
        "agent_loop_step": 6, "answer_protocol_retry": 1,
        "goal_declaration_retry": 2, "clarification_scope_retry": 1,
    }

    prepared = prepare_agent_loop_turn_node(state)
    latest = find_handle(prepared["artifact_ledger"], offer["handle"], scope=scope_for_state(state), allowed_kinds={"offer"}, active_only=False)

    assert prepared["active_draft_id"] == offer["handle"]
    assert latest is not None
    assert latest["draft_state"] == "NEEDS_INPUT"
    assert "status" not in latest and "action_state" not in latest
    assert "superseded_reason" not in latest
    assert prepared["agent_loop_step"] == 0
    assert prepared["answer_protocol_retry"] == 0
    assert prepared["goal_declaration_retry"] == 0
    assert prepared["clarification_scope_retry"] == 0








def _pending_form_state():
    from agent_core.ledger import artifact_entry, offer_entry

    scope = {"user_id": "u001", "thread_id": "thread-v166-recovery", "tenant_id": "tenant-a"}
    target = artifact_entry(
        resource_type="order",
        resource_id="10003",
        label="无线鼠标（订单 10003）",
        facts={"status": "待发货"},
        scope=scope,
        turn=4,
        source="test",
        handle="h:order-v166",
    )
    offer = offer_entry(
        action_id="cancel_order",
        operation="CANCEL_ORDER",
        target_handle=target["handle"],
        input_values={},
        preview={
            "message": "请补充取消原因。",
            "required_inputs": [{"name": "reason", "label": "取消原因", "input_kind": "text", "required": True}],
        },
        scope=scope,
        turn=4,
        label="取消订单",
        handle="h:offer-v166",
    )
    from agent_core.transaction import transition_draft
    from agent_core.transaction.capability_snapshot import attach_snapshot
    offer = attach_snapshot(offer)
    offer = transition_draft(offer, "NEEDS_INPUT")
    offer.update(
        {
            "input_form_id": "form-v166",
            "input_form_version": 1,
            "input_step": 1,
            "interaction_revision": 4,
        }
    )
    return {
        "current_user_id": "u001",
        "current_thread_id": "thread-v166-recovery",
        "current_tenant_id": "tenant-a",
        "turn_index": 4,
        "active_draft_id": offer["handle"],
        "artifact_ledger": [target, offer],
        "task_board": [],
        "summary": "取消订单",
    }


def test_single_action_continuation_on_pending_form_is_runtime_aligned_without_model_loop():
    from agent_core.composition import get_runtime_registry
    from agent_core.lifecycle.goal_planning import validate_goal_declaration

    state = {
        **_pending_form_state(),
        "current_user_input": "原因是不喜欢。",
    }
    result, declared = validate_goal_declaration(
        state=state,
        args={
            "summary": "补充退款原因",
            "goals": [{
                "goal_id": "g1", "description": "把不喜欢作为当前草稿原因",
                "evidence_span": "原因是不喜欢",
                "requested_effect": {"domain": "order", "operation": "cancel", "object_type": "order"},
                "goal_type": "action",
                "expected_result_cardinality": "single", "required": True,
                "depends_on": [],
            }],
        },
        capability_registry=get_runtime_registry().capabilities,
    )

    assert result["ok"] is True
    assert declared is not None
    assert declared["alignment_proof"]["reason_code"] == "local_candidate_declaration_only"
    assert declared["_frozen_semantic_contract"]["goals"][0]["requested_effect"] == {
        "domain": "order",
        "operation": "cancel",
        "object_type": "order",
        "raw_description": "把不喜欢作为当前草稿原因",
    }


def test_pending_action_goal_re_presents_card_without_second_model_call():
    from agent_core.lifecycle.dialogue_runtime import agent_loop_node
    from tests.support.runtime_support import runtime_deps

    class MustNotResolveModel:
        def __call__(self):
            raise AssertionError("pending action goal must not spend another model call")

    from agent_core.lifecycle.semantic_contract import freeze_semantic_contract, goal_declaration_projection_from_contract

    contract = freeze_semantic_contract(
        turn=5,
        user_text="停下来，不要提交。",
        summary="停止当前办理",
        goals=[{
            "goal_id": "stop",
            "description": "停止当前办理",
            "evidence_span": "停下来，不要提交",
            "requested_effect": {"domain": "order", "operation": "cancel", "object_type": "order"},
            "goal_type": "action",
            "expected_result_cardinality": "none",
            "required": True,
            "depends_on": [],
        }],
        alignment_proof={"verdict": "exact", "independent": True},
    )
    state = {
        **_pending_form_state(),
        "current_user_input": "停下来，不要提交。",
        "turn_index": 5,
        "frozen_semantic_contract": contract,
    }
    deps = runtime_deps()

    result = agent_loop_node(
        state,
        context_bundle_builder=deps.context_bundle_builder,
        capability_registry=deps.capability_registry,
        model_resolver=MustNotResolveModel(),
    )

    assert result["phase"] == "offer_confirmation"
    assert result["status"] == "PendingInteractionActionRedirect"
    assert result["response_contract"]["interaction"]["interaction_id"] == state["active_draft_id"]
    assert "不会取消草稿" in result["runtime_outcome"]["customer_safe_summary"]


def test_answer_release_rejects_pending_action_claim_without_structured_card():
    from agent_core.composition import get_runtime_registry
    from agent_core.runtime.answer_release_alignment import _deterministic_verdict

    get_runtime_registry()  # Explicit Composition Root initialization for capability snapshots.

    from agent_core.lifecycle.semantic_contract import freeze_semantic_contract, goal_declaration_projection_from_contract

    contract = freeze_semantic_contract(
        turn=5,
        user_text="停止当前办理",
        summary="停止当前办理",
        goals=[{
            "goal_id": "stop",
            "description": "停止当前办理",
            "evidence_span": "停止当前办理",
            "requested_effect": {"domain": "order", "operation": "cancel", "object_type": "order"},
            "goal_type": "action",
            "expected_result_cardinality": "none",
            "required": True,
            "depends_on": [],
        }],
        alignment_proof={"verdict": "exact", "independent": True},
    )
    state = {
        **_pending_form_state(),
        "frozen_semantic_contract": contract,
        "response_contract": None,
    }
    from tests.support.test_semantic_state import install_test_plan_authority
    install_test_plan_authority(
        state,
        goals=[{"goal_id": "stop", "required": True}],
        steps=[{
            "step_id": "step:stop",
            "effect_id": "effect:stop",
            "goal_ids": ["stop"],
            "kind": "action_draft",
            "verification": {"goal_effect_role": "completion"},
        }],
    )

    verdict = _deterministic_verdict(result=state, blocks=[])

    assert verdict.decision == "reject"
    assert verdict.reason_code == "pending_interaction_action_requires_structured_card"


class _ServiceSnapshot:
    def __init__(self, values):
        self.values = values


class _ServiceFailingInputGraph:
    def __init__(self, values, *, terminal_after_invoke=False):
        self.values = values
        self.terminal_after_invoke = terminal_after_invoke

    def get_state(self, _config):
        return _ServiceSnapshot(deepcopy(self.values))

    def invoke(self, _command, *, config):
        if self.terminal_after_invoke:
            state = deepcopy(self.values)
            state["active_draft_id"] = None
            for row in state["artifact_ledger"]:
                if row.get("handle") == "h:offer-v166":
                    from agent_core.transaction import transition_draft
                    state["artifact_ledger"][state["artifact_ledger"].index(row)] = transition_draft(row, "COMMITTED")
            state["action_gateway_result"] = {
                "offer_handle": "h:offer-v166",
                "decision": "committed",
                "message": "订单已取消。",
            }
            state["offer_execution_result"] = {"success": True}
            state["current_final_answer"] = "已完成取消订单。"
            state["response_contract"] = None
            from agent_core.runtime.outcomes import outcome
            state["runtime_outcome"] = outcome(
                "commit", effects="committed", safe_to_continue=True,
                customer_safe_summary="已完成取消订单。", next_interaction="show_status",
            ).as_dict()
            self.values = state
        raise RuntimeError("response path interrupted after graph invocation")


class _ServiceLeaseStore:
    def acquire(self, _key, *, owner=None, ttl_seconds=300):
        return {"acquired": True, "owner": owner, "fencing_token": 1}

    def renew(self, _key, *, owner, fencing_token, ttl_seconds=300):
        return {"renewed": True}

    def validate(self, _key, *, owner, fencing_token):
        return True

    def release(self, _key, owner=None, fencing_token=None):
        return None


class _ServiceThreadStore:
    def assert_thread_owner(self, *_args, **_kwargs):
        return None

    def upsert_thread(self, *_args, **_kwargs):
        return None


class _ServiceMessageStore:
    def __init__(self):
        self.rows = []

    def add_message(self, *args, **kwargs):
        self.rows.append((args, kwargs))


class _ServiceTraceLogger:
    def log_event(self, *_args, **_kwargs):
        return "trace"


class _ServiceTransactionRepo:
    def list_drafts_for_scope(self, **_kwargs):
        return []


def _v166_service_for_input_recovery(graph):
    from app.services.agent_service import AgentService

    service = AgentService.__new__(AgentService)
    service.graph = graph
    service.agent_runtime_error = None
    service.thread_store = _ServiceThreadStore()
    service.message_store = _ServiceMessageStore()
    service.trace_logger = _ServiceTraceLogger()
    service.conversation_lock_store = _ServiceLeaseStore()
    service.transactions = _ServiceTransactionRepo()
    service._config_for_request = lambda *_args, **_kwargs: {"configurable": {"thread_id": "test"}}
    # This is a focused interaction-recovery test, not a checkpoint migration
    # test.  Keep its fake graph state authoritative so repository health
    # probing does not force the real hydrator to interpret an intentionally
    # partial test double.
    service.checkpoint_hydrator = type(
        "_Hydrator",
        (),
        {"values": staticmethod(lambda graph, **_kwargs: dict(graph.get_state({}).values))},
    )()
    return service


def _v166_input_request():
    from app.schemas.chat_schema import ActionInputRequest

    return ActionInputRequest(
        thread_id="thread-v166-recovery",
        user_id="u001",
        role="customer",
        tenant_id="tenant-a",
        interaction_mode="submit_input",
        offer_handle="h:offer-v166",
        action_id="cancel_order",
        target_handle="h:order-v166",
        form_id="form-v166",
        form_version=1,
        form_step=1,
        conversation_revision=4,
        client_request_id="request-v166-recovery",
        input_values={"reason": "测试原因"},
    )


def test_interaction_input_exception_recovers_live_authoritative_card():
    """A graph exception must not make the browser guess that the form expired."""
    graph = _ServiceFailingInputGraph(_pending_form_state())
    service = _v166_service_for_input_recovery(graph)

    response = service.submit_action_input(_v166_input_request())

    assert response.type == "interaction_required"
    assert response.interaction is not None
    assert response.interaction["lifecycle"] == "collecting_input"
    assert response.interaction_update is None


def test_interaction_exception_recovers_terminal_commit_instead_of_uncertain_text():
    """If a commit reached the checkpoint before response failure, return its terminal update."""
    graph = _ServiceFailingInputGraph(_pending_form_state(), terminal_after_invoke=True)
    service = _v166_service_for_input_recovery(graph)

    response = service.submit_action_input(_v166_input_request())

    assert response.type == "answer"
    assert response.interaction_update is not None
    assert response.interaction_update["lifecycle"] == "committed"
    # The terminal outcome is persisted once for history replay, whereas a live
    # form recovery intentionally does not append a duplicate history record.
    assert len(service.message_store.rows) == 1




def test_thread_store_honors_tenant_scope_even_without_user_filter(tmp_path, monkeypatch):
    from agent_core.persistence.thread_store import ThreadStore

    monkeypatch.setenv("APP_ENV", "production")
    store = ThreadStore(tmp_path / "threads.db")
    store.claim_or_validate_thread("tenant-a-thread", "u001", "tenant-a")
    store.claim_or_validate_thread("tenant-b-thread", "u002", "tenant-b")

    rows = store.list_threads(tenant_id="tenant-a", limit=20)

    assert [row["thread_id"] for row in rows] == ["tenant-a-thread"]
    store.close()




