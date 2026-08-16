from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_existing_candidate_adoption_contract.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_existing_candidate_adoption_contract", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load adoption contract verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _copy_contract_surface(module, destination: Path) -> None:
    for relative in (
        module.PROFILE,
        module.CONTROLLER,
        module.INGEST_BRIDGE,
        module.ADOPTION_WORKFLOW,
        module.SOLO_WORKFLOW,
    ):
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


class ExistingCandidateAdoptionContractTests(unittest.TestCase):
    def test_repository_adoption_contract_is_fail_closed(self) -> None:
        module = _load_module()
        result = module.verify(ROOT)
        self.assertEqual(result["status"], "PASS", result["errors"])
        self.assertEqual(result["schema"], "governed-existing-candidate-adoption-contract@3")
        self.assertEqual(result["source_pr_number"], 1348)
        self.assertEqual(result["exact_blob_count"], 8)
        self.assertFalse(result["candidate_write_authority"])
        self.assertTrue(result["agent_test_import_environment_bound"])
        self.assertEqual(result["agent_test_source_root"], "src")
        self.assertTrue(result["agent_test_user_site_disabled"])
        self.assertTrue(result["failed_profile_evidence_required"])
        self.assertTrue(result["failed_profile_evidence_diagnostic_only"])
        self.assertFalse(result["failed_profile_can_be_converted_to_success"])
        self.assertFalse(result["baseline_before_governance_allowed"])
        self.assertFalse(result["automatic_merge_allowed"])
        self.assertFalse(result["production_closed"])

    def test_missing_failed_profile_artifact_path_is_killed(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-contract-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            workflow = isolated / module.ADOPTION_WORKFLOW
            source = workflow.read_text(encoding="utf-8")
            marker = "governed-ci-existing-candidate-adoption-failure-"
            self.assertEqual(source.count(marker), 1)
            workflow.write_text(source.replace(marker, "REMOVED-FAILURE-ARTIFACT-", 1), encoding="utf-8")

            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn(
                f"adoption_workflow_marker_missing:{marker}",
                result["errors"],
            )

    def test_implicit_success_status_on_failure_evidence_is_killed(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-status-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            workflow = isolated / module.ADOPTION_WORKFLOW
            source = workflow.read_text(encoding="utf-8")
            guarded = "failure() && steps.fixed_profile_validation.outcome == 'failure'"
            unguarded = "steps.fixed_profile_validation.outcome == 'failure'"
            self.assertEqual(source.count(guarded), 2)
            workflow.write_text(source.replace(guarded, unguarded, 1), encoding="utf-8")

            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("adoption_failure_status_check_count_drift", result["errors"])

    def test_runtime_regression_source_import_environment_drift_is_killed(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-import-runtime-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            profile_path = isolated / module.PROFILE
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            row = next(
                item
                for item in profile["verification_commands"]
                if item["id"] == "dependency-basis-runtime-regression"
            )
            self.assertEqual(row["argv"][:4], module.AGENT_TEST_PREFIX)
            row["argv"][1] = "PYTHONPATH=."
            profile_path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["agent_test_import_environment_bound"])
            self.assertIn(
                "profile_agent_test_import_environment_drift:dependency-basis-runtime-regression",
                result["errors"],
            )

    def test_full_python_suite_user_site_isolation_drift_is_killed(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-import-suite-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            profile_path = isolated / module.PROFILE
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            row = next(
                item
                for item in profile["verification_commands"]
                if item["id"] == "python-test-suites"
            )
            self.assertEqual(row["argv"][:4], module.AGENT_TEST_PREFIX)
            row["argv"][2] = "PYTHONNOUSERSITE=0"
            profile_path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["agent_test_import_environment_bound"])
            self.assertIn(
                "profile_agent_test_import_environment_drift:python-test-suites",
                result["errors"],
            )


if __name__ == "__main__":
    unittest.main()
