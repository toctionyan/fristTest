from __future__ import annotations

from copy import deepcopy

from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.lifecycle.goal_lifecycle import apply_semantic_contract_to_goal_records
from agent_core.lifecycle.goal_planning import validate_goal_declaration
from agent_core.lifecycle.protocol import DECLARE_TURN_GOALS_SCHEMA
from agent_core.lifecycle.semantic_contract import (
    freeze_semantic_contract,
    semantic_contract_ready,
)
from agent_core.lifecycle.workflow_runtime import (
    build_workflow_plan,
    validate_grounded_execution_plan,
)


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


def _registry() -> CapabilityRegistry:
    return CapabilityRegistry([], allow_empty=True)


def _state(user_text: str) -> dict:
    return {
        "current_user_input": user_text,
        "turn_index": 5,
        "goal_alignment_verifier": _ExactGoalVerifier(),
        "goal_records": [
            {
                "goal_id": "goal-invoice-old",
                "description": "给杯子开票",
                "requested_effect": {
                    "domain": "invoice",
                    "operation": "create",
                    "object_type": "order",
                },
                "lifecycle": "ACTIVE",
                "revision": 3,
                "created_turn": 1,
                "updated_turn": 4,
            },
            {
                "goal_id": "goal-refund-old",
                "description": "给键盘退款",
                "requested_effect": {
                    "domain": "refund",
                    "operation": "create",
                    "object_type": "order",
                },
                "lifecycle": "ACTIVE",
                "revision": 2,
                "created_turn": 2,
                "updated_turn": 4,
            },
        ],
        "focus_state": {"focused_goal_id": "goal-refund-old", "revision": 2},
    }


def _args(*, goal_changes=None, focus_change=None) -> dict:
    payload = {
        "summary": "继续处理当前请求",
        "goals": [
            {
                "goal_id": "goal-current-query",
                "description": "继续处理当前请求",
                "evidence_span": "继续处理",
                "requested_effect": {
                    "domain": "conversation",
                    "operation": "continue_request",
                    "object_type": "goal",
                },
                "expected_result_cardinality": "none",
                "required": True,
                "depends_on": [],
            }
        ],
    }
    if goal_changes is not None:
        payload["goal_changes"] = goal_changes
    if focus_change is not None:
        payload["focus_change"] = focus_change
    return payload


def test_goal_change_schema_is_closed_and_discriminated() -> None:
    properties = DECLARE_TURN_GOALS_SCHEMA["function"]["parameters"]["properties"]
    goal_change_items = properties["goal_changes"]["items"]
    focus_change = properties["focus_change"]

    assert "oneOf" in goal_change_items
    assert "oneOf" in focus_change
    assert all(row.get("additionalProperties") is False for row in goal_change_items["oneOf"])
    assert all(row.get("additionalProperties") is False for row in focus_change["oneOf"])


def test_goal_change_rejects_missing_literal_evidence() -> None:
    state = _state("杯子先别管，继续处理键盘")
    result, plan = validate_goal_declaration(
        state=state,
        args=_args(
            goal_changes=[
                {
                    "operation": "SET_GOAL_LIFECYCLE",
                    "goal_id": "goal-invoice-old",
                    "expected_revision": 3,
                    "from": "ACTIVE",
                    "to": "PAUSED",
                    "evidence_span": "用户没有说过的片段",
                }
            ]
        ),
        capability_registry=_registry(),
    )

    assert result["ok"] is False
    assert plan is None
    assert "goal_change_evidence_not_in_current_turn:goal-invoice-old" in result["data"]["errors"]


def test_goal_change_rejects_stale_revision_and_wrong_from_state() -> None:
    state = _state("杯子先别管，继续处理键盘")
    result, plan = validate_goal_declaration(
        state=state,
        args=_args(
            goal_changes=[
                {
                    "operation": "SET_GOAL_LIFECYCLE",
                    "goal_id": "goal-invoice-old",
                    "expected_revision": 2,
                    "from": "BLOCKED",
                    "to": "PAUSED",
                    "evidence_span": "杯子先别管",
                }
            ]
        ),
        capability_registry=_registry(),
    )

    assert result["ok"] is False
    assert plan is None
    errors = result["data"]["errors"]
    assert "goal_revision_conflict:goal-invoice-old:expected=2:actual=3" in errors
    assert "goal_lifecycle_from_mismatch:goal-invoice-old:expected=BLOCKED:actual=ACTIVE" in errors


def test_patch_goal_cannot_replace_requested_effect() -> None:
    state = _state("不是退款，改成开票，继续处理")
    result, plan = validate_goal_declaration(
        state=state,
        args=_args(
            goal_changes=[
                {
                    "operation": "PATCH_GOAL",
                    "goal_id": "goal-refund-old",
                    "expected_revision": 2,
                    "evidence_span": "不是退款，改成开票",
                    "patch": {
                        "requested_effect": {
                            "domain": "invoice",
                            "operation": "create",
                            "object_type": "order",
                        }
                    },
                }
            ]
        ),
        capability_registry=_registry(),
    )

    assert result["ok"] is False
    assert plan is None
    assert "goal_patch_forbidden_field:goal-refund-old:requested_effect" in result["data"]["errors"]


