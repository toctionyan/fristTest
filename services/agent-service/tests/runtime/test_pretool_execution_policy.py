from __future__ import annotations


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
    return {
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
        "depends_on": list(depends_on),
    }


def _registry():
    from agent_core.kernel.capability_registry import CapabilityRegistry
    from agent_modules.ecommerce.module import EcommerceModule

    return CapabilityRegistry(EcommerceModule().contribution().capabilities)


def _completed_assessment_state(contract):
    from agent_core.lifecycle.plan_execution import (
        begin_step_attempt,
        complete_step_attempt,
        create_plan_run,
        freeze_plan_definition,
    )

    plan = {
        "plan_contract_version": "grounded-execution-plan@2",
        "workflow_id": "workflow:refund",
        "turn_plan_id": "turn-plan:refund",
        "formal_semantic_contract_id": contract["semantic_contract_id"],
        "formal_semantic_digest": contract["semantic_digest"],
        "goal_source": "frozen_semantic_contract",
        "level": "SEQUENTIAL",
        "goals": [{"goal_id": "refund", "required": True, "depends_on": []}],
        "tasks": [{"task_id": "task:refund", "goal_ids": ["refund"]}],
        "steps": [{
            "step_id": "step:assess",
            "effect_id": "effect:assess",
            "tool_name": "evaluate_refund_eligibility",
            "capability_id": "ecommerce.refund.eligibility",
            "goal_ids": ["refund"],
            "depends_on": [],
            "required": True,
            "verification": {"goal_effect_role": "support"},
        }],
        "created_turn": 1,
    }
    definition = freeze_plan_definition(plan)
    run = create_plan_run(definition, turn_index=1)
    run, attempt = begin_step_attempt(
        definition=definition,
        plan_run=run,
        effect_id="effect:assess",
        tool_name="evaluate_refund_eligibility",
        args={"target": {"mode": "single", "order_id": "10002"}},
        execution_permit={"permit_id": "permit:assess"},
    )
    run, _outcome = complete_step_attempt(
        definition=definition,
        plan_run=run,
        attempt_id=attempt["attempt_id"],
        result={"ok": True, "data": {"assessment_id": "assessment:1"}},
        step_status="SUCCEEDED",
        failure_type="NONE",
    )
    return definition, run


def test_policy_exposes_only_initial_frontier_for_contract_closed_refund_paths() -> None:
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    state = {
        "frozen_semantic_contract": _contract([
            _goal("refund", domain="refund", operation="create")
        ])
    }
    policy = build_pretool_execution_policy(state=state, capability_registry=_registry())

    assert policy["mode"] == "ENFORCED"
    assert set(policy["allowed_capability_tools"]) == {
        "evaluate_refund_eligibility",
        "prepare_refund",
    }
    goal = policy["goal_policies"][0]
    assert goal["status"] == "FRONTIER_READY"
    assert goal["max_path_progress"] == 0
    assert policy["creates_permit"] is False
    assert policy["dispatches_tools"] is False


def test_policy_prefers_progressed_path_after_verified_support_step() -> None:
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    contract = _contract([_goal("refund", domain="refund", operation="create")])
    definition, run = _completed_assessment_state(contract)
    policy = build_pretool_execution_policy(
        state={
            "frozen_semantic_contract": contract,
            "frozen_plan_definition": definition,
            "plan_run": run,
        },
        capability_registry=_registry(),
    )

    assert policy["mode"] == "ENFORCED"
    assert policy["allowed_capability_tools"] == ["prepare_refund_from_eligibility"]
    goal = policy["goal_policies"][0]
    assert goal["completed_tools"] == ["evaluate_refund_eligibility"]
    assert goal["max_path_progress"] == 1


