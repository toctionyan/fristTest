from __future__ import annotations

import ast
from pathlib import Path

from agent_core.lifecycle.state_contracts import (
    NODE_ALLOWED_GROUPS,
    validate_state_schema_coverage,
    validate_state_update,
)


def test_finalize_turn_may_record_non_authoritative_decision_chain_debug() -> None:
    violations = validate_state_update(
        "finalize_turn",
        {
            "current_final_answer": "您好。",
            "conversation_event_log": [],
            "audit_snapshot": [],
            "phase": "done",
            "status": "GroundingRequired",
            "decision_chain": [
                {
                    "stage": "finalize_turn",
                    "decision": "immutable_turn_audit_committed",
                    "details": {},
                }
            ],
        },
    )

    assert violations == []


def test_retired_workflow_projection_cannot_be_written_by_any_node() -> None:
    for node in ("agent_loop", "validate_and_execute", "finalize_turn", "action_gateway"):
        assert validate_state_update(node, {"workflow_plan": {}}) == [
            {"type": "retired_state_key", "node": node, "key": "workflow_plan"}
        ]


def test_strict_agent_loop_accepts_declared_model_governance_state_only() -> None:
    assert validate_state_update(
        "agent_loop",
        {
            "model_call_trace": [{"purpose": "agent_loop"}],
            "model_call_budget": {"max_calls": 8},
        },
    ) == []
    assert validate_state_update("agent_loop", {"undeclared_model_state": {}}) == [
        {
            "type": "unclassified_state_key",
            "node": "agent_loop",
            "key": "undeclared_model_state",
        }
    ]


def test_every_typed_state_key_has_one_contract_group() -> None:
    assert validate_state_schema_coverage() == []


def test_every_actual_graph_node_has_exactly_one_strict_contract() -> None:
    graph_path = Path(__file__).resolve().parents[2] / "src/agent_core/lifecycle/graph.py"
    tree = ast.parse(graph_path.read_text(encoding="utf-8"))
    node_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "nodes" for target in node.targets
        ) and isinstance(node.value, ast.Dict):
            node_names = {
                str(key.value)
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    assert node_names
    assert set(NODE_ALLOWED_GROUPS) == node_names


def test_formal_execution_nodes_may_record_disposition_but_not_workflow() -> None:
    formal_update = {
        "execution_dispositions": [{"disposition": "gateway"}],
        "latest_execution_disposition": {"disposition": "gateway"},
        "runtime_outcome": {"outcome_type": "draft_created"},
        "tool_trace": [],
        "decision_chain": [],
    }
    for node in ("action_gateway", "commit_action", "reconcile_submission"):
        assert validate_state_update(node, formal_update) == []
        assert validate_state_update(node, {"workflow_plan": {}}) == [
            {"type": "retired_state_key", "node": node, "key": "workflow_plan"}
        ]