def test_valid_pause_change_increments_goal_revision() -> None:
    state = _state("杯子先别管，继续处理键盘")
    result, plan = validate_goal_declaration(
        state=state,
        args=_args(
            goal_changes=[
                {
                    "operation": "SET_GOAL_LIFECYCLE",
                    "goal_id": "goal-invoice-old",
                    "expected_revision": 3,
                    "from": "ACTIVE",
                    "to": "PAUSED",
                    "evidence_span": "杯子先别管",
                }
            ]
        ),
        capability_registry=_registry(),
    )

    assert result["ok"] is True
    assert plan is not None
    contract = plan["_frozen_semantic_contract"]
    updated = apply_semantic_contract_to_goal_records(state["goal_records"], contract, turn=5)
    invoice = next(row for row in updated if row["goal_id"] == "goal-invoice-old")
    assert invoice["lifecycle"] == "PAUSED"
    assert invoice["revision"] == 4
    assert invoice["last_change_evidence_span"] == "杯子先别管"


def test_focus_change_rejects_unknown_goal_and_stale_revision() -> None:
    state = _state("继续处理键盘")
    result, plan = validate_goal_declaration(
        state=state,
        args=_args(
            focus_change={
                "operation": "SET_GOAL_FOCUS",
                "goal_id": "goal-missing",
                "expected_revision": 1,
                "evidence_span": "键盘",
            }
        ),
        capability_registry=_registry(),
    )

    assert result["ok"] is False
    assert plan is None
    errors = result["data"]["errors"]
    assert "unknown_goal_for_focus:goal-missing" in errors
    assert "focus_revision_conflict:expected=1:actual=2" in errors


def test_valid_goal_focus_change_is_normalized_and_versioned() -> None:
    state = _state("继续处理键盘")
    result, plan = validate_goal_declaration(
        state=state,
        args=_args(
            focus_change={
                "operation": "SET_GOAL_FOCUS",
                "goal_id": "goal-refund-old",
                "expected_revision": 2,
                "evidence_span": "键盘",
            }
        ),
        capability_registry=_registry(),
    )

    assert result["ok"] is True
    assert plan is not None
    focus_change = plan["_frozen_semantic_contract"]["focus_change"]
    assert focus_change["operation"] == "SET_GOAL_FOCUS"
    assert focus_change["validated_against_focus_revision"] == 2
    assert focus_change["next_focus_revision"] == 3


def _contract() -> dict:
    return freeze_semantic_contract(
        turn=8,
        user_text="给键盘退款",
        summary="退款",
        goals=[
            {
                "goal_id": "goal-refund",
                "description": "给键盘退款",
                "evidence_span": "给键盘退款",
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


def test_semantic_contract_rejects_content_tampering_with_old_digest() -> None:
    contract = _contract()
    tampered = deepcopy(contract)
    tampered["goals"][0]["requested_effect"]["operation"] = "cancel"

    assert semantic_contract_ready({"frozen_semantic_contract": tampered}) is False


def test_grounded_plan_rejects_tampered_semantic_contract_content() -> None:
    contract = _contract()
    plan = build_workflow_plan(
        state={"frozen_semantic_contract": contract, "turn_index": 8},
        turn_plan={"effects": []},
        user_text="给键盘退款",
    )
    tampered = deepcopy(contract)
    tampered["goals"][0]["requested_effect"]["operation"] = "cancel"

    validation = validate_grounded_execution_plan(plan=plan, semantic_contract=tampered)

    assert validation["status"] == "REJECTED"
    assert any(row["code"] == "SEMANTIC_CONTRACT_DIGEST_INVALID" for row in validation["errors"])


def test_goal_change_cannot_apply_after_concurrent_revision_advance() -> None:
    state = _state("杯子先别管，继续处理键盘")
    result, plan = validate_goal_declaration(
        state=state,
        args=_args(
            goal_changes=[
                {
                    "operation": "SET_GOAL_LIFECYCLE",
                    "goal_id": "goal-invoice-old",
                    "expected_revision": 3,
                    "from": "ACTIVE",
                    "to": "PAUSED",
                    "evidence_span": "杯子先别管",
                }
            ]
        ),
        capability_registry=_registry(),
    )
    assert result["ok"] is True and plan is not None
    concurrent = deepcopy(state["goal_records"])
    next(row for row in concurrent if row["goal_id"] == "goal-invoice-old")["revision"] = 4

    import pytest

    with pytest.raises(ValueError, match="goal_revision_conflict"):
        apply_semantic_contract_to_goal_records(
            concurrent,
            plan["_frozen_semantic_contract"],
            turn=5,
        )


def test_focus_change_apply_rechecks_revision_and_sets_only_one_focus_kind() -> None:
    from agent_core.lifecycle.semantic_state_changes import apply_focus_change

    change = {
        "operation": "SET_GOAL_FOCUS",
        "goal_id": "goal-refund-old",
        "expected_revision": 2,
        "validated_against_focus_revision": 2,
        "next_focus_revision": 3,
        "evidence_span": "键盘",
        "evidence_turn": 5,
    }
    updated = apply_focus_change(
        {"focused_interaction_id": "draft:old", "revision": 2},
        change,
        turn=5,
    )
    assert updated == {
        "revision": 3,
        "updated_turn": 5,
        "last_change_operation": "SET_GOAL_FOCUS",
        "last_change_evidence_span": "键盘",
        "focused_goal_id": "goal-refund-old",
    }

    import pytest

    with pytest.raises(ValueError, match="focus_revision_conflict"):
        apply_focus_change({"revision": 3}, change, turn=5)
