from __future__ import annotations

from copy import deepcopy


def _contract(goals):
    from agent_core.lifecycle.semantic_contract import freeze_semantic_contract

    return freeze_semantic_contract(
        turn=1,
        user_text="test user text",
        summary="test",
        goals=goals,
        alignment_proof={"verdict": "exact", "evidence_spans": ["test user text"]},
    )


def _goal(goal_id: str, *, domain: str, operation: str, object_type: str = "order", depends_on=()):
    row = {
        "goal_id": goal_id,
        "description": f"{domain}.{operation}",
        "evidence_span": "test user text",
        "requested_effect": {
            "domain": domain,
            "operation": operation,
            "object_type": object_type,
            "raw_description": f"{domain}.{operation}",
        },
        "expected_result_cardinality": "single",
        "required": True,
    }
    if depends_on:
        row["depends_on"] = list(depends_on)
    return row


def _registry():
    from agent_core.kernel.capability_registry import CapabilityRegistry
    from agent_modules.ecommerce.module import EcommerceModule

    return CapabilityRegistry(EcommerceModule().contribution().capabilities)


def test_shadow_plan_is_built_from_frozen_goals_before_tool_calls() -> None:
    from agent_core.lifecycle.pretool_planner import build_pretool_shadow_plan

    state = {
        "frozen_semantic_contract": _contract([
            _goal("logistics", domain="order", operation="query_logistics")
        ]),
        "current_turn_plan": {"tool_calls": [{"name": "prepare_refund"}]},
    }
    plan = build_pretool_shadow_plan(state=state, capability_registry=_registry())

    assert plan["version"] == "pretool-grounded-shadow-plan@1"
    assert plan["authority"] == "shadow_only_not_execution_authority"
    assert plan["generated_before_model_tool_call"] is True
    assert plan["must_not_dispatch"] is True
    assert plan["observed_model_tool_calls"] == []
    task = plan["goal_plans"][0]
    assert task["goal_id"] == "logistics"
    assert task["preferred_path"]["completion_tool"] == "get_order_logistics"
    assert task["preferred_path"]["status"] == "closed"


def test_refund_shadow_plan_derives_direct_and_assessment_paths_from_contract_types() -> None:
    from agent_core.lifecycle.pretool_planner import build_pretool_shadow_plan

    state = {
        "frozen_semantic_contract": _contract([
            _goal("refund", domain="refund", operation="create")
        ])
    }
    plan = build_pretool_shadow_plan(state=state, capability_registry=_registry())
    task = plan["goal_plans"][0]
    paths = {row["completion_tool"]: row for row in task["candidate_paths"]}

    assert {"prepare_refund", "prepare_refund_from_eligibility"} <= set(paths)
    assert paths["prepare_refund"]["status"] == "closed"
    promoted = paths["prepare_refund_from_eligibility"]
    assert promoted["status"] == "closed"
    assert [step["tool_name"] for step in promoted["steps"]] == [
        "evaluate_refund_eligibility",
        "prepare_refund_from_eligibility",
    ]
    assert promoted["steps"][1]["depends_on_step_ids"] == [promoted["steps"][0]["step_id"]]
    assert task["preferred_path"]["completion_tool"] == "prepare_refund"


def test_shadow_plan_does_not_guess_when_exact_completion_or_v2_contract_is_absent() -> None:
    from agent_core.lifecycle.pretool_planner import build_pretool_shadow_plan

    state = {
        "frozen_semantic_contract": _contract([
            _goal("transfer", domain="order", operation="transfer_ownership")
        ])
    }
    plan = build_pretool_shadow_plan(state=state, capability_registry=_registry())
    task = plan["goal_plans"][0]

    assert task["status"] == "absent_proven"
    assert task["candidate_paths"] == []
    assert task["preferred_path"] is None
    assert plan["status"] == "UNRESOLVED_SHADOW"


