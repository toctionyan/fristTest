from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPTS = ROOT / "scripts"
for entry in (str(CONTROL), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import autonomous_repair_router as router  # noqa: E402
import github_repair_authority as authority  # noqa: E402


CONTROL_PATH = "scripts/verify_engineering_bounded_autonomy_closure.py"
INCIDENT_LOG = """
FAIL: test_current_bounded_autonomy_chain_closes_routine_clicks (test_engineering_bounded_autonomy_closure.EngineeringBoundedAutonomyClosureTests.test_current_bounded_autonomy_chain_closes_routine_clicks)
Traceback (most recent call last):
AssertionError: 'FAIL' != 'PASS'
FAILED (failures=2)
"""


class ControlPlaneRepairAuthorityTests(unittest.TestCase):
    def _control_failure(self):
        route = router.validate_route(
            router.route_failure(
                workflow_name="quality",
                conclusion="failure",
                legacy_classification="unknown_failure_without_gate_evidence",
                combined_text=INCIDENT_LOG,
                failed_gates=[],
                legacy_candidate_paths=[],
                source_changed_files=[
                    CONTROL_PATH,
                    "skill-system/tests/test_engineering_bounded_autonomy_closure.py",
                ],
                same_repository=True,
            )
        )
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
            "repair_domain": router.REPAIR_DOMAIN_CONTROL_PLANE,
            "repair_route": route,
            "repair_allowed": True,
            "same_repository": True,
            "candidate_paths": [CONTROL_PATH],
            "failed_gates": [
                {
                    "gate_id": "skill-control-plane",
                    "status": "FAIL",
                    "category": "control-plane-implementation",
                    "owner": "skill-control-plane",
                    "failure_kind": "control_plane_implementation",
                }
            ],
            "production_closed": False,
        }

    def _rca(self, failure, *, paths=None):
        paths = list(paths or failure["candidate_paths"])
        rca = {
            "schema": authority.RCA_SCHEMA,
            "binding": authority.failure_binding(failure),
            "failure_case_sha256": authority.failure_case_fingerprint(failure),
            "candidate_paths": list(failure["candidate_paths"]),
            "repair_domain": failure["repair_domain"],
            "failure_class": "control-plane verifier implementation defect",
            "violated_invariant": "machine verifier must reflect workflow semantics",
            "authority_owner": "engineering verifier implementation",
            "drifted_projection": CONTROL_PATH,
            "root_cause": "implementation interpreted workflow source text too literally",
            "existing_gate_gap": "semantic verifier implementation was incorrect",
            "required_permanent_guard": "existing skill-control-plane test remains mandatory",
            "repair_plan": ["repair verifier implementation only", "rerun existing guard"],
            "write_scope_recommendation": {"decision": "GRANT", "paths": paths},
            "read_only": True,
            "workspace_mutated": False,
            "production_closed": False,
        }
        rca["rca_sha256"] = authority.rca_fingerprint(rca)
        return rca

    def test_control_failure_compiles_exact_implementation_only_grant(self) -> None:
        failure = self._control_failure()
        rca = self._rca(failure)
        grant = authority.compile_write_grant(
            failure_case=failure,
            rca=rca,
            candidate_paths=failure["candidate_paths"],
        )
        self.assertEqual(grant["repair_domain"], router.REPAIR_DOMAIN_CONTROL_PLANE)
        self.assertEqual(grant["allowed_paths"], [CONTROL_PATH])
        self.assertEqual(
            authority.validate_write_grant(
                grant,
                failure_case=failure,
                rca=rca,
                candidate_paths=failure["candidate_paths"],
            ),
            (CONTROL_PATH,),
        )
        self.assertTrue(grant["authority"]["write_authority"])
        self.assertFalse(grant["authority"]["test_authority"])
        self.assertFalse(grant["authority"]["goal_authority"])
        self.assertFalse(grant["production_closed"])

    def test_control_route_domain_tamper_is_rejected(self) -> None:
        failure = self._control_failure()
        failure["repair_domain"] = router.REPAIR_DOMAIN_PRODUCT
        rca = self._rca({**failure, "repair_domain": router.REPAIR_DOMAIN_PRODUCT})
        with self.assertRaises(authority.RepairAuthorityError):
            authority.compile_write_grant(
                failure_case=failure,
                rca=rca,
                candidate_paths=failure["candidate_paths"],
            )

    def test_test_path_cannot_enter_control_write_grant(self) -> None:
        failure = self._control_failure()
        test_path = "skill-system/tests/test_engineering_bounded_autonomy_closure.py"
        rca = self._rca(failure, paths=[test_path])
        with self.assertRaises(authority.RepairAuthorityError):
            authority.compile_write_grant(
                failure_case=failure,
                rca=rca,
                candidate_paths=failure["candidate_paths"],
            )

    def test_legacy_product_failure_still_uses_product_domain(self) -> None:
        path = "services/agent-service/src/agent_core/example.py"
        failure = {
            "schema": "github-failure-ingest@1",
            "status": "INGESTED",
            "repository": "toctionyan/fristTest",
            "workflow_run_id": "1",
            "workflow_run_attempt": "1",
            "head_sha": "b" * 40,
            "failure_signature": "e" * 64,
            "classification": "code_or_contract",
            "repair_allowed": True,
            "candidate_paths": [path],
            "failed_gates": [{"gate_id": "python-test-suites", "status": "FAIL"}],
        }
        rca = {
            "schema": authority.RCA_SCHEMA,
            "binding": authority.failure_binding(failure),
            "failure_case_sha256": authority.failure_case_fingerprint(failure),
            "candidate_paths": [path],
            "failure_class": "product",
            "violated_invariant": "x",
            "authority_owner": "x",
            "drifted_projection": path,
            "root_cause": "x",
            "existing_gate_gap": "x",
            "required_permanent_guard": "x",
            "repair_plan": ["fix product"],
            "write_scope_recommendation": {"decision": "GRANT", "paths": [path]},
            "read_only": True,
            "workspace_mutated": False,
            "production_closed": False,
        }
        rca["rca_sha256"] = authority.rca_fingerprint(rca)
        grant = authority.compile_write_grant(
            failure_case=failure,
            rca=rca,
            candidate_paths=[path],
        )
        self.assertEqual(grant["repair_domain"], router.REPAIR_DOMAIN_PRODUCT)


if __name__ == "__main__":
    unittest.main()
