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
        module.PYTHON_SUITE_RUNNER,
        module.INGEST_BRIDGE,
        module.QUALITY_TARGET_CREATOR,
        module.ADOPTION_WORKFLOW,
        module.SOLO_WORKFLOW,
    ):
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _mutate_profile(module, isolated: Path, command_id: str, mutate) -> None:
    profile_path = isolated / module.PROFILE
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    row = next(item for item in profile["verification_commands"] if item["id"] == command_id)
    mutate(row)
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ExistingCandidateAdoptionContractTests(unittest.TestCase):
    def test_repository_adoption_contract_is_fail_closed(self) -> None:
        module = _load_module()
        result = module.verify(ROOT)
        self.assertEqual(result["status"], "PASS", result["errors"])
        self.assertEqual(result["schema"], "governed-existing-candidate-adoption-contract@5")
        self.assertEqual(result["source_pr_number"], 1348)
        self.assertEqual(result["exact_blob_count"], 8)
        self.assertFalse(result["candidate_write_authority"])
        self.assertTrue(result["targeted_runtime_import_environment_bound"])
        self.assertTrue(result["canonical_python_suite_authority_bound"])
        self.assertEqual(result["canonical_python_suite_owner"], "scripts/run_python_test_suites.py")
        self.assertTrue(result["python_suite_evidence_outside_candidate"])
        self.assertTrue(result["independent_quick_target_workflow_bound"])
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
            self.assertIn(f"adoption_workflow_marker_missing:{marker}", result["errors"])

    def test_implicit_success_status_on_failure_evidence_is_killed(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-status-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            workflow = isolated / module.ADOPTION_WORKFLOW
            source = workflow.read_text(encoding="utf-8")
            guarded = "failure() && steps.fixed_profile_validation.outcome == 'failure'"
            self.assertEqual(source.count(guarded), 2)
            workflow.write_text(source.replace(guarded, "steps.fixed_profile_validation.outcome == 'failure'", 1), encoding="utf-8")
            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("adoption_failure_status_check_count_drift", result["errors"])

    def test_targeted_runtime_import_environment_drift_is_killed(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-targeted-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            _mutate_profile(
                module,
                isolated,
                "dependency-basis-runtime-regression",
                lambda row: row["argv"].__setitem__(1, "PYTHONPATH=."),
            )
            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["targeted_runtime_import_environment_bound"])
            self.assertIn("profile_targeted_runtime_environment_drift", result["errors"])

    def test_full_suite_cannot_bypass_canonical_runner_with_raw_pytest(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-raw-pytest-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            def mutate(row):
                row["cwd"] = "services/agent-service"
                row["argv"] = ["{agent_python}", "-B", "-m", "pytest", "tests"]
            _mutate_profile(module, isolated, "python-test-suites", mutate)
            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["canonical_python_suite_authority_bound"])
            self.assertIn("profile_python_suite_authority_drift", result["errors"])
            self.assertIn("profile_python_suite_bypasses_canonical_runner", result["errors"])

    def test_full_suite_standard_mode_drift_is_killed(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-suite-mode-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            def mutate(row):
                index = row["argv"].index("standard")
                row["argv"][index] = "integration"
            _mutate_profile(module, isolated, "python-test-suites", mutate)
            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("profile_python_suite_authority_drift", result["errors"])
            self.assertIn("profile_python_suite_not_standard_mode", result["errors"])

    def test_full_suite_evidence_must_stay_outside_candidate(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-suite-evidence-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            def mutate(row):
                index = row["argv"].index("../adoption/profile-runtime/junit")
                row["argv"][index] = ".quality/profile-runtime/junit"
            _mutate_profile(module, isolated, "python-test-suites", mutate)
            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["python_suite_evidence_outside_candidate"])
            self.assertIn("profile_python_suite_evidence_not_external", result["errors"])

    def test_full_suite_interpreter_override_clearance_is_killed(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-suite-override-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            def mutate(row):
                row["argv"].remove("QUALITY_BUSINESS_PYTHON")
            _mutate_profile(module, isolated, "python-test-suites", mutate)
            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("profile_python_suite_authority_drift", result["errors"])
            self.assertIn("profile_python_suite_override_not_cleared:QUALITY_BUSINESS_PYTHON", result["errors"])

    def test_adoption_quick_workflow_registration_removal_is_killed(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-quick-registration-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            creator = isolated / module.QUALITY_TARGET_CREATOR
            source = creator.read_text(encoding="utf-8")
            marker = '    "governed-ci-existing-candidate-adoption": "quick",\n'
            self.assertEqual(source.count(marker), 1)
            creator.write_text(source.replace(marker, "", 1), encoding="utf-8")
            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["independent_quick_target_workflow_bound"])
            self.assertIn("quality_target_adoption_mode_drift", result["errors"])

    def test_adoption_quick_workflow_mode_drift_is_killed(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-quick-mode-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            creator = isolated / module.QUALITY_TARGET_CREATOR
            source = creator.read_text(encoding="utf-8")
            original = '    "governed-ci-existing-candidate-adoption": "quick",'
            self.assertEqual(source.count(original), 1)
            creator.write_text(
                source.replace(
                    original,
                    '    "governed-ci-existing-candidate-adoption": "static",',
                    1,
                ),
                encoding="utf-8",
            )
            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["independent_quick_target_workflow_bound"])
            self.assertIn("quality_target_adoption_mode_drift", result["errors"])

    def test_adoption_quick_workflow_false_identity_is_killed(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-quick-provenance-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            workflow = isolated / module.ADOPTION_WORKFLOW
            source = workflow.read_text(encoding="utf-8")
            marker = "--workflow governed-ci-existing-candidate-adoption"
            self.assertEqual(source.count(marker), 1)
            workflow.write_text(
                source.replace(marker, "--workflow quality-quick", 1),
                encoding="utf-8",
            )
            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["independent_quick_target_workflow_bound"])
            self.assertIn(
                "adoption_workflow_marker_missing:--workflow governed-ci-existing-candidate-adoption",
                result["errors"],
            )

    def test_adoption_quick_gate_contract_drift_is_killed(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory(prefix="existing-candidate-adoption-quick-gates-") as temp:
            isolated = Path(temp)
            _copy_contract_surface(module, isolated)
            creator = isolated / module.QUALITY_TARGET_CREATOR
            source = creator.read_text(encoding="utf-8")
            block = '''    "governed-ci-existing-candidate-adoption": [
        "adversarial-runtime-counterexamples",
        "python-test-suites",
        "frontend-vitest",
        "coverage-baseline",
        "full-lifecycle-canary",
        "product-browser-journey",
    ],'''
            self.assertEqual(source.count(block), 1)
            creator.write_text(
                source.replace(block, '    "governed-ci-existing-candidate-adoption": [],', 1),
                encoding="utf-8",
            )
            result = module.verify(isolated)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["independent_quick_target_workflow_bound"])
            self.assertIn("quality_target_adoption_gate_contract_drift", result["errors"])


if __name__ == "__main__":
    unittest.main()
