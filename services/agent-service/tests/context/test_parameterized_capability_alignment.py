from __future__ import annotations

from copy import deepcopy

from app.services.response_projector import ResponseProjector
from agent_core.runtime.capability_gate import issue_execution_permit
from agent_core.composition import get_runtime_registry
from agent_modules.ecommerce import _shared_execution as ecommerce_execution
from agent_modules.ecommerce.shared import context as ecommerce_context


class _ExactSemanticVerifier:
    """Keep parameterization/dispatch unit tests independent of deployment profile.

    Protected release runs with the real semantic verifier enabled. These tests
    exercise deterministic argument binding and backend filtering, not model
    classification, so they provide an explicit exact semantic premise just as
    semantic rejection tests inject their own verifier premise.
    """

    def verify(self, **kwargs):
        user_text = str(kwargs.get("user_text") or "")
        args = kwargs.get("args") if isinstance(kwargs.get("args"), dict) else {}
        span = str(args.get("reference_span") or "").strip()
        if not span or span not in user_text:
            span = user_text
        return {
            "verdict": "exact",
            "evidence_span": span,
            "reason_code": "deterministic_parameterization_test_fixture",
            "source": "test",
            "independent": True,
        }


def _state(text: str = "哪些还在路上？") -> dict:
    return {
        "current_thread_id": "v184-thread",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_role": "customer",
        "current_user_input": text,
        "turn_index": 7,
        "artifact_ledger": [],
        "current_turn_plan": {"plan_id": "turn-plan:v184", "turn": 7, "effects": []},
        "semantic_capability_verifier": _ExactSemanticVerifier(),
    }


def _args(*, with_binding: bool = True) -> dict:
    args = {
        "target": {"mode": "all_orders"},
        "query": {"delivery_status": "运输中"},
        "expected_shape": "collection",
        "reference_span": "哪些还在路上",
    }
    if with_binding:
        args["constraint_bindings"] = [
            {
                "source_span": "在路上",
                "kind": "condition",
                "parameter_path": "query.delivery_status",
                "normalized_value": "运输中",
            }
        ]
    return args


class _FilteredBusiness:
    def __init__(self) -> None:
        self.rows = [
            {"order_id": "10003", "product_name": "无线鼠标", "status": "待发货", "amount": 99.0, "version": 3},
            {"order_id": "10001", "product_name": "蓝牙耳机", "status": "已发货", "amount": 199.0, "version": 4},
        ]
        self.query_calls: list[dict] = []
        self.per_order_calls = 0

    def query_resources(self, _actor, *, resource_type, query_spec):
        if resource_type == "order":
            assert query_spec.get("user_id") == "u001"
            return {"success": True, "data": deepcopy(self.rows)}
        if resource_type == "logistics":
            self.query_calls.append(deepcopy(query_spec))
            assert query_spec["filters"] == {"delivery_status": "运输中"}
            assert query_spec["scope"]["order_ids"] == ["10003", "10001"]
            return {
                "success": True,
                "data": [
                    {
                        "order_id": "10001",
                        "product_name": "蓝牙耳机",
                        "order_status": "已发货",
                        "amount": 199.0,
                        "delivery_status": "运输中",
                        "latest": "已到达 Phoenix 分拨中心",
                        "eta": "预计 2 天内送达",
                        "updated_at": "2026-07-05T10:00:00+00:00",
                    }
                ],
                "summary": {
                    "source_population_count": 2,
                    "matched_population_count": 1,
                    "applied_filters": {"delivery_status": "运输中"},
                },
            }
        raise AssertionError(f"unexpected resource type {resource_type}")

    def read_resource(self, _actor, *, resource_type, resource_id, query=None):
        if resource_type == "order":
            assert (query or {}).get("user_id") == "u001"
            return {"success": True, "data": deepcopy(next(row for row in self.rows if row["order_id"] == resource_id))}
        if resource_type == "logistics":
            self.per_order_calls += 1
            raise AssertionError("filtered query must not call per-order logistics fallback")
        raise AssertionError(f"unexpected resource type {resource_type}")



def test_parameterized_permit_rejects_missing_constraint_binding():
    decision = issue_execution_permit(
        state=_state(),
        tool_name="get_order_logistics",
        args=_args(with_binding=False),
        effect_id="turn-plan:v184:effect:1",
        capability_registry=get_runtime_registry().capabilities,
    )
    assert decision.permitted is False
    assert decision.rejection and decision.rejection["code"] == "CAPABILITY_PARAMETERIZATION_INCOMPLETE"
    assert "parameterized_query_missing_constraint_binding:query.delivery_status" in decision.match_proof["constraint_errors"]