def test_shadow_comparison_records_divergence_without_becoming_execution_authority() -> None:
    from agent_core.lifecycle.pretool_planner import (
        build_pretool_shadow_plan,
        compare_shadow_plan_to_model_calls,
    )

    plan = build_pretool_shadow_plan(
        state={
            "frozen_semantic_contract": _contract([
                _goal("logistics", domain="order", operation="query_logistics")
            ])
        },
        capability_registry=_registry(),
    )
    comparison = compare_shadow_plan_to_model_calls(
        plan,
        [{"name": "list_invoices", "args": {"goal_ids": ["logistics"]}}],
    )

    assert comparison["status"] == "DIVERGED"
    assert comparison["unexpected_bindings"] == [
        {"goal_id": "logistics", "tool_name": "list_invoices"}
    ]
    assert comparison["blocks_execution"] is False
    assert comparison["creates_permit"] is False
    assert comparison["mutates_semantics"] is False
    assert plan["observed_model_tool_calls"] == []


def test_shadow_plan_preserves_goal_dependencies_without_inventing_business_order() -> None:
    from agent_core.lifecycle.pretool_planner import build_pretool_shadow_plan

    contract = _contract([
        _goal("refund", domain="refund", operation="create"),
        _goal("invoice", domain="invoice", operation="create", depends_on=("refund",)),
    ])
    plan = build_pretool_shadow_plan(
        state={"frozen_semantic_contract": deepcopy(contract)},
        capability_registry=_registry(),
    )
    by_goal = {row["goal_id"]: row for row in plan["goal_plans"]}

    assert by_goal["refund"]["depends_on_goal_ids"] == []
    assert by_goal["invoice"]["depends_on_goal_ids"] == ["refund"]
    assert plan["goal_dependency_edges"] == [{"from_goal_id": "refund", "to_goal_id": "invoice"}]


def test_shadow_comparison_detects_goal_dependency_order_without_blocking_execution() -> None:
    from agent_core.lifecycle.pretool_planner import (
        build_pretool_shadow_plan,
        compare_shadow_plan_to_model_calls,
    )

    plan = build_pretool_shadow_plan(
        state={
            "frozen_semantic_contract": _contract([
                _goal("refund", domain="refund", operation="create"),
                _goal("invoice", domain="invoice", operation="create", depends_on=("refund",)),
            ])
        },
        capability_registry=_registry(),
    )
    comparison = compare_shadow_plan_to_model_calls(
        plan,
        [
            {"name": "prepare_invoice", "args": {"goal_ids": ["invoice"]}},
            {"name": "prepare_refund", "args": {"goal_ids": ["refund"]}},
        ],
    )

    assert comparison["status"] == "DIVERGED"
    assert comparison["dependency_order_violations"] == [
        {
            "from_goal_id": "refund",
            "to_goal_id": "invoice",
            "reason": "dependent_goal_observed_before_declared_dependency",
        }
    ]
    assert comparison["blocks_execution"] is False


def test_shadow_plan_is_not_injected_into_model_prompt() -> None:
    from agent_core.lifecycle.dialogue_runtime import _loop_system_prompt

    class EmptyContext:
        def build(self, _state):
            return {"context_health": {}}

    prompt = _loop_system_prompt(
        {
            "current_user_input": "test user text",
            "frozen_semantic_contract": _contract([
                _goal("logistics", domain="order", operation="query_logistics")
            ]),
            "pretool_shadow_plan": {"secret_marker": "SHADOW_MUST_NOT_ENTER_PROMPT"},
        },
        context_bundle_builder=EmptyContext(),
        capability_registry=_registry(),
    )
    assert "SHADOW_MUST_NOT_ENTER_PROMPT" not in prompt


def test_typed_verification_failure_does_not_raise_or_add_dependencies() -> None:
    from agent_core.lifecycle.pretool_planner import build_pretool_shadow_plan

    contract = _contract(
        [
            {
                **_goal("lookup", domain="order", operation="query_logistics"),
                "input_bindings": [],
            },
            {
                **_goal(
                    "refund",
                    domain="refund",
                    operation="create",
                ),
                "input_bindings": [
                    {
                        "port": "target",
                        "source": {
                            "kind": "current_goal_output",
                            "producer_goal_id": "lookup",
                            "output_id": "open:query_logistics",
                        },
                        "relation_kind": "result_reference",
                        "expected_cardinality": "single",
                        "evidence_span": "它",
                    }
                ],
            },
        ]
    )

    plan = build_pretool_shadow_plan(
        state={"frozen_semantic_contract": contract},
        capability_registry=_registry(),
    )
    by_goal = {row["goal_id"]: row for row in plan["goal_plans"]}

    assert by_goal["refund"]["depends_on_goal_ids"] == []
    assert plan["must_not_dispatch"] is True
    assert plan["creates_permit"] is False
