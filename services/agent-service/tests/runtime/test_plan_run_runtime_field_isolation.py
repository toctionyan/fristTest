from __future__ import annotations

from copy import deepcopy

from agent_core.lifecycle.plan_execution import (
    create_plan_run,
    freeze_plan_definition,
    revise_plan_run,
)


def _plan() -> dict:
    return {
        "plan_contract_version": "grounded-execution-plan@2",
        "workflow_id": "workflow:test",
        "turn_plan_id": "turn-plan:test",
        "formal_semantic_contract_id": "semantic:test",
        "formal_semantic_digest": "semantic-digest",
        "goal_source": "frozen_semantic_contract",
        "level": "L1_LIGHTWEIGHT_PLAN",
        "goal": "查订单再查物流",
        "created_turn": 1,
        "reasons": [],
        "goals": [
            {"goal_id": "g1", "required": True, "depends_on": []},
            {"goal_id": "g2", "required": True, "depends_on": ["g1"]},
        ],
        "tasks": [],
        "steps": [
            {
                "step_id": "step:1",
                "effect_id": "effect:orders",
                "kind": "observation",
                "tool_name": "list_orders",
                "capability_id": "ecommerce.orders.list",
                "goal_ids": ["g1"],
                "depends_on": [],
                "status": "PLANNED",
                "required": True,
                "verification": {
                    "per_goal": {
                        "g1": {
                            "mapped_requested_effect_identity": "order.query:list",
                            "expected_result_cardinality": "collection",
                            "effect_result_cardinality_hint": "collection",
                            "formal_effect_completion_eligible": True,
                            "goal_cardinality_eligible": True,
                            "goal_completion_eligible": True,
                        }
                    },
                    "goal_effect_roles": {"g1": "completion"},
                    "goal_effect_role": "completion",
                    "goal_completion_eligible_by_goal": {"g1": True},
                },
            }
        ],
    }


def test_runtime_per_goal_verification_does_not_change_frozen_step_identity() -> None:
    initial_plan = _plan()
    definition = freeze_plan_definition(initial_plan, plan_definition_id="plan-definition:test")
    run = create_plan_run(definition, turn_index=1)
    run["step_states"]["effect:orders"].update(
        {
            "status": "SUCCEEDED",
            "verification": {
                "per_goal": {
                    "g1": {
                        "mapped_requested_effect_identity": "order.query:list",
                        "expected_result_cardinality": "collection",
                        "effect_result_cardinality_hint": "collection",
                        "formal_effect_completion_eligible": True,
                        "verified_result_member_count": 3,
                        "goal_cardinality_eligible": True,
                        "goal_completion_eligible": True,
                    }
                },
                "goal_effect_roles": {"g1": "completion"},
                "goal_effect_role": "completion",
                "goal_cardinality_eligible_by_goal": {"g1": True},
                "goal_completion_eligible_by_goal": {"g1": True},
                "verified_by_runtime": True,
                "verified_result_member_count": 3,
            },
        }
    )

    rebuilt_plan = deepcopy(initial_plan)
    rebuilt_plan["steps"][0]["verification"] = {
        **rebuilt_plan["steps"][0]["verification"],
        **run["step_states"]["effect:orders"]["verification"],
    }
    revised_definition = freeze_plan_definition(
        rebuilt_plan,
        plan_definition_id="plan-definition:test",
    )
    revised_run = revise_plan_run(
        previous_definition=definition,
        previous_run=run,
        definition=revised_definition,
        turn_index=1,
    )

    assert revised_definition["definition_digest"] == definition["definition_digest"]
    assert revised_run["inherited_effect_ids"] == ["effect:orders"]
    assert revised_run["step_states"]["effect:orders"]["status"] == "SUCCEEDED"
    frozen_verification = revised_definition["steps"][0]["verification"]
    assert "goal_cardinality_eligible_by_goal" not in frozen_verification
    assert "goal_completion_eligible_by_goal" not in frozen_verification
    assert "verified_result_member_count" not in frozen_verification["per_goal"]["g1"]
    assert "goal_cardinality_eligible" not in frozen_verification["per_goal"]["g1"]
    assert "goal_completion_eligible" not in frozen_verification["per_goal"]["g1"]
    assert frozen_verification["per_goal"]["g1"]["formal_effect_completion_eligible"] is True
