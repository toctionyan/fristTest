from __future__ import annotations

from copy import deepcopy
from typing import Any

from agent_core.composition import get_runtime_registry
from agent_core.lifecycle.plan_execution import (
    create_plan_run,
    freeze_plan_definition,
    project_grounded_execution_plan,
)
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract


def install_test_semantic_contract(state: dict[str, Any], declaration: dict[str, Any]) -> dict[str, Any]:
    """Install a formal semantic contract for workflow-focused unit tests.

    Older tests described goals with a retired ``turn_goal_plan`` fixture. This
    helper keeps those tests focused on workflow behavior while making the
    semantic authority explicit. It is test-only and never participates in
    runtime capability selection.
    """
    assert isinstance(state, dict), "test semantic state must be a mapping"
    assert isinstance(declaration, dict), "test semantic declaration must be a mapping"
    goals: list[dict[str, Any]] = []
    for index, raw in enumerate(list(declaration.get("goals") or []), start=1):
        if not isinstance(raw, dict):
            continue
        row = deepcopy(raw)
        description = str(row.get("description") or f"test goal {index}")
        effect = row.get("requested_effect")
        if not isinstance(effect, dict):
            effect = {
                "domain": "test",
                "operation": str(row.get("goal_type") or "query"),
                "object_type": "test_object",
                "raw_description": description,
            }
        goals.append({
            "goal_id": str(row.get("goal_id") or f"goal:{index}"),
            "description": description,
            "evidence_span": str(row.get("evidence_span") or state.get("current_user_input") or description),
            "requested_effect": effect,
            "expected_result_cardinality": str(row.get("expected_result_cardinality") or "unknown"),
            "required": bool(row.get("required", True)),
            "depends_on": [str(value) for value in list(row.get("depends_on") or []) if str(value)],
            "continuation_of": str(row.get("continuation_of") or "") or None,
            "goal_type": str(row.get("goal_type") or "open"),
        })
    contract = freeze_semantic_contract(
        turn=int(declaration.get("turn") or state.get("turn_index") or 0),
        user_text=str(declaration.get("user_text") or state.get("current_user_input") or ""),
        summary=str(declaration.get("summary") or "test semantic contract"),
        goals=goals,
        alignment_proof={"verdict": "exact", "authority": "test_fixture"},
    )
    state["state_schema_version"] = 2
    state["frozen_semantic_contract"] = contract
    return contract


def requested_effect_for_tool(tool_name: str, *, role: str = "completion") -> dict[str, str]:
    """Return the exact registered effect for an explicitly named test tool.

    This is test-fixture plumbing, not language discovery: callers must name the
    intended tool and role, and an absent or ambiguous contract fails loudly.
    """
    contract = get_runtime_registry().capabilities.contract_for_tool(tool_name)
    if contract is None:
        raise AssertionError(f"TEST_TOOL_CONTRACT_NOT_FOUND:{tool_name}")
    identities = (
        list(contract.completion_effects)
        if role == "completion"
        else list(contract.support_effects)
    )
    if len(identities) != 1:
        raise AssertionError(
            f"TEST_TOOL_EFFECT_NOT_UNIQUE:{tool_name}:{role}:{identities}"
        )
    identity = str(identities[0])
    operation_identity, separator, object_type = identity.partition(":")
    domain, dot, operation = operation_identity.partition(".")
    if not separator or not dot or not domain or not operation or not object_type:
        raise AssertionError(f"TEST_TOOL_EFFECT_INVALID:{tool_name}:{identity}")
    return {
        "domain": domain,
        "operation": operation,
        "object_type": object_type,
        "raw_description": f"test exact effect for {tool_name}",
    }


def install_test_plan_authority(
    state: dict[str, Any],
    *,
    goals: list[dict[str, Any]],
    steps: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    level: str = "L1_LIGHTWEIGHT_PLAN",
) -> dict[str, Any]:
    """Install a real immutable Definition/PlanRun pair for reader tests."""
    plan = {
        "plan_contract_version": "grounded-execution-plan@2",
        "workflow_id": "workflow:test-authority",
        "turn_plan_id": "turn-plan:test-authority",
        "formal_semantic_contract_id": (state.get("frozen_semantic_contract") or {}).get("semantic_contract_id"),
        "formal_semantic_digest": (state.get("frozen_semantic_contract") or {}).get("semantic_digest"),
        "goal_source": "frozen_semantic_contract",
        "level": level,
        "goal": str(state.get("current_user_input") or "test plan"),
        "goals": deepcopy(goals),
        "tasks": deepcopy(tasks or []),
        "steps": deepcopy(steps or []),
        "created_turn": int(state.get("turn_index") or 0),
        "reasons": ["test_authoritative_plan"],
        "validation": {"status": "ACCEPTED", "dispatch_allowed": True},
    }
    definition = freeze_plan_definition(plan, plan_definition_id="plan-definition:test-authority")
    run = create_plan_run(definition, turn_index=int(state.get("turn_index") or 0))
    projection = project_grounded_execution_plan(definition=definition, plan_run=run)
    state["frozen_plan_definition"] = definition
    state["plan_run"] = run
    state["grounded_execution_plan"] = projection
    return projection


def test_requested_effect_fixture_is_bound_to_exact_registered_contract() -> None:
    assert requested_effect_for_tool("list_orders") == {
        "domain": "order",
        "operation": "list",
        "object_type": "order",
        "raw_description": "test exact effect for list_orders",
    }


def test_authoritative_plan_fixture_installs_definition_run_and_projection() -> None:
    state = {"turn_index": 1, "current_user_input": "查订单"}
    install_test_semantic_contract(state, {
        "turn": 1,
        "user_text": "查订单",
        "goals": [{
            "goal_id": "g1",
            "description": "查订单",
            "evidence_span": "查订单",
            "requested_effect": requested_effect_for_tool("list_orders"),
            "required": True,
            "depends_on": [],
        }],
    })
    projection = install_test_plan_authority(
        state,
        goals=[{"goal_id": "g1", "required": True}],
    )

    assert state["frozen_plan_definition"]["immutable"] is True
    assert state["plan_run"]["plan_definition_id"] == state["frozen_plan_definition"]["plan_definition_id"]
    assert projection["authority"] == "compatibility_projection_from_frozen_definition_and_plan_run"
