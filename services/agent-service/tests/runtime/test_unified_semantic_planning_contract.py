from __future__ import annotations

from agent_core.context.projection import partition_tool_trace
from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.lifecycle.goal_blockers import active_goal_blockers, apply_blocker_resolutions
from agent_core.lifecycle.goal_planning import validate_goal_declaration
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract, semantic_goals


class _ExactGoalVerifier:
    def verify(self, *, user_text, goals, known_tools):
        return {
            "verdict": "exact",
            "evidence_spans": [goal["evidence_span"] for goal in goals],
            "missing_spans": [],
            "reason_code": "test_exact",
            "source": "test",
            "independent": True,
            "details": {},
        }


def _empty_registry() -> CapabilityRegistry:
    return CapabilityRegistry([], allow_empty=True)


def test_invalid_legacy_goal_type_is_rejected_not_rewritten() -> None:
    result, plan = validate_goal_declaration(
        state={
            "current_user_input": "把订单转给另一个账号",
            "turn_index": 1,
            "goal_alignment_verifier": _ExactGoalVerifier(),
        },
        args={
            "summary": "转移订单所有权",
            "goals": [
                {
                    "goal_id": "goal-1",
                    "description": "把订单转给另一个账号",
                    "evidence_span": "把订单转给另一个账号",
                    "goal_type": "nearby_existing_category",
                    "expected_result_cardinality": "single",
                    "required": True,
                    "depends_on": [],
                }
            ],
        },
        capability_registry=_empty_registry(),
    )

    assert result["ok"] is False
    assert plan is None
    assert "invalid_goal_type:goal-1" in result["data"]["errors"]


def test_open_requested_effect_freezes_without_goal_type() -> None:
    contract = freeze_semantic_contract(
        turn=7,
        user_text="把订单转给另一个账号",
        summary="转移订单所有权",
        goals=[
            {
                "goal_id": "goal-transfer",
                "description": "把订单转给另一个账号",
                "evidence_span": "把订单转给另一个账号",
                "requested_effect": {
                    "domain": "order",
                    "operation": "transfer_ownership",
                    "object_type": "order",
                    "raw_description": "把订单转给另一个账号",
                },
                "expected_result_cardinality": "single",
                "required": True,
                "depends_on": [],
            }
        ],
        alignment_proof={"verdict": "exact", "source": "test"},
    )

    goals = semantic_goals({"frozen_semantic_contract": contract})
    assert contract["version"] == "frozen-turn-semantic-contract@1"
    assert contract["authority"] == "sole_formal_turn_semantics"
    assert goals[0]["requested_effect"]["operation"] == "transfer_ownership"
    assert "goal_type" not in contract["goals"][0]


def test_context_projection_separates_verified_observations_and_diagnostics() -> None:
    verified, diagnostics = partition_tool_trace(
        [
            {
                "name": "get_order",
                "result": {
                    "ok": True,
                    "code": "ORDER_FOUND",
                    "data": {"order_handle": "order:10002"},
                    "match_proof": {"status": "MATCHED"},
                    "execution_permit": {"permit_id": "permit-1"},
                },
            },
            {
                "name": "create_refund",
                "result": {
                    "ok": False,
                    "code": "CAPABILITY_EXACT_MATCH_REQUIRED",
                    "message": "not exact",
                    "match_proof": {"status": "REJECTED"},
                },
            },
        ]
    )

    assert [row["tool_name"] for row in verified] == ["get_order"]
    assert verified[0]["result_handles"] == ["order:10002"]
    assert [row["tool_name"] for row in diagnostics] == ["create_refund"]
    assert diagnostics[0]["authority"] == "execution_diagnostic_not_user_intent_or_business_fact"


