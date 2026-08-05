from __future__ import annotations

from tests.support.test_semantic_state import install_test_semantic_contract
import sys
import types

import pytest

from app.services.lifecycle_command_runner import LifecycleCommandRunner
from agent_core.lifecycle.budget import compute_loop_budget
from agent_core.model_calls import (
    ModelCallBudgetExceeded,
    classify_model_failure,
    invoke_model,
    is_environmental_model_failure,
    is_environmental_model_failure_category,
    model_call_scope,
)


class _FakeModel:
    model_name = "test-model"

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, payload):
        self.calls += 1
        return {"payload": payload, "call": self.calls}


class _UsageModel:
    model_name = "deepseek-test-model"

    def invoke(self, _payload):
        response = types.SimpleNamespace()
        response.response_metadata = {
            "token_usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 40,
                "total_tokens": 1040,
                "prompt_cache_hit_tokens": 750,
                "prompt_cache_miss_tokens": 250,
            }
        }
        response.usage_metadata = {
            "input_tokens": 1000,
            "output_tokens": 40,
            "total_tokens": 1040,
        }
        return response


class _FakeGraph:
    def __init__(self) -> None:
        self.updated: list[tuple[dict, dict, str]] = []
        self.invoked: list[tuple[object, dict]] = []

    def update_state(self, config, update, *, as_node):
        self.updated.append((config, update, as_node))

    def invoke(self, value, *, config):
        self.invoked.append((value, config))
        return {"phase": "final", "status": "FormalRouteResumed"}


class _ProviderStatusError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_model_failure_classification_separates_environment_from_code_defects() -> None:
    balance = _ProviderStatusError(402, "provider balance unavailable")
    assert classify_model_failure(balance) == "http_402"
    assert is_environmental_model_failure(balance) is True
    assert is_environmental_model_failure_category("http_429") is True
    assert is_environmental_model_failure_category("http_400") is False
    assert classify_model_failure(RuntimeError("request validation failed")) == "validation"


def test_model_call_budget_is_shared_by_nested_scope_and_records_latency() -> None:
    model = _FakeModel()
    with model_call_scope(max_calls=2, scope="test") as outer:
        response1, trace1 = invoke_model(purpose="agent_loop", model=model, payload={"a": 1})
        with model_call_scope(max_calls=99, scope="nested") as inner:
            response2, trace2 = invoke_model(purpose="semantic_verifier", model=model, payload={"b": 2})
            assert inner is outer
        with pytest.raises(ModelCallBudgetExceeded):
            invoke_model(purpose="rag_answer", model=model, payload={"c": 3})
    assert response1["call"] == 1 and response2["call"] == 2
    assert outer.used_calls == 2
    assert trace1["purpose"] == "agent_loop"
    assert trace2["purpose"] == "semantic_verifier"
    assert isinstance(trace1["latency_ms"], float)


def test_model_call_trace_records_deepseek_cache_usage() -> None:
    with model_call_scope(max_calls=1, scope="usage-test") as ledger:
        _, trace = invoke_model(purpose="agent_loop", model=_UsageModel(), payload={"messages": []})

    assert trace["prompt_tokens"] == 1000
    assert trace["completion_tokens"] == 40
    assert trace["total_tokens"] == 1040
    assert trace["prompt_cache_hit_tokens"] == 750
    assert trace["prompt_cache_miss_tokens"] == 250
    assert trace["prompt_cache_hit_rate"] == 0.75
    assert ledger.summary()["token_usage"] == {
        "prompt_tokens": 1000,
        "completion_tokens": 40,
        "total_tokens": 1040,
        "prompt_cache_hit_tokens": 750,
        "prompt_cache_miss_tokens": 250,
        "prompt_cache_hit_rate": 0.75,
        "calls_with_usage": 1,
    }