def test_policy_blocks_dependent_goal_until_declared_dependency_completes() -> None:
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    contract = _contract([
        _goal("refund", domain="refund", operation="create"),
        _goal("invoice", domain="invoice", operation="create", depends_on=("refund",)),
    ])
    policy = build_pretool_execution_policy(
        state={"frozen_semantic_contract": contract, "goal_records": []},
        capability_registry=_registry(),
    )
    by_goal = {row["goal_id"]: row for row in policy["goal_policies"]}

    assert by_goal["invoice"]["status"] == "BLOCKED_BY_GOAL_DEPENDENCY"
    assert by_goal["invoice"]["allowed_tools"] == []
    assert "prepare_invoice" not in policy["allowed_capability_tools"]

    completed = build_pretool_execution_policy(
        state={
            "frozen_semantic_contract": contract,
            "goal_records": [{"goal_id": "refund", "lifecycle": "COMPLETED"}],
        },
        capability_registry=_registry(),
    )
    assert "prepare_invoice" in completed["allowed_capability_tools"]


def test_policy_preserves_exact_unsupported_reporter_without_nearby_tool() -> None:
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    policy = build_pretool_execution_policy(
        state={
            "frozen_semantic_contract": _contract([
                _goal("transfer", domain="order", operation="transfer_ownership")
            ])
        },
        capability_registry=_registry(),
    )

    assert policy["allowed_capability_tools"] == ["report_unsupported_request"]
    assert policy["goal_policies"][0]["status"] == "UNSUPPORTED_EXACTLY_PROVEN"


def test_migrated_order_details_uses_contract_frontier_without_support_tool_leakage() -> None:
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    policy = build_pretool_execution_policy(
        state={
            "frozen_semantic_contract": _contract([
                _goal("details", domain="order", operation="query_details")
            ])
        },
        capability_registry=_registry(),
    )

    assert policy["mode"] == "ENFORCED"
    assert policy["migration_gap_goal_ids"] == []
    assert policy["allowed_capability_tools"] == ["get_order_details"]
    assert policy["goal_policies"][0]["enforcement"] == "contract_frontier"


def test_prompt_projection_contains_boundary_not_shadow_topology() -> None:
    from agent_core.lifecycle.pretool_execution_policy import (
        build_pretool_execution_policy,
        execution_policy_prompt_projection,
    )

    policy = build_pretool_execution_policy(
        state={
            "frozen_semantic_contract": _contract([
                _goal("logistics", domain="order", operation="query_logistics")
            ])
        },
        capability_registry=_registry(),
    )
    projection = execution_policy_prompt_projection(policy)

    assert projection["mode"] == "ENFORCED"
    assert projection["allowed_capability_tools"] == ["get_order_logistics"]
    assert "candidate_paths" not in projection
    assert "ExecutionPermit" not in projection["rule"]


def test_agent_loop_binds_policy_frontier_instead_of_full_exact_surface(monkeypatch) -> None:
    from types import SimpleNamespace

    from agent_core.lifecycle.dialogue_runtime import agent_loop_node

    contract = _contract([
        _goal("logistics", domain="order", operation="query_logistics")
    ])

    class ContextBuilder:
        def build(self, _state):
            return {"context_health": {}}

    class CapturingModel:
        def __init__(self):
            self.bound_names = []

        def bind_tools(self, schemas, **_kwargs):
            self.bound_names = [
                str((schema.get("function") or {}).get("name") or "")
                for schema in schemas
            ]
            return self

    model = CapturingModel()
    monkeypatch.setattr(
        "agent_core.lifecycle.dialogue_runtime.invoke_model",
        lambda **_kwargs: (SimpleNamespace(content="", tool_calls=[]), {"status": "test"}),
    )
    update = agent_loop_node(
        {
            "current_user_input": "test user text",
            "frozen_semantic_contract": contract,
            "turn_index": 1,
            "agent_loop_step": 0,
            "agent_loop_max_steps": 6,
            "loop_plans": [],
            "goal_records": [],
            "artifact_ledger": [],
        },
        context_bundle_builder=ContextBuilder(),
        capability_registry=_registry(),
        model_resolver=lambda: model,
    )

    assert "get_order_logistics" in model.bound_names
    assert "list_orders" not in model.bound_names
    assert "prepare_refund" not in model.bound_names
    assert update["pretool_execution_policy"]["allowed_capability_tools"] == [
        "get_order_logistics"
    ]


