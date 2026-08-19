from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import github_repair_loop_controller as loop  # noqa: E402


CONTROL_PATH = "scripts/verify_engineering_bounded_autonomy_closure.py"


def control_failure():
    return {
        "schema": "github-failure-ingest@1",
        "status": "INGESTED",
        "repository": "toctionyan/fristTest",
        "workflow_name": "quality",
        "workflow_run_id": "32210402217",
        "workflow_run_attempt": "1",
        "head_sha": "a" * 40,
        "failure_signature": "f" * 64,
        "classification": "control_plane_implementation",
        "repair_domain": "CONTROL_PLANE_IMPLEMENTATION",
        "repair_route": {
            "schema": "engineering-autonomous-repair-route@1",
            "repair_class": "CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE",
            "repair_domain": "CONTROL_PLANE_IMPLEMENTATION",
            "automatic_write_allowed": True,
            "human_required": False,
            "allowed_write_paths": [CONTROL_PATH],
            "test_write_allowed": False,
            "acceptance_write_allowed": False,
            "oracle_write_allowed": False,
            "scope_expansion_allowed": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
            "route_sha256": "b" * 64,
        },
        "repair_allowed": True,
        "same_repository": True,
        "candidate_paths": [CONTROL_PATH],
        "repair_branch": "governed-repair/example",
        "repair_base_branch": "feature/example",
    }


class ControlPlaneOuterRepairLoopTests(unittest.TestCase):
    def test_targeted_skill_failure_routes_same_control_scope(self) -> None:
        targeted = {
            "schema": "github-governed-repair-stage3@2",
            "status": "TARGETED_VALIDATION_FAILED",
            "repair_domain": "CONTROL_PLANE_IMPLEMENTATION",
            "results": [
                {
                    "component": "skill-control-plane",
                    "exit_code": 1,
                    "passed": False,
                    "stdout": "",
                    "stderr": (
                        "FAIL: test_current_bounded_autonomy_chain_closes_routine_clicks "
                        "(test_engineering_bounded_autonomy_closure.EngineeringBoundedAutonomyClosureTests.test_current_bounded_autonomy_chain_closes_routine_clicks)\n"
                        "AssertionError: 'FAIL' != 'PASS'\nFAILED (failures=1)"
                    ),
                }
            ],
        }
        failure_class, paths, _reason = loop.classify_targeted_failure(
            targeted, original_failure=control_failure()
        )
        self.assertEqual(failure_class, "CONTROL_PLANE_IMPLEMENTATION_FAILURE")
        self.assertEqual(paths, [CONTROL_PATH])

    def test_control_feedback_cannot_switch_domain_or_expand_scope(self) -> None:
        original = control_failure()
        feedback = loop._safe_feedback_failure(
            original,
            repair_paths=[CONTROL_PATH],
            repair_round=1,
            verification_attempt=1,
            failure_fingerprint="c" * 64,
        )
        self.assertEqual(feedback["classification"], "control_plane_implementation")
        self.assertEqual(feedback["repair_domain"], "CONTROL_PLANE_IMPLEMENTATION")
        self.assertEqual(feedback["candidate_paths"], [CONTROL_PATH])
        self.assertEqual(
            feedback["loop_feedback"]["failure_class"],
            "CONTROL_PLANE_IMPLEMENTATION_FAILURE",
        )
        self.assertEqual(
            feedback["loop_feedback"]["repair_domain"],
            "CONTROL_PLANE_IMPLEMENTATION",
        )
        self.assertFalse(feedback["loop_feedback"]["scope_expanded"])
        self.assertFalse(feedback["repair_route"]["test_write_allowed"])

    def test_product_feedback_remains_historical_product_class(self) -> None:
        path = "services/agent-service/src/agent_core/example.py"
        original = {
            "schema": "github-failure-ingest@1",
            "status": "INGESTED",
            "classification": "code_or_contract",
            "repair_domain": "PRODUCT_CODE",
            "repair_allowed": True,
            "candidate_paths": [path],
        }
        feedback = loop._safe_feedback_failure(
            original,
            repair_paths=[path],
            repair_round=1,
            verification_attempt=1,
            failure_fingerprint="d" * 64,
        )
        self.assertEqual(feedback["classification"], "code_or_contract")
        self.assertEqual(feedback["repair_domain"], "PRODUCT_CODE")
        self.assertEqual(feedback["loop_feedback"]["failure_class"], "PRODUCT_SOURCE_FAILURE")

    def test_test_oracle_path_never_enters_control_feedback(self) -> None:
        with self.assertRaises(loop.RepairLoopError):
            loop._safe_feedback_failure(
                control_failure(),
                repair_paths=["skill-system/tests/test_engineering_bounded_autonomy_closure.py"],
                repair_round=1,
                verification_attempt=1,
                failure_fingerprint="e" * 64,
            )


if __name__ == "__main__":
    unittest.main()