def test_planner_exhaustion_cannot_consume_reserved_verifier_capacity(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_CALL_MAX_PER_TURN", "4")
    monkeypatch.setenv("MODEL_CALL_MAX_PLANNER_PER_TURN", "1")
    monkeypatch.setenv("MODEL_CALL_MAX_VERIFIER_PER_TURN", "2")
    monkeypatch.setenv("MODEL_CALL_MAX_SUPPORT_PER_TURN", "1")
    model = _FakeModel()

    with model_call_scope(scope="lane-isolation") as ledger:
        invoke_model(purpose="agent_loop", model=model, payload={"step": 1})
        with pytest.raises(ModelCallBudgetExceeded, match="planner lane exhausted"):
            invoke_model(purpose="agent_loop", model=model, payload={"step": 2})
        invoke_model(purpose="semantic_capability_verifier", model=model, payload={"check": 1})
        invoke_model(purpose="answer_release_alignment", model=model, payload={"check": 2})
        invoke_model(purpose="rag_query_rewriter", model=model, payload={"support": 1})

    assert ledger.used_calls == 4
    assert ledger.used_calls_by_lane == {"planner": 1, "verifier": 2, "support": 1}
    assert ledger.summary()["remaining_calls_by_lane"] == {"planner": 0, "verifier": 0, "support": 0}


def test_answer_release_verifier_repairs_one_non_json_response(monkeypatch) -> None:
    import agent_core.config as config_module
    import agent_core.runtime.answer_release_alignment as alignment_module

    class Response:
        def __init__(self, content: str) -> None:
            self.content = content

    responses = iter([
        Response("I think the answer is aligned."),
        Response('{"decision":"pass","reason_code":"scope_preserved"}'),
    ])
    calls: list[dict] = []

    def fake_invoke_model(*, purpose, model, payload):
        calls.append({"purpose": purpose, "payload": payload})
        return next(responses), {"purpose": purpose}

    monkeypatch.setattr(config_module, "get_model", lambda: object())
    monkeypatch.setattr(alignment_module, "invoke_model", fake_invoke_model)
    monkeypatch.setattr(
        alignment_module,
        "structured_verifier_messages",
        lambda *, instruction, payload, format_repair=None, **_kwargs: [
            types.SimpleNamespace(content=instruction),
            types.SimpleNamespace(content=str({"payload": payload, "FORMAT_REPAIR": format_repair}) if format_repair else str({"payload": payload})),
        ],
    )

    verdict = alignment_module.ModelAnswerAlignmentVerifier().verify(
        user_text="其中最贵的是哪个？",
        match_proofs=[],
        runtime_evidence=[],
        answer="唯一订单 10001 就是其中最贵的。",
        blocks=[],
    )

    assert verdict.decision == "pass"
    assert verdict.reason_code == "scope_preserved"
    assert len(calls) == 2
    assert calls[0]["payload"][0].content == calls[1]["payload"][0].content
    assert "FORMAT_REPAIR" not in calls[0]["payload"][1].content
    assert "FORMAT_REPAIR" in calls[1]["payload"][1].content


def test_answer_release_verifier_still_fails_closed_after_two_non_json_responses(monkeypatch) -> None:
    import agent_core.config as config_module
    import agent_core.runtime.answer_release_alignment as alignment_module

    class Response:
        content = "not-json"

    monkeypatch.setattr(config_module, "get_model", lambda: object())
    monkeypatch.setattr(
        alignment_module,
        "invoke_model",
        lambda **_kwargs: (Response(), {"purpose": "answer_release_alignment"}),
    )
    monkeypatch.setattr(
        alignment_module,
        "structured_verifier_messages",
        lambda *, instruction, payload, format_repair=None, **_kwargs: [
            types.SimpleNamespace(content=instruction),
            types.SimpleNamespace(content=str({"payload": payload, "FORMAT_REPAIR": format_repair}) if format_repair else str({"payload": payload})),
        ],
    )

    verdict = alignment_module.ModelAnswerAlignmentVerifier().verify(
        user_text="查询订单",
        match_proofs=[],
        runtime_evidence=[],
        answer="订单结果",
        blocks=[],
    )

    assert verdict.decision == "reject"
    assert verdict.reason_code == "alignment_verifier_non_json"
    assert verdict.details == {"attempts": 2}


def test_answer_release_projects_scoped_visible_singleton_as_runtime_evidence() -> None:
    from agent_core.context.visible_result_refs import mark_visible_result_refs
    from agent_core.ledger import artifact_entry, result_entry
    from agent_core.runtime.answer_release_alignment import _runtime_evidence

    scope = {"tenant_id": "default", "user_id": "u001", "thread_id": "thread-visible-singleton"}
    order = artifact_entry(
        resource_type="order",
        resource_id="10001",
        label="蓝牙耳机（订单 10001）",
        facts={"order_id": "10001", "amount": 199.0},
        scope=scope,
        turn=3,
        source="test",
        handle="h_order:10001",
    )
    visible = result_entry(
        capability="ecommerce.orders.list",
        member_handles=[order["handle"]],
        labels=[order["label"]],
        scope=scope,
        turn=3,
        source_target={"mode": "set_operation", "operator": "filter"},
        handle="h_result:in-transit",
    )
    state = {
        "current_tenant_id": scope["tenant_id"],
        "current_user_id": scope["user_id"],
        "current_thread_id": scope["thread_id"],
        "turn_index": 4,
        "artifact_ledger": [order, visible],
        "runtime_outcome": {
            "outcome_type": "narrative",
            "effects": "none",
            "safe_to_continue": True,
            "customer_safe_summary": "唯一成员就是结果。",
            "next_interaction": "none",
            "evidence_handles": [visible["handle"]],
        },
        "tool_trace": [],
    }
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"],
        state={**state, "turn_index": 3},
        evidence_handles=[visible["handle"]],
    )

    rows = _runtime_evidence(state)

    assert rows[0] == {
        "evidence_kind": "released_result_ref",
        "result_ref": "h_result:in-transit",
        "source_result_handle": "h_result:in-transit",
        "shape": "collection",
        "member_count": 1,
        "member_handles": ["h_order:10001"],
        "member_labels": ["蓝牙耳机（订单 10001）"],
        "source_turn": 3,
        "presentation_origin": "customer_final_response",
        "scope_verified": True,
        "customer_visible": True,
    }
    assert rows[1] == {
        "evidence_kind": "released_ledger_evidence",
        "result_ref": "h_result:in-transit",
        "scope_verified": True,
        "customer_visible": True,
        "detail": {
            "kind": "result",
            "label": "ecommerce.orders.list结果（1项）",
            "status": "active",
            "member_handles": ["h_order:10001"],
            "labels": ["蓝牙耳机（订单 10001）"],
        },
    }
    assert rows[2] == {
        "evidence_kind": "conversation_scope_candidate",
        "result_ref": "h_result:in-transit",
        "source_turn": 3,
        "shape": "collection",
        "member_count": 1,
        "member_labels": ["蓝牙耳机（订单 10001）"],
        "discourse_recency_rank": 1,
        "is_latest_visible_turn": True,
        "scope_verified": True,
        "customer_visible": True,
    }