def test_multiple_goal_blockers_do_not_force_single_disposition() -> None:
    blockers = [
        {
            "blocker_id": "blocker-refund-reason",
            "goal_id": "goal-refund",
            "status": "OPEN",
            "missing_kind": "condition",
            "question": "退款原因是什么？",
        },
        {
            "blocker_id": "blocker-invoice-title",
            "goal_id": "goal-invoice",
            "status": "OPEN",
            "missing_kind": "condition",
            "question": "发票抬头是什么？",
        },
    ]

    updated = apply_blocker_resolutions(
        blockers,
        [
            {
                "blocker_id": "blocker-refund-reason",
                "operation": "RESOLVE_BLOCKER",
                "evidence_span": "不灵敏",
            }
        ],
        turn=9,
    )

    active = active_goal_blockers({"goal_blockers": updated})
    assert [row["blocker_id"] for row in active] == ["blocker-invoice-title"]
    resolved = next(row for row in updated if row["blocker_id"] == "blocker-refund-reason")
    assert resolved["status"] == "RESOLVED"
    assert resolved["resolution_evidence_span"] == "不灵敏"


def test_goal_lifecycle_applies_explicit_changes_and_persists_execution_metadata() -> None:
    from agent_core.lifecycle.goal_lifecycle import (
        apply_semantic_contract_to_goal_records,
        update_goal_records_from_execution_plan,
    )

    contract = freeze_semantic_contract(
        turn=3,
        user_text="杯子先别管，继续处理退款",
        summary="继续退款",
        goals=[
            {
                "goal_id": "goal-refund-next",
                "description": "继续处理退款",
                "evidence_span": "继续处理退款",
                "continuation_of": "goal-refund-old",
                "requested_effect": {
                    "domain": "refund",
                    "operation": "create",
                    "object_type": "order",
                },
                "expected_result_cardinality": "single",
                "required": True,
                "depends_on": [],
            }
        ],
        goal_changes=[
            {
                "operation": "SET_GOAL_LIFECYCLE",
                "goal_id": "goal-invoice-old",
                "expected_revision": 1,
                "validated_against_revision": 1,
                "next_revision": 2,
                "from": "ACTIVE",
                "to": "PAUSED",
                "evidence_span": "杯子先别管",
                "evidence_turn": 3,
            }
        ],
        alignment_proof={"verdict": "exact", "source": "test"},
    )
    records = apply_semantic_contract_to_goal_records(
        [
            {"goal_id": "goal-refund-old", "lifecycle": "COMPLETED", "created_turn": 1},
            {"goal_id": "goal-invoice-old", "lifecycle": "ACTIVE", "created_turn": 1},
        ],
        contract,
        turn=3,
    )
    invoice = next(row for row in records if row["goal_id"] == "goal-invoice-old")
    continued = next(row for row in records if row["goal_id"] == "goal-refund-next")
    assert invoice["lifecycle"] == "PAUSED"
    assert continued["continuation_of"] == "goal-refund-old"
    assert continued["requested_effect"]["operation"] == "create"

    records = update_goal_records_from_execution_plan(
        records,
        {
            "goals": [
                {
                    "goal_id": "goal-refund-next",
                    "coverage_status": "COVERED",
                    "completion_tool_names": ["create_refund"],
                }
            ]
        },
        turn=3,
    )
    continued = next(row for row in records if row["goal_id"] == "goal-refund-next")
    assert continued["lifecycle"] == "COMPLETED"
    assert continued["completion_tool_names"] == ["create_refund"]


def test_continuation_capability_hint_requires_explicit_goal_relation() -> None:
    from agent_core.lifecycle.continuation_runtime import verified_continuation_tool_hints

    class _Registry:
        def contract_for_tool(self, name):
            return object() if name == "create_refund" else None

    state = {
        "current_user_input": "不是那个，还是继续之前的退款",
        "goal_records": [
            {
                "goal_id": "goal-old",
                "lifecycle": "COMPLETED",
                "completion_tool_names": ["create_refund"],
            }
        ],
    }
    no_relation = verified_continuation_tool_hints(
        state,
        [{"goal_id": "goal-new", "evidence_span": "还是继续之前的退款"}],
        _Registry(),
    )
    explicit_relation = verified_continuation_tool_hints(
        state,
        [
            {
                "goal_id": "goal-new",
                "evidence_span": "还是继续之前的退款",
                "continuation_of": "goal-old",
            }
        ],
        _Registry(),
    )
    assert no_relation == {}
    assert explicit_relation == {"goal-new": ["create_refund"]}


