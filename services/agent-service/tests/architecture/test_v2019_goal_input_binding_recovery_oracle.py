from __future__ import annotations

from agent_core.lifecycle.protocol import DECLARE_TURN_GOALS_SCHEMA


def test_live_goal_schema_uses_input_bindings_not_raw_dependency_edges() -> None:
    goal_schema = (
        DECLARE_TURN_GOALS_SCHEMA["function"]["parameters"]["properties"]["goals"]["items"]
    )
    properties = goal_schema["properties"]
    required = set(goal_schema["required"])

    assert "input_bindings" in properties
    assert "input_bindings" in required
    assert "depends_on" not in properties
    assert "depends_on" not in required