def test_parameterized_permit_normalizes_unique_constraint_leaf_alias():
    args = _args()
    args["constraint_bindings"][0]["parameter_path"] = "delivery_status"

    decision = issue_execution_permit(
        state=_state(),
        tool_name="get_order_logistics",
        args=args,
        effect_id="turn-plan:v184:effect:normalized",
        capability_registry=get_runtime_registry().capabilities,
    )

    assert decision.permitted is True
    assert decision.normalized_arguments["constraint_bindings"][0]["parameter_path"] == "query.delivery_status"
    proof = decision.match_proof["argument_normalization"]
    assert proof["changed"] is True
    assert proof["transformations"] == [{
        "kind": "constraint_parameter_path",
        "from": "delivery_status",
        "to": "query.delivery_status",
        "reason": "unique_supplied_argument_leaf",
    }]


def test_filtered_logistics_uses_backend_parameter_and_presents_only_matches(monkeypatch):
    business = _FilteredBusiness()
    monkeypatch.setattr(ecommerce_context, "get_business_port", lambda: business)
    state = _state()
    args = _args()
    permit = issue_execution_permit(
        state=state,
        tool_name="get_order_logistics",
        args=args,
        effect_id="turn-plan:v184:effect:1",
        capability_registry=get_runtime_registry().capabilities,
    )
    assert permit.permitted is True
    assert permit.execution_permit and permit.execution_permit["arguments_digest"]
    result = get_runtime_registry().capabilities.dispatch_permitted(
        state,
        "get_order_logistics",
        args,
        execution_permit=permit.execution_permit,
        effect_id="turn-plan:v184:effect:1",
    )
    assert result["ok"] is True
    assert len(business.query_calls) == 1
    assert business.per_order_calls == 0
    payload = result["data"]
    assert payload["parameterization"]["required_backend_conditions"] == {"delivery_status": "运输中"}
    assert payload["parameterization"]["backend_applied_conditions"] == {"delivery_status": "运输中"}
    assert payload["parameterization"]["source_population_count"] == 2
    assert payload["parameterization"]["matched_population_count"] == 1
    assert payload["parameterization"]["presentation_population"] == "matched_members"
    assert [item["order"]["order_id"] for item in payload["items"]] == ["10001"]


def test_alignment_gate_refuses_broader_release_when_independent_verdict_rejects():
    from agent_core.runtime.outcomes import outcome

    class RejectingVerifier:
        def verify(self, **_kwargs):
            return {"decision": "reject", "reason_code": "decisive_condition_dropped"}

    result = {
        "current_user_input": "哪些还在路上？",
        "answer_alignment_verifier": RejectingVerifier(),
        "runtime_outcome": outcome(
            "query",
            customer_safe_summary="物流查询成功",
            payload={},
        ).as_dict(),
        "tool_trace": [],
    }
    response = ResponseProjector(message_store=None).normalize("v184-thread", result)
    assert response.presentation_mode == "notice"
    assert response.answer is None
    assert response.blocks[0]["type"] == "notice"
    assert result["answer_release_alignment"]["reason_code"] == "decisive_condition_dropped"


