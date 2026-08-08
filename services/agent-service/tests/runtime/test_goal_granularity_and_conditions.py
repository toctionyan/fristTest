from __future__ import annotations

from agent_core.lifecycle.condition_expression import (
    condition_goal_dependencies,
    normalize_condition_expression,
)
from agent_core.lifecycle.goal_planning import validate_goal_declaration


class _VerdictVerifier:
    def __init__(self, verdict: str):
        self.verdict = verdict

    def verify(self, *, user_text, goals):
        return {
            "verdict": self.verdict,
            "reason_code": f"test_{self.verdict}",
            "findings": [],
        }


class _OverSplitVerifier:
    def verify(self, *, user_text, goals):
        return {
            "verdict": "over_split",
            "reason_code": "implementation_steps_promoted_to_goals",
            "findings": [
                {
                    "goal_id": "g1",
                    "reason": "permission check is not a user outcome",
                    "recommended_role": "capability_precondition",
                    "evidence_span": "申请退款",
                }
            ],
        }


def test_legacy_condition_is_frozen_as_closed_ast() -> None:
    condition = normalize_condition_expression(
        {
            "source_goal_id": "g1",
            "output_path": "eligible",
            "operator": "eq",
            "value": True,
        },
        known_goal_ids={"g1", "g2"},
    )

    assert condition["op"] == "eq"
    assert condition["left"] == {
        "source": "goal_output",
        "goal_id": "g1",
        "path": "eligible",
    }
    assert condition["right"] == {"source": "literal", "value": True}
    assert condition_goal_dependencies(condition) == {"g1"}


def test_condition_goal_output_dependency_must_be_declared() -> None:
    from tests.runtime.test_pretool_execution_policy import _registry

    state = {
        "current_user_input": "能退的话就申请",
        "turn_index": 1,
    }
    result, plan = validate_goal_declaration(
        state=state,
        capability_registry=_registry(),
        args={
            "summary": "conditional refund",
            "goals": [
                {
                    "goal_id": "g1",
                    "description": "评估退款资格",
                    "evidence_span": "能退",
                    "requested_effect": {
                        "domain": "refund",
                        "operation": "evaluate_eligibility",
                        "object_type": "order",
                    },
                    "expected_result_cardinality": "single",
                    "required": True,
                    "depends_on": [],
                },
                {
                    "goal_id": "g2",
                    "description": "准备退款",
                    "evidence_span": "申请",
                    "requested_effect": {
                        "domain": "refund",
                        "operation": "prepare",
                        "object_type": "refund",
                    },
                    "expected_result_cardinality": "single",
                    "required": True,
                    "depends_on": [],
                    "condition": {
                        "op": "eq",
                        "left": {"source": "goal_output", "goal_id": "g1", "path": "eligible"},
                        "right": {"source": "literal", "value": True},
                    },
                },
            ],
        },
    )

    assert result["ok"] is False
    assert any("condition_dependency_not_declared:g2:g1" in value for value in result["data"]["errors"])
    assert plan is None


def test_granularity_verifier_blocks_over_split_before_capability_discovery() -> None:
    from tests.runtime.test_pretool_execution_policy import _registry

    state = {
        "current_user_input": "申请退款",
        "turn_index": 1,
        "goal_granularity_verifier": _OverSplitVerifier(),
    }
    result, plan = validate_goal_declaration(
        state=state,
        capability_registry=_registry(),
        args={
            "summary": "over split",
            "goals": [
                {
                    "goal_id": "g1",
                    "description": "验证订单权限",
                    "evidence_span": "申请退款",
                    "requested_effect": {
                        "domain": "order",
                        "operation": "verify_ownership",
                        "object_type": "order",
                    },
                    "expected_result_cardinality": "single",
                    "required": True,
                    "depends_on": [],
                }
            ],
        },
    )

    assert result["ok"] is False
    assert result["code"] == "GOAL_DECLARATION_OVER_SPLIT"
    assert result["data"]["granularity_proof"]["verdict"] == "over_split"
    assert plan is None


def test_condition_rejects_unsupported_operator_and_unsafe_path() -> None:
    import pytest

    with pytest.raises(ValueError, match="condition_operator_invalid"):
        normalize_condition_expression(
            {
                "op": "execute",
                "left": {"source": "literal", "value": True},
                "right": {"source": "literal", "value": True},
            },
            known_goal_ids={"g1"},
        )

    with pytest.raises(ValueError, match="condition_operand_path_invalid"):
        normalize_condition_expression(
            {
                "op": "exists",
                "left": {
                    "source": "goal_output",
                    "goal_id": "g1",
                    "path": "__class__.__mro__",
                },
            },
            known_goal_ids={"g1"},
        )


def test_condition_rejects_unknown_goal_and_excessive_depth() -> None:
    import pytest

    with pytest.raises(ValueError, match="condition_unknown_goal:g9"):
        normalize_condition_expression(
            {
                "op": "exists",
                "left": {"source": "goal_output", "goal_id": "g9", "path": "eligible"},
            },
            known_goal_ids={"g1"},
        )

    nested = {
        "op": "not",
        "args": [{
            "op": "not",
            "args": [{
                "op": "not",
                "args": [{
                    "op": "not",
                    "args": [{
                        "op": "not",
                        "args": [{
                            "op": "exists",
                            "left": {"source": "goal_output", "goal_id": "g1", "path": "eligible"},
                        }],
                    }],
                }],
            }],
        }],
    }
    with pytest.raises(ValueError, match="condition_expression_depth_exceeded"):
        normalize_condition_expression(nested, known_goal_ids={"g1"})


def test_granularity_under_split_and_mixed_are_distinct_fail_closed_results() -> None:
    from tests.runtime.test_pretool_execution_policy import _registry

    base_args = {
        "summary": "combined",
        "goals": [
            {
                "goal_id": "g1",
                "description": "处理订单相关事项",
                "evidence_span": "查物流并开发票",
                "requested_effect": {
                    "domain": "order",
                    "operation": "handle_related",
                    "object_type": "order",
                },
                "expected_result_cardinality": "unknown",
                "required": True,
                "depends_on": [],
            }
        ],
    }
    expected = {
        "under_split": "GOAL_DECLARATION_UNDER_SPLIT",
        "mixed": "GOAL_DECLARATION_GRANULARITY_MIXED",
        "indeterminate": "GOAL_GRANULARITY_UNVERIFIED",
    }
    for verdict, code in expected.items():
        state = {
            "current_user_input": "查物流并开发票",
            "turn_index": 1,
            "goal_granularity_verifier": _VerdictVerifier(verdict),
        }
        result, plan = validate_goal_declaration(
            state=state,
            capability_registry=_registry(),
            args=base_args,
        )
        assert result["ok"] is False
        assert result["code"] == code
        assert plan is None