def test_answer_release_projects_released_history_as_history_only_evidence() -> None:
    from agent_core.runtime.answer_release_alignment import _runtime_evidence

    state = {
        "current_tenant_id": "default",
        "current_user_id": "u001",
        "current_thread_id": "thread-history-evidence",
        "artifact_ledger": [],
        "runtime_outcome": {"evidence_handles": []},
        "tool_trace": [],
        "conversation_event_log": [{
            "turn_index": 3,
            "user_text": "查键盘退款资格",
            "answer": "机械键盘具备退款资格。",
            "tool_trace": [{"name": "evaluate_refund_eligibility"}],
            "answer_evidence_handles": ["h_eligibility:keyboard"],
        }],
    }

    rows = _runtime_evidence(state)

    assert rows == [{
        "evidence_kind": "released_conversation_event",
        "turn": 3,
        "user_summary": "查键盘退款资格",
        "answer_summary": "机械键盘具备退款资格。",
        "tool_names": ["evaluate_refund_eligibility"],
        "result_handles": ["h_eligibility:keyboard"],
        "historical_only": True,
        "scope_verified": True,
        "customer_visible": True,
    }]


def test_answer_release_skips_second_model_judge_for_exact_current_empty_query(monkeypatch) -> None:
    from agent_core.context.visible_result_refs import mark_visible_result_refs
    from agent_core.ledger import result_entry
    from agent_core.runtime.answer_release_alignment import evaluate_answer_release

    scope = {"tenant_id": "default", "user_id": "u001", "thread_id": "thread-empty-exact"}
    result_ref = result_entry(
        capability="ecommerce.after_sales.list",
        member_handles=[],
        labels=[],
        scope=scope,
        turn=2,
        source_target={"mode": "entity_match", "target": {"mode": "entity_match", "attribute_span": "机械键盘"}},
        handle="h_result:empty-keyboard-after-sales",
    )
    state = {
        "current_tenant_id": scope["tenant_id"],
        "current_user_id": scope["user_id"],
        "current_thread_id": scope["thread_id"],
        "current_user_input": "只看机械键盘相关的。",
        "turn_index": 2,
        "artifact_ledger": [result_ref],
        "runtime_outcome": {
            "outcome_type": "query",
            "effects": "none",
            "safe_to_continue": False,
            "customer_safe_summary": "已完成已验证查询，共 0 项。",
            "next_interaction": "none",
            "evidence_handles": [result_ref["handle"]],
            "payload": {"count": 0},
        },
        "tool_trace": [{
            "name": "list_after_sales_requests",
            "classification": "observation",
            "effect_id": "effect:empty",
            "match_proof": {
                "effect_id": "effect:empty",
                "candidate_tool": "list_after_sales_requests",
                "exact_match": True,
                "parameterization_complete": True,
                "visible_result_reference": {"complete": True},
                "explicit_member_scope": {"complete": True},
                "derived_collection_scope": {"complete": True},
                "semantic_verdict": {"verdict": "exact"},
                "constraint_errors": [],
                "scope": scope,
            },
            "result": {"ok": True, "data": {"count": 0, "result_handle": result_ref["handle"]}},
        }],
    }
    state["artifact_ledger"] = mark_visible_result_refs(
        state["artifact_ledger"], state=state, evidence_handles=[result_ref["handle"]],
    )

    monkeypatch.setattr(
        "agent_core.runtime.answer_release_alignment.ModelAnswerAlignmentVerifier.verify",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("model judge must be skipped")),
    )
    monkeypatch.setenv("ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE", "model")

    verdict = evaluate_answer_release(
        state=state,
        result=state,
        answer="机械键盘（订单10002）目前没有售后工单记录。",
        blocks=[],
    )

    assert verdict.decision == "pass"
    assert verdict.reason_code == "current_turn_exact_scope_proven"
    assert verdict.details["model_verifier_skipped"] is True