def _effect_registry() -> CapabilityRegistry:
    from agent_core.kernel.capability import ToolCapabilityContract
    from agent_core.kernel.capability_registry import CapabilityBinding

    def _dispatch(*args, **kwargs):
        return {"ok": True}

    def _binding(
        key: str,
        tool: str,
        kind: str,
        completion=(),
        support=(),
        completion_effects=(),
        support_effects=(),
    ):
        return CapabilityBinding(
            domain_id="test",
            contract=ToolCapabilityContract(
                key=key,
                tool_name=tool,
                category="observation" if kind != "action_draft" else "action_draft",
                writes_business_data=False,
                evidence_sources=("test",),
                planner_rule=f"test {tool}",
                unavailable_response="unavailable",
                execution_kind=kind,
                goal_completion_types=tuple(completion),
                goal_support_types=tuple(support),
                completion_effects=tuple(completion_effects),
                support_effects=tuple(support_effects),
            ),
            schema={
                "type": "function",
                "function": {
                    "name": tool,
                    "description": tool,
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            dispatcher=_dispatch,
        )

    return CapabilityRegistry(
        [
            _binding(
                "ecommerce.refund.prepare",
                "prepare_refund",
                "action_draft",
                completion=("action",),
                completion_effects=("refund.create:order",),
            ),
            _binding(
                "ecommerce.refund.policy_consultation",
                "consult_refund_policy",
                "knowledge_read",
                completion=("consult", "query"),
                completion_effects=("refund.consult_policy:order",),
            ),
            _binding(
                "ecommerce.orders.list",
                "list_orders",
                "grounding_read",
                completion=("query",),
                support=("action", "consult"),
                completion_effects=("order.list:order",),
                support_effects=("refund.create:order",),
            ),
            _binding(
                "runtime.unsupported",
                "report_unsupported_request",
                "unsupported",
                completion=("query", "consult", "action", "unsupported"),
            ),
        ]
    )


def _refund_contract() -> dict:
    return freeze_semantic_contract(
        turn=4,
        user_text="把键盘退了",
        summary="创建退款",
        goals=[
            {
                "goal_id": "goal-refund",
                "description": "为键盘订单创建退款",
                "evidence_span": "把键盘退了",
                "requested_effect": {
                    "domain": "refund",
                    "operation": "create",
                    "object_type": "order",
                },
                "expected_result_cardinality": "single",
                "required": True,
                "depends_on": [],
            }
        ],
        alignment_proof={"verdict": "exact", "source": "test"},
    )


def test_exact_effect_surface_does_not_select_nearby_consultation() -> None:
    from agent_core.runtime.capability_effects import discover_exact_effect_surface

    registry = _effect_registry()
    contract = _refund_contract()
    surface = discover_exact_effect_surface(registry, semantic_goals(contract))
    row = surface["goals"][0]

    assert row["status"] == "exact_supported"
    assert row["completion_tools"] == ["prepare_refund"]
    assert "list_orders" in row["support_tools"]
    assert "consult_refund_policy" not in row["candidate_tools"]
    assert row["similarity_used"] is False


def test_unknown_effect_is_preserved_and_only_unsupported_reporter_is_exposed() -> None:
    from agent_core.runtime.capability_effects import discover_exact_effect_surface

    registry = _effect_registry()
    contract = freeze_semantic_contract(
        turn=5,
        user_text="把订单转给另一个账号",
        summary="转移订单所有权",
        goals=[
            {
                "goal_id": "goal-transfer",
                "description": "把订单转给另一个账号",
                "evidence_span": "把订单转给另一个账号",
                "requested_effect": {
                    "domain": "order",
                    "operation": "transfer_ownership",
                    "object_type": "order",
                },
                "expected_result_cardinality": "single",
                "required": True,
                "depends_on": [],
            }
        ],
        alignment_proof={"verdict": "exact", "source": "test"},
    )
    surface = discover_exact_effect_surface(registry, semantic_goals(contract))
    row = surface["goals"][0]

    assert row["requested_effect_identity"] == "order.transfer_ownership:order"
    assert row["status"] == "absent_proven"
    assert row["candidate_tools"] == ["report_unsupported_request"]


def test_goal_effect_match_proof_distinguishes_completion_support_and_mismatch() -> None:
    from agent_core.runtime.capability_effects import goal_effect_match_proof

    registry = _effect_registry()
    state = {"frozen_semantic_contract": _refund_contract()}

    completion = goal_effect_match_proof(
        state=state,
        tool_name="prepare_refund",
        goal_ids=["goal-refund"],
        registry=registry,
    )
    support = goal_effect_match_proof(
        state=state,
        tool_name="list_orders",
        goal_ids=["goal-refund"],
        registry=registry,
    )
    mismatch = goal_effect_match_proof(
        state=state,
        tool_name="consult_refund_policy",
        goal_ids=["goal-refund"],
        registry=registry,
    )

    assert completion["allowed"] is True
    assert completion["goals"][0]["role"] == "completion"
    assert support["allowed"] is True
    assert support["goals"][0]["role"] == "support"
    assert mismatch["allowed"] is False
    assert mismatch["goals"][0]["role"] == "none"


def test_one_goal_can_expand_to_support_and_completion_tools_without_split() -> None:
    from agent_core.lifecycle.workflow_runtime import build_workflow_plan
    from agent_core.runtime.capability_effects import discover_exact_effect_surface
    from agent_core.runtime.capability_gate import build_effects

    registry = _effect_registry()
    contract = _refund_contract()
    state = {
        "turn_index": 4,
        "frozen_semantic_contract": contract,
    }
    state["capability_surface"] = discover_exact_effect_surface(
        registry, semantic_goals(contract)
    )
    effects, calls = build_effects(
        plan_id="plan:test",
        calls=[
            {"name": "list_orders", "args": {"goal_ids": ["goal-refund"]}},
            {"name": "prepare_refund", "args": {"goal_ids": ["goal-refund"]}},
        ],
        capability_registry=registry,
    )
    plan = build_workflow_plan(
        state=state,
        turn_plan={
            "plan_id": "plan:test",
            "effects": effects,
            "tool_calls": calls,
        },
        user_text="把键盘退了",
    )

    assert len(plan["goals"]) == 1
    support_step = next(row for row in plan["steps"] if row["tool_name"] == "list_orders")
    completion_step = next(row for row in plan["steps"] if row["tool_name"] == "prepare_refund")
    assert support_step["verification"]["goal_effect_role"] == "support"
    assert support_step["verification"]["goal_completion_eligible"] is False
    assert completion_step["verification"]["goal_effect_role"] == "completion"
    assert completion_step["verification"]["goal_completion_eligible"] is True
    assert plan["goals"][0]["covered_by_step_ids"] == [completion_step["step_id"]]


def test_execution_permit_rejects_manually_surfaced_nearby_capability() -> None:
    from agent_core.runtime.capability_gate import build_effects, issue_execution_permit

    registry = _effect_registry()
    contract = _refund_contract()
    effects, _ = build_effects(
        plan_id="plan:manual-surface",
        calls=[
            {
                "name": "consult_refund_policy",
                "args": {"goal_ids": ["goal-refund"]},
            }
        ],
        capability_registry=registry,
    )
    state = {
        "turn_index": 4,
        "current_user_input": "把键盘退了",
        "frozen_semantic_contract": contract,
        "artifact_ledger": [],
        # Simulate a stale or compromised discovery surface. The permit gate
        # must still reject the nearby capability by formal effect identity.
        "capability_surface": {
            "goals": [
                {
                    "goal_id": "goal-refund",
                    "status": "exact_supported",
                    "candidate_tools": ["consult_refund_policy"],
                }
            ]
        },
        "current_turn_plan": {"effects": effects},
    }

    decision = issue_execution_permit(
        state=state,
        tool_name="consult_refund_policy",
        args={},
        effect_id=effects[0]["effect_id"],
        capability_registry=registry,
    )

    assert decision.permitted is False
    assert decision.rejection["code"] == "CAPABILITY_GOAL_EFFECT_MISMATCH"
    proof = decision.match_proof["goal_effect_identity"]
    assert proof["allowed"] is False
    assert proof["similarity_used"] is False


def test_module_owned_effect_contract_supports_unseen_domain_without_runtime_mapping() -> None:
    from agent_core.kernel.capability import ToolCapabilityContract
    from agent_core.kernel.capability_registry import CapabilityBinding
    from agent_core.runtime.capability_effects import discover_exact_effect_surface

    binding = CapabilityBinding(
        domain_id="custom",
        contract=ToolCapabilityContract(
            key="custom.subscription.pause",
            tool_name="pause_subscription",
            category="action",
            writes_business_data=False,
            evidence_sources=("test",),
            planner_rule="pause subscription",
            unavailable_response="unavailable",
            execution_kind="action_draft",
            completion_effects=("subscription.pause:subscription",),
        ),
        schema={
            "type": "function",
            "function": {
                "name": "pause_subscription",
                "description": "pause",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        dispatcher=lambda *args, **kwargs: {"ok": True},
    )
    registry = CapabilityRegistry([binding])
    goals = [{
        "goal_id": "goal-pause",
        "requested_effect": {
            "domain": "subscription",
            "operation": "pause",
            "object_type": "subscription",
        },
    }]

    surface = discover_exact_effect_surface(registry, goals)

    assert surface["goals"][0]["status"] == "exact_supported"
    assert surface["goals"][0]["completion_tools"] == ["pause_subscription"]


def test_registry_rejects_invalid_module_effect_identity() -> None:
    import pytest
    from agent_core.kernel.capability import ToolCapabilityContract
    from agent_core.kernel.capability_registry import CapabilityBinding

    binding = CapabilityBinding(
        domain_id="custom",
        contract=ToolCapabilityContract(
            key="custom.invalid",
            tool_name="invalid_tool",
            category="query",
            writes_business_data=False,
            evidence_sources=("test",),
            planner_rule="invalid",
            unavailable_response="unavailable",
            completion_effects=("not-a-valid-effect",),
        ),
        schema={
            "type": "function",
            "function": {
                "name": "invalid_tool",
                "description": "invalid",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        dispatcher=lambda *args, **kwargs: {"ok": True},
    )

    with pytest.raises(ValueError, match="invalid completion_effects identity"):
        CapabilityRegistry([binding])


def _grounded_refund_plan() -> tuple[dict, dict]:
    from agent_core.lifecycle.workflow_runtime import build_workflow_plan
    from agent_core.runtime.capability_effects import discover_exact_effect_surface
    from agent_core.runtime.capability_gate import build_effects

    registry = _effect_registry()
    contract = _refund_contract()
    state = {
        "turn_index": 4,
        "frozen_semantic_contract": contract,
    }
    state["capability_surface"] = discover_exact_effect_surface(
        registry, semantic_goals(contract)
    )
    effects, calls = build_effects(
        plan_id="plan:grounded-validation",
        calls=[
            {"name": "list_orders", "args": {"goal_ids": ["goal-refund"]}},
            {"name": "prepare_refund", "args": {"goal_ids": ["goal-refund"]}},
        ],
        capability_registry=registry,
    )
    return contract, build_workflow_plan(
        state=state,
        turn_plan={
            "plan_id": "plan:grounded-validation",
            "effects": effects,
            "tool_calls": calls,
        },
        user_text="把键盘退了",
    )


def test_grounded_execution_plan_is_semantically_bound_and_digest_protected() -> None:
    from agent_core.lifecycle.workflow_runtime import validate_grounded_execution_plan

    contract, plan = _grounded_refund_plan()
    validation = validate_grounded_execution_plan(
        plan=plan,
        semantic_contract=contract,
    )

    assert plan["plan_contract_version"] == "grounded-execution-plan@2"
    assert validation["status"] == "ACCEPTED"
    assert validation["semantic_binding_verified"] is True
    assert plan["plan_digest"] == validation["structure_digest"]
    assert plan["immutable_structure"] is True


def test_grounded_execution_plan_rejects_dependency_cycle() -> None:
    from copy import deepcopy

    from agent_core.lifecycle.workflow_runtime import validate_grounded_execution_plan

    contract, plan = _grounded_refund_plan()
    cyclic = deepcopy(plan)
    first, second = cyclic["steps"]
    first["depends_on"] = [second["effect_id"]]
    second["depends_on"] = [first["effect_id"]]

    validation = validate_grounded_execution_plan(
        plan=cyclic,
        semantic_contract=contract,
    )

    assert validation["status"] == "REJECTED"
    assert any(row["code"] == "PLAN_DEPENDENCY_CYCLE" for row in validation["errors"])


def test_dispatch_rejects_stale_semantic_binding() -> None:
    from copy import deepcopy

    from agent_core.lifecycle.workflow_runtime import validate_step_dispatch

    contract, plan = _grounded_refund_plan()
    stale = deepcopy(contract)
    stale["semantic_digest"] = "stale-digest"
    effect_id = plan["steps"][0]["effect_id"]

    result = validate_step_dispatch(
        workflow_plan=plan,
        effect_id=effect_id,
        semantic_contract=stale,
    )

    assert result["ok"] is False
    assert result["code"] == "GROUNDED_PLAN_VALIDATION_FAILED"
    assert any(
        row["code"] == "PLAN_SEMANTIC_DIGEST_MISMATCH"
        for row in result["data"]["validation"]["errors"]
    )


def test_dispatch_rejects_structural_tampering_even_when_plan_remains_well_formed() -> None:
    from copy import deepcopy

    from agent_core.lifecycle.workflow_runtime import validate_step_dispatch

    contract, plan = _grounded_refund_plan()
    tampered = deepcopy(plan)
    tampered["steps"][0]["capability_id"] = "changed-without-replanning"
    effect_id = tampered["steps"][0]["effect_id"]

    result = validate_step_dispatch(
        workflow_plan=tampered,
        effect_id=effect_id,
        semantic_contract=contract,
    )

    assert result["ok"] is False
    assert result["code"] == "GROUNDED_PLAN_DIGEST_MISMATCH"


def test_write_serialization_uses_capability_execution_kind_not_legacy_goal_type():
    from agent_core.lifecycle.tool_execution_runtime import _call_is_bound_to_write_capability

    plan = {
        "effects": [
            {
                "effect_id": "e-write",
                "goal_ids": ["g-query-labelled"],
                "execution_kind": "action_draft",
            },
            {
                "effect_id": "e-read",
                "goal_ids": ["g-action-labelled"],
                "execution_kind": "grounding_read",
            },
        ]
    }

    assert _call_is_bound_to_write_capability(
        call={"_effect_id": "e-write", "_goal_ids": ["g-query-labelled"]},
        plan=plan,
    )
    assert not _call_is_bound_to_write_capability(
        call={"_effect_id": "e-read", "_goal_ids": ["g-action-labelled"]},
        plan=plan,
    )


def test_new_goal_declaration_schema_requires_open_requested_effect():
    from agent_core.lifecycle.protocol import DECLARE_TURN_GOALS_SCHEMA

    goal_schema = DECLARE_TURN_GOALS_SCHEMA["function"]["parameters"]["properties"]["goals"]["items"]
    assert "requested_effect" in goal_schema["required"]


def test_new_goal_declaration_rejects_goal_type_only_semantics():
    result, plan = validate_goal_declaration(
        state={
            "current_user_input": "给键盘退款",
            "turn_index": 9,
            "goal_alignment_verifier": _ExactGoalVerifier(),
        },
        args={
            "summary": "给键盘退款",
            "goals": [{
                "goal_id": "refund-keyboard",
                "description": "给键盘退款",
                "evidence_span": "给键盘退款",
                "goal_type": "action",
                "expected_result_cardinality": "single",
                "required": True,
                "depends_on": [],
            }],
        },
        capability_registry=_empty_registry(),
    )

    assert result["ok"] is False
    assert plan is None
    assert "invalid_requested_effect:refund-keyboard:requested_effect.required_for_new_turn" in result["data"]["errors"]
