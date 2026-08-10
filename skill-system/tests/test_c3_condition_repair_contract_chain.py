from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services/agent-service/src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from agent_core.lifecycle.dialogue_runtime import _workflow_repair_allowed_tools
from agent_core.lifecycle.tool_execution_runtime import _tool_result_message
from agent_core.runtime import answer_release_alignment as alignment
from agent_core.runtime import capability_gate


class C3ConditionRepairContractChainTests(unittest.TestCase):
    @staticmethod
    def _formal_goal() -> dict:
        return {
            "goal_id": "g1",
            "condition": {
                "version": "condition-expression@1",
                "op": "eq",
                "left": {"source": "target_fact", "path": "delivery_status"},
                "right": {"source": "literal", "value": "运输中"},
            },
            "requested_effect": {
                "domain": "logistics",
                "operation": "query",
                "object_type": "order",
            },
        }

    @staticmethod
    def _good_parameterization() -> dict:
        return {
            "bindings": [{
                "kind": "condition",
                "source_span": "哪些还在路上",
                "parameter_path": "query.delivery_status",
                "normalized_value": "运输中",
                "actual_value": "运输中",
                "status": "covered",
            }],
            "parameterization_complete": True,
            "errors": [],
        }

    def test_rejected_condition_candidate_survives_tool_message_and_repair_frontier(self) -> None:
        """The C2 rejection must remain actionable to the next bounded model turn."""
        with patch.object(capability_gate, "semantic_goals", return_value=[self._formal_goal()]):
            formal = capability_gate._formal_goal_condition_coverage_proof(
                {},
                goal_ids={"g1"},
                parameterization={
                    "bindings": [],
                    "parameterization_complete": True,
                    "errors": [],
                },
            )

        self.assertTrue(formal["required"])
        self.assertFalse(formal["complete"])
        self.assertEqual(
            formal["errors"],
            ["formal_goal_condition_unbound:g1:delivery_status"],
        )

        match_proof = {
            "candidate_tool": "get_order_logistics",
            "parameterization_complete": True,
            "formal_goal_condition_coverage": formal,
        }
        rejection = {
            "ok": False,
            "code": "CAPABILITY_PARAMETERIZATION_INCOMPLETE",
            "message": "当前请求中的决定性条件没有被完整绑定到正式参数，系统不会用更宽泛查询代替。",
            "data": {"match_proof": match_proof},
            "match_proof": match_proof,
            "execution_permit": None,
        }
        call = {
            "id": "call-broad-logistics",
            "name": "get_order_logistics",
            "args": {"target": {"mode": "collection", "left_handle": "h_result:orders"}},
        }
        message = _tool_result_message(call, rejection)
        self.assertIsNotNone(message)
        transported = json.loads(str(message.content))
        self.assertEqual(transported["code"], "CAPABILITY_PARAMETERIZATION_INCOMPLETE")
        transported_formal = transported["match_proof"]["formal_goal_condition_coverage"]
        self.assertFalse(transported_formal["complete"])
        self.assertEqual(
            transported_formal["requirements"][0]["condition_path"],
            "delivery_status",
        )

        allowed = _workflow_repair_allowed_tools(
            policy_frontier={"get_order_logistics"},
            completion_tools=set(),
            unsupported_tools=set(),
        )
        self.assertIn("get_order_logistics", allowed)
        self.assertIn("ask_user_clarification", allowed)
        self.assertNotIn("respond_to_user", allowed)

    def test_repaired_candidate_releases_only_after_backend_condition_execution(self) -> None:
        """A corrected candidate still needs backend execution evidence before release."""
        with patch.object(capability_gate, "semantic_goals", return_value=[self._formal_goal()]):
            formal = capability_gate._formal_goal_condition_coverage_proof(
                {},
                goal_ids={"g1"},
                parameterization=self._good_parameterization(),
            )

        self.assertTrue(formal["required"])
        self.assertTrue(formal["complete"])
        self.assertEqual(
            formal["checks"][0]["matched_parameter_path"],
            "query.delivery_status",
        )

        proof = {
            "candidate_tool": "get_order_logistics",
            "parameterization_complete": True,
            "formal_goal_condition_coverage": formal,
        }
        evidence = [{
            "evidence_kind": "current_tool_parameterization",
            "tool_name": "get_order_logistics",
            "ok": True,
            "parameterization": {
                "required_backend_conditions": {"delivery_status": "运输中"},
                "backend_applied_conditions": {"delivery_status": "运输中"},
                "source_population_count": 4,
                "matched_population_count": 1,
                "presentation_population": "matched_members",
            },
        }]
        blocks = [{
            "contract_id": "commerce.logistics_overview@1",
            "items": [{"order_id": "10001"}],
        }]

        with patch.object(alignment, "_formal_goals", return_value=[]), patch.object(
            alignment, "_effective_match_proofs", return_value=[proof]
        ), patch.object(alignment, "_runtime_evidence", return_value=[]):
            missing = alignment._deterministic_verdict(result={}, blocks=blocks)
        self.assertEqual(missing.decision, "reject")
        self.assertEqual(
            missing.reason_code,
            "required_condition_execution_evidence_missing",
        )

        with patch.object(alignment, "_formal_goals", return_value=[]), patch.object(
            alignment, "_effective_match_proofs", return_value=[proof]
        ), patch.object(alignment, "_runtime_evidence", return_value=evidence):
            passed = alignment._deterministic_verdict(result={}, blocks=blocks)
        self.assertEqual(passed.decision, "pass")
        self.assertEqual(passed.reason_code, "deterministic_evidence_complete")

    def test_backend_condition_mismatch_remains_fail_closed_after_candidate_repair(self) -> None:
        """Correct model bindings cannot override a backend that applied another scope."""
        with patch.object(capability_gate, "semantic_goals", return_value=[self._formal_goal()]):
            formal = capability_gate._formal_goal_condition_coverage_proof(
                {},
                goal_ids={"g1"},
                parameterization=self._good_parameterization(),
            )

        proof = {
            "candidate_tool": "get_order_logistics",
            "parameterization_complete": True,
            "formal_goal_condition_coverage": formal,
        }
        evidence = [{
            "evidence_kind": "current_tool_parameterization",
            "tool_name": "get_order_logistics",
            "ok": True,
            "parameterization": {
                "required_backend_conditions": {"delivery_status": "运输中"},
                "backend_applied_conditions": {},
                "source_population_count": 4,
                "matched_population_count": 4,
                "presentation_population": "source_members",
            },
        }]

        with patch.object(alignment, "_formal_goals", return_value=[]), patch.object(
            alignment, "_effective_match_proofs", return_value=[proof]
        ), patch.object(alignment, "_runtime_evidence", return_value=evidence):
            verdict = alignment._deterministic_verdict(result={}, blocks=[])
        self.assertEqual(verdict.decision, "reject")
        self.assertEqual(verdict.reason_code, "backend_condition_execution_mismatch")


if __name__ == "__main__":
    unittest.main()
