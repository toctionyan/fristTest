from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agent_core.lifecycle.context_runtime import prepare_agent_loop_turn_node
from agent_core.lifecycle.goal_planning import goal_plan_ready
from agent_core.lifecycle.semantic_contract import semantic_contract_integrity
from agent_core.lifecycle.state_schema import (
    CURRENT_STATE_SCHEMA_VERSION,
    LegacyStateRestartRequired,
    migrate_checkpoint_state,
)
from agent_core.lifecycle.workflow_runtime import _goal_rows


def _legacy_goal_plan(*, requested_effect: bool = True) -> dict:
    goal = {
        "goal_id": "goal:refund",
        "description": "给机械键盘退款",
        "evidence_span": "给机械键盘退款",
        "goal_type": "action",
        "required": True,
        "depends_on": [],
    }
    if requested_effect:
        goal["requested_effect"] = {
            "domain": "refund",
            "operation": "create",
            "object_type": "order",
            "raw_description": "给机械键盘退款",
        }
    return {
        "version": "turn-goal-plan@1.1",
        "turn": 3,
        "summary": "退款",
        "user_text": "给机械键盘退款",
        "goals": [goal],
    }


def _legacy_pending() -> dict:
    return {
        "version": "pending-clarification@1",
        "clarification_id": "clarification:1",
        "status": "pending",
        "created_turn": 3,
        "missing_kind": "condition",
        "question": "退款原因是什么？",
        "user_request": "给机械键盘退款",
        "suspended_goals": [
            {
                "goal_id": "goal:refund",
                "completion_tool_names": ["prepare_refund"],
            }
        ],
    }


def test_new_turn_uses_schema_v2_and_does_not_generate_retired_fields() -> None:
    update = prepare_agent_loop_turn_node({"turn_index": 0, "artifact_ledger": []})
    assert update["state_schema_version"] == CURRENT_STATE_SCHEMA_VERSION
    assert "turn_goal_plan" not in update
    assert "workflow_plan" not in update
    assert "pending_clarification" not in update
    assert update["legacy_compatibility_metrics"]["legacy_fallback_allowed"] is False
    assert update["state_migration"]["from_version"] == CURRENT_STATE_SCHEMA_VERSION
    assert update["state_migration"]["changed"] is False
    assert update["legacy_compatibility_metrics"]["legacy_checkpoint_migrations"] == 0


def test_safe_legacy_pending_checkpoint_migrates_once_to_goal_records_and_blockers() -> None:
    migrated, report = migrate_checkpoint_state(
        {
            "state_schema_version": 1,
            "turn_index": 3,
            "turn_goal_plan": _legacy_goal_plan(),
            "pending_clarification": _legacy_pending(),
            "goal_records": [],
            "goal_blockers": [],
        }
    )
    assert report["from_version"] == 1
    assert report["to_version"] == 2
    assert "pending_clarification->goal_blockers" in report["migrated_fields"]
    assert "legacy_active_goals->goal_records" in report["migrated_fields"]
    assert migrated["turn_goal_plan"] is None
    assert migrated["workflow_plan"] is None
    assert migrated["pending_clarification"] is None
    assert semantic_contract_integrity(migrated["frozen_semantic_contract"])["ok"] is True
    assert migrated["goal_records"][0]["goal_id"] == "goal:refund"
    assert migrated["goal_blockers"][0]["goal_id"] == "goal:refund"
    assert migrated["goal_blockers"][0]["completion_tool_names"] == ["prepare_refund"]


def test_ambiguous_active_legacy_checkpoint_requires_new_conversation() -> None:
    with pytest.raises(LegacyStateRestartRequired) as exc:
        migrate_checkpoint_state(
            {
                "state_schema_version": 1,
                "turn_index": 3,
                "turn_goal_plan": _legacy_goal_plan(requested_effect=False),
                "pending_clarification": _legacy_pending(),
            }
        )
    assert exc.value.code == "LEGACY_STATE_REQUIRES_RESTART"
    assert exc.value.reason == "legacy_goal_plan_lacks_explicit_semantic_identity"