def test_plan_run_terminal_projection_cannot_unlock_dependent_goal() -> None:
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    contract = _contract([
        _goal("refund", domain="refund", operation="create"),
        _goal("invoice", domain="invoice", operation="create", depends_on=("refund",)),
    ])
    definition, run = _completed_assessment_state(contract)
    # Structural PlanRun validation does not make this projection a Goal lifecycle
    # authority.  A forged/stale COVERED row must not unlock invoice execution.
    run["terminal_goal_states"] = {
        "refund": {"terminal_tool": "prepare_refund", "handling_status": "COVERED"}
    }
    policy = build_pretool_execution_policy(
        state={
            "frozen_semantic_contract": contract,
            "frozen_plan_definition": definition,
            "plan_run": run,
            "goal_records": [],
        },
        capability_registry=_registry(),
    )
    by_goal = {row["goal_id"]: row for row in policy["goal_policies"]}

    assert by_goal["invoice"]["status"] == "BLOCKED_BY_GOAL_DEPENDENCY"
    assert "prepare_invoice" not in policy["allowed_capability_tools"]


def test_invalid_progress_evidence_restarts_from_zero_without_surface_widening() -> None:
    from copy import deepcopy

    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    contract = _contract([_goal("refund", domain="refund", operation="create")])
    definition, run = _completed_assessment_state(contract)
    corrupt_definition = deepcopy(definition)
    corrupt_definition["definition_digest"] = "tampered"

    policy = build_pretool_execution_policy(
        state={
            "frozen_semantic_contract": contract,
            "frozen_plan_definition": corrupt_definition,
            "plan_run": run,
        },
        capability_registry=_registry(),
    )

    assert policy["mode"] == "EVIDENCE_INVALID_ZERO_PROGRESS"
    assert policy["runtime_evidence_errors"] == [
        "FROZEN_PLAN_DEFINITION_DIGEST_INVALID"
    ]
    assert set(policy["allowed_capability_tools"]) == {
        "evaluate_refund_eligibility",
        "prepare_refund",
    }
    assert "prepare_refund_from_eligibility" not in policy["allowed_capability_tools"]


def test_agent_loop_policy_compiler_failure_hides_all_business_tools(monkeypatch) -> None:
    from types import SimpleNamespace

    from agent_core.lifecycle.dialogue_runtime import agent_loop_node

    contract = _contract([_goal("details", domain="order", operation="query_details")])

    class ContextBuilder:
        def build(self, _state):
            return {"context_health": {}}

    class CapturingModel:
        def __init__(self):
            self.bound_names = []

        def bind_tools(self, schemas, **_kwargs):
            self.bound_names = [
                str((schema.get("function") or {}).get("name") or "")
                for schema in schemas
            ]
            return self

    model = CapturingModel()
    monkeypatch.setattr(
        "agent_core.lifecycle.dialogue_runtime.build_pretool_execution_policy",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("compiler failed")),
    )
    monkeypatch.setattr(
        "agent_core.lifecycle.dialogue_runtime.invoke_model",
        lambda **_kwargs: (SimpleNamespace(content="", tool_calls=[]), {"status": "test"}),
    )

    update = agent_loop_node(
        {
            "current_user_input": "test user text",
            "frozen_semantic_contract": contract,
            "turn_index": 1,
            "agent_loop_step": 0,
            "agent_loop_max_steps": 6,
            "loop_plans": [],
            "goal_records": [],
            "artifact_ledger": [],
        },
        context_bundle_builder=ContextBuilder(),
        capability_registry=_registry(),
        model_resolver=lambda: model,
    )

    registered_business_tools = set(_registry().tool_names())
    assert registered_business_tools.isdisjoint(model.bound_names)
    assert "respond_to_user" in model.bound_names
    assert update["pretool_execution_policy"]["mode"] == "POLICY_BUILD_FAILED_FAIL_CLOSED"
    assert update["pretool_execution_policy"]["allowed_capability_tools"] == []