def test_alignment_ignores_a_rejected_candidate_after_runtime_verified_repair():
    from agent_core.runtime.answer_release_alignment import _deterministic_verdict
    from tests.support.test_semantic_state import (
        install_test_plan_authority,
        install_test_semantic_contract,
        requested_effect_for_tool,
    )

    failed_effect = "turn-plan:repair:effect:1"
    repaired_effect = "turn-plan:repair:effect:2"
    result = {
        "current_user_input": "查我的订单",
        "turn_index": 7,
        "turn_match_proofs": [
            {
                "effect_id": failed_effect,
                "candidate_tool": "list_orders",
                "parameterization_complete": False,
            },
            {
                "effect_id": repaired_effect,
                "candidate_tool": "list_orders",
                "parameterization_complete": True,
            },
        ],
    }
    install_test_semantic_contract(result, {
        "turn": 7,
        "user_text": result["current_user_input"],
        "goals": [{
            "goal_id": "g1",
            "description": "查我的订单",
            "evidence_span": "查我的订单",
            "requested_effect": requested_effect_for_tool("list_orders"),
            "required": True,
            "depends_on": [],
        }],
    })
    install_test_plan_authority(
        result,
        goals=[{"goal_id": "g1", "required": True}],
        steps=[
            {
                "effect_id": failed_effect,
                "tool_name": "list_orders",
                "goal_ids": ["g1"],
                "verification": {},
            },
            {
                "effect_id": repaired_effect,
                "tool_name": "list_orders",
                "goal_ids": ["g1"],
                "verification": {},
            },
        ],
    )
    from agent_core.lifecycle.plan_execution import begin_step_attempt, complete_step_attempt, project_grounded_execution_plan
    run, attempt = begin_step_attempt(
        definition=result["frozen_plan_definition"],
        plan_run=result["plan_run"],
        effect_id=repaired_effect,
        tool_name="list_orders",
        args={},
        execution_permit=None,
    )
    run, _outcome = complete_step_attempt(
        definition=result["frozen_plan_definition"],
        plan_run=run,
        attempt_id=attempt["attempt_id"],
        result={"ok": True, "code": "OK", "message": "repaired candidate succeeded"},
        step_status="SUCCEEDED",
        failure_type="NONE",
        verification={"verified_by_runtime": True},
        related_step_updates={
            failed_effect: {
                "status": "SKIPPED",
                "verification": {
                    "candidate_repaired": True,
                    "superseded_by_effect_id": repaired_effect,
                },
            }
        },
    )
    result["plan_run"] = run
    result["grounded_execution_plan"] = project_grounded_execution_plan(
        definition=result["frozen_plan_definition"],
        plan_run=run,
    )

    verdict = _deterministic_verdict(result=result, blocks=[])

    assert verdict.decision == "pass"
    assert verdict.reason_code == "deterministic_evidence_complete"


def test_thread_history_exposes_released_timeline_blocks_but_not_live_interaction_controls():
    from types import SimpleNamespace

    from app.api.product_api import list_thread_messages
    from agent_core.security.auth_provider import AuthenticatedActor

    class _Messages:
        def list_messages(self, _thread_id, limit=100):
            assert limit == 100
            return [
                {
                    "id": 1,
                    "role": "user",
                    "content": "给这个订单退款",
                    "message_type": "chat",
                    "presentation": [],
                    "interaction": None,
                    "created_at": "2026-07-06T10:00:00+00:00",
                },
                {
                    "id": 2,
                    "role": "assistant",
                    "content": "",
                    "message_type": "interaction_required",
                    "presentation": [
                        {
                            "type": "interaction_timeline",
                            "contract_id": "runtime.interaction_timeline@1",
                            "interaction_id": "draft:1",
                            "summary": "申请退款需要补充退款原因。",
                        }
                    ],
                    # Deliberately include a private-looking control marker: the
                    # public history endpoint must never return this snapshot.
                    "interaction": {"control": {"confirmation_id": "private-token"}},
                    "created_at": "2026-07-06T10:00:01+00:00",
                },
            ]

    class _Service:
        message_store = _Messages()

        def _assert_thread_owner(self, thread_id, user_id, tenant_id):
            assert (thread_id, user_id, tenant_id) == ("thread-history", "u001", "tenant-a")

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent_service=_Service())))
    actor = AuthenticatedActor(user_id="u001", role="customer", tenant_id="tenant-a")

    payload = list_thread_messages("thread-history", request, actor, limit=100)

    assert payload["thread_id"] == "thread-history"
    assert payload["items"][0]["role"] == "user"
    assert payload["items"][1]["presentation_mode"] == "structured"
    assert payload["items"][1]["blocks"][0]["contract_id"] == "runtime.interaction_timeline@1"
    assert "interaction" not in payload["items"][1]
    assert "private-token" not in str(payload)


def test_alignment_rewrite_request_never_publishes_the_unrewritten_response():
    from agent_core.runtime.outcomes import outcome

    class RewriteVerifier:
        def verify(self, **_kwargs):
            return {"decision": "rewrite_from_evidence", "reason_code": "wording_needs_evidence_bound_rewrite"}

    result = {
        "current_user_input": "哪些还在路上？",
        "answer_alignment_verifier": RewriteVerifier(),
        "runtime_outcome": outcome(
            "query",
            customer_safe_summary="物流查询成功",
            payload={},
        ).as_dict(),
        "tool_trace": [],
    }
    response = ResponseProjector(message_store=None).normalize("v184-thread", result)
    assert response.presentation_mode == "notice"
    assert response.answer is None
    assert response.blocks[0]["type"] == "notice"
    assert "重新整理回答" in response.blocks[0]["content"]