def test_completed_legacy_turn_projection_is_discarded_without_resurrecting_goal() -> None:
    migrated, report = migrate_checkpoint_state(
        {
            "state_schema_version": 1,
            "turn_index": 3,
            "turn_goal_plan": _legacy_goal_plan(requested_effect=False),
            "goal_records": [],
            "goal_blockers": [],
        }
    )
    assert migrated.get("frozen_semantic_contract") is None
    assert migrated.get("goal_records") in (None, [])
    assert "turn_goal_plan:completed_same_turn_projection" in report["discarded_non_authoritative_fields"]


def test_schema_v2_never_uses_legacy_goal_plan_as_current_semantics() -> None:
    state = {
        "state_schema_version": 2,
        "turn_index": 3,
        "turn_goal_plan": _legacy_goal_plan(),
    }
    assert goal_plan_ready(state) is False
    rows = _goal_rows(
        state=state,
        user_text="给机械键盘退款",
        effects=[{"effect_id": "effect:1", "tool_name": "prepare_refund"}],
    )
    assert rows == []


def test_checkpoint_hydrator_persists_one_time_v2_tombstones() -> None:
    from app.services.checkpoint_hydrator import CheckpointHydrator

    class Graph:
        def __init__(self) -> None:
            self.updated = None

        def get_state(self, config):
            del config
            return SimpleNamespace(
                values={
                    "state_schema_version": 1,
                    "turn_index": 3,
                    "turn_goal_plan": _legacy_goal_plan(),
                    "pending_clarification": _legacy_pending(),
                    "goal_records": [],
                    "goal_blockers": [],
                }
            )

        def update_state(self, config, values):
            self.updated = (config, values)

    graph = Graph()
    hydrator = CheckpointHydrator(
        config_for_request=lambda thread, user, tenant: {"thread": thread, "user": user, "tenant": tenant},
        transactions=object(),
        trace_logger=None,
    )
    values = hydrator.values(graph, thread_id="t1", user_id="u1", tenant_id=None)
    assert values["state_schema_version"] == 2
    assert graph.updated is not None
    assert graph.updated[1]["pending_clarification"] is None
    assert graph.updated[1]["state_schema_version"] == CURRENT_STATE_SCHEMA_VERSION
    assert "turn_index" not in graph.updated[1]

def test_conversation_gateway_returns_typed_restart_instead_of_running_graph() -> None:
    from app.schemas.chat_schema import ChatRequest
    from app.use_cases.conversation_turn import ConversationTurnService

    class Noop:
        def add_message(self, *_args, **_kwargs) -> None:
            return None

        def log_event(self, *_args, **_kwargs) -> None:
            return None

    class Graph:
        invoked = False

        def invoke(self, *_args, **_kwargs):
            self.invoked = True
            raise AssertionError("ambiguous legacy checkpoint must not invoke the graph")

    class Service:
        def __init__(self) -> None:
            self.graph = Graph()
            self.message_store = Noop()
            self.trace_logger = Noop()

        def _claim_or_validate_thread(self, *_args) -> None:
            return None

        @contextmanager
        def _serialized_turn(self, *_args):
            yield {"wait_ms": 0, "assert_valid": lambda: None}

        def _require_graph(self):
            return self.graph

        def _checkpoint_values(self, *_args, **_kwargs):
            raise LegacyStateRestartRequired(
                "legacy_goal_plan_lacks_explicit_semantic_identity",
                details={"goal_id": "legacy:g1"},
            )

    service = Service()
    response = ConversationTurnService(service).chat(
        ChatRequest(
            thread_id="legacy-thread",
            user_id="u001",
            role="customer",
            message="继续",
        ),
        include_debug=True,
    )
    assert response.type == "error"
    assert response.error == "LEGACY_STATE_REQUIRES_RESTART"
    assert response.state == {
        "migration_error": {
            "reason": "legacy_goal_plan_lacks_explicit_semantic_identity",
            "details": {"goal_id": "legacy:g1"},
        }
    }
    assert service.graph.invoked is False