def test_answer_release_does_not_scope_judge_a_non_effecting_narrative(monkeypatch) -> None:
    from agent_core.runtime.answer_release_alignment import evaluate_answer_release

    state = {
        "current_user_input": "这两个问题有什么不同？",
        "runtime_outcome": {
            "outcome_type": "narrative",
            "effects": "none",
            "safe_to_continue": True,
            "customer_safe_summary": "一个问通用规则，一个问具体资格。",
            "next_interaction": "none",
            "evidence_handles": [],
        },
        "tool_trace": [],
    }
    install_test_semantic_contract(state, {
        "turn": 1,
        "user_text": state["current_user_input"],
        "goals": [{
            "goal_id": "g1",
            "description": "比较两个问题",
            "goal_type": "narrative",
            "evidence_span": "这两个问题有什么不同",
            "requested_effect": {"domain": "narrative", "operation": "compare", "object_type": "question"},
            "required": True,
            "depends_on": [],
        }],
    })
    monkeypatch.setenv("ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE", "model")
    monkeypatch.setattr(
        "agent_core.runtime.answer_release_alignment.ModelAnswerAlignmentVerifier.verify",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("model judge must be skipped")),
    )

    verdict = evaluate_answer_release(
        state=state,
        result=state,
        answer="一个问通用规则，一个问具体订单现在是否具备资格。",
        blocks=[],
    )

    assert verdict.decision == "pass"
    assert verdict.reason_code == "non_effecting_narrative_goal"


def test_role_budget_configuration_must_equal_total(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_CALL_MAX_PER_TURN", "8")
    monkeypatch.setenv("MODEL_CALL_MAX_PLANNER_PER_TURN", "8")
    monkeypatch.setenv("MODEL_CALL_MAX_VERIFIER_PER_TURN", "8")
    monkeypatch.setenv("MODEL_CALL_MAX_SUPPORT_PER_TURN", "2")

    with pytest.raises(RuntimeError, match="must equal the sum"):
        with model_call_scope(scope="invalid-budget"):
            pass


def test_loop_budget_uses_outcome_not_domain_tool_name() -> None:
    state = {
        "tool_trace": [
            {
                "name": "unrelated_future_domain_query",
                "classification": "observation",
                "result": {
                    "ok": True,
                    "data": {"result_handle": "r1"},
                    "runtime_outcome": {
                        "effects": "none",
                        "safe_to_continue": True,
                        "next_interaction": "none",
                    },
                    "execution_disposition": {"disposition": "continue"},
                },
            }
        ]
    }
    budget = compute_loop_budget(state)
    assert budget.terminal_only is True
    assert budget.reason == "sufficient_verified_observation"


def test_structured_command_facade_runs_public_wrapper_then_resumes_graph(monkeypatch) -> None:
    node_module = types.ModuleType("agent_core.lifecycle.nodes")
    node_module.action_gateway_node = lambda state: {"runtime_outcome": {"outcome_type": "authority_required"}}
    node_module.commit_action_node = lambda state: {"phase": "final"}
    node_module.reconcile_submission_node = lambda state: {"phase": "final"}
    monkeypatch.setitem(sys.modules, "agent_core.lifecycle.nodes", node_module)

    graph = _FakeGraph()
    runner = LifecycleCommandRunner(service=object())
    result = runner.advance_gateway(graph=graph, config={"configurable": {}}, state={"phase": "action_gateway"})

    assert graph.updated == [
        ({"configurable": {}}, {"runtime_outcome": {"outcome_type": "authority_required"}}, "action_gateway")
    ]
    assert graph.invoked == [(None, {"configurable": {}})]
    assert result["status"] == "FormalRouteResumed"
