from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from repair_governance import (  # type: ignore
    MANDATORY_CLOSURE_DIMENSIONS,
    compute_diff_review,
    create_permit,
    file_sha256,
    load_chain,
    permit_path_decision,
    validate_begin_ready,
    validate_verification_ready,
    write_closure_matrix,
    write_diff_review,
)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class RepairGovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        _write(self.root / "src/module.py", "def value():\n    return 1\n")
        _write(
            self.root / "tests/test_module.py",
            "import unittest\n\nclass TestModule(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(1, 1)\n",
        )
        _write(self.root / "README.md", "baseline\n")
        self.change_id = "repair-example-001"
        self.case_dir = self.root / "governance/repair-cases" / self.change_id
        self.contract = {
            "schema_version": 1,
            "change_id": self.change_id,
            "target_kind": "repair",
            "allowed_paths": ["src/module.py", "tests/test_module.py", "tests/test_new.py"],
            "forbidden_paths": ["governance/**", ".quality/**"],
            "repair_governance": self.case_dir.relative_to(self.root).as_posix(),
        }
        self._write_prerequisites()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_prerequisites(self, *, root_decision: str = "PROVEN", review_decision: str = "APPROVED") -> None:
        failure = {
            "schema_version": 1,
            "record_type": "failure-case",
            "change_id": self.change_id,
            "classification": "implementation-defect",
            "reproduction": {
                "status": "REPRODUCED",
                "expected": "value 2",
                "actual": "value 1",
                "evidence_refs": ["src/module.py"],
            },
            "violated_invariants": ["value contract"],
            "affected_boundaries": ["module"],
        }
        _write(self.case_dir / "failure-case.json", failure)
        root = {
            "schema_version": 1,
            "record_type": "root-cause-proof",
            "change_id": self.change_id,
            "failure_case_sha256": file_sha256(self.case_dir / "failure-case.json"),
            "decision": root_decision,
            "root_cause": "the implementation returns the obsolete value",
            "causal_chain": ["obsolete literal remains", "caller receives obsolete value"],
            "evidence_refs": ["src/module.py"],
            "rejected_hypotheses": ["the test oracle is wrong"],
            "affected_boundaries": ["module"],
        }
        _write(self.case_dir / "root-cause-proof.json", root)
        tests = {
            "focused": ["module test"],
            "counterexamples": ["unrelated input"],
            "regression": ["full unit suite"],
            "negative_path": ["scope violation"],
        }
        plan = {
            "schema_version": 1,
            "record_type": "repair-plan",
            "change_id": self.change_id,
            "root_cause_proof_sha256": file_sha256(self.case_dir / "root-cause-proof.json"),
            "status": "PROPOSED",
            "strategy": "replace the obsolete value and add direct regression coverage",
            "changes": [
                {"path": "src/module.py", "responsibility": "runtime value", "reason": "root cause"},
                {"path": "tests/test_module.py", "responsibility": "regression", "reason": "prove repair"},
                {"path": "tests/test_new.py", "responsibility": "counterexample", "reason": "safe case"},
            ],
            "unchanged_boundaries": ["README"],
            "forbidden_repairs": ["do not edit README"],
            "forbidden_patterns": [],
            "required_invariants": ["scope remains exact"],
            "required_tests": tests,
            "rollback_plan": "restore the baseline files",
            "risks": ["incorrect oracle"],
        }
        _write(self.case_dir / "repair-plan.json", plan)
        review = {
            "schema_version": 1,
            "record_type": "plan-review",
            "change_id": self.change_id,
            "repair_plan_sha256": file_sha256(self.case_dir / "repair-plan.json"),
            "reviewer_role": "repair-plan-reviewer",
            "decision": review_decision,
            "skill_rule_mappings": [{"rule": "root-cause-first", "plan_evidence": "bound records"}],
            "approved_paths": list(self.contract["allowed_paths"]),
        }
        _write(self.case_dir / "plan-review.json", review)

    def _permit(self) -> None:
        create_permit(self.root, self.contract)

    def _valid_candidate(self) -> None:
        _write(self.root / "src/module.py", "def value():\n    return 2\n")
        _write(
            self.root / "tests/test_module.py",
            "import unittest\n\nclass TestModule(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(2, 2)\n        self.assertNotEqual(2, 1)\n",
        )
        _write(
            self.root / "tests/test_new.py",
            "import unittest\n\nclass TestCounterexample(unittest.TestCase):\n    def test_unrelated(self):\n        self.assertTrue(True)\n",
        )

    def _write_evidence(self) -> dict[str, str]:
        rows: dict[str, str] = {}
        for dimension in sorted(MANDATORY_CLOSURE_DIMENSIONS):
            path = self.case_dir / "evidence" / f"{dimension}.json"
            _write(path, {"status": "PASS", "dimension": dimension})
            rows[dimension] = path.relative_to(self.root).as_posix()
        return rows

    def test_begin_requires_proven_root_cause_and_approved_plan(self) -> None:
        self._permit()
        self.assertEqual(validate_begin_ready(self.root, self.contract)["status"], "PASS")
        root = json.loads((self.case_dir / "root-cause-proof.json").read_text())
        root["decision"] = "UNPROVEN"
        _write(self.case_dir / "root-cause-proof.json", root)
        with self.assertRaisesRegex(ValueError, "not bound|proven root cause"):
            validate_begin_ready(self.root, self.contract)

    def test_permit_scope_is_stricter_than_contract_scope(self) -> None:
        self._permit()
        self.assertEqual(permit_path_decision(self.root, self.contract, "src/module.py"), (True, "allowed by active ChangePermit"))
        allowed, reason = permit_path_decision(self.root, self.contract, "README.md")
        self.assertFalse(allowed)
        self.assertIn("outside ChangePermit", reason)

    def test_real_workspace_diff_rejects_out_of_scope_change(self) -> None:
        self._permit()
        self._valid_candidate()
        _write(self.root / "README.md", "unauthorized\n")
        review = compute_diff_review(self.root, self.contract)
        self.assertEqual(review["decision"], "REJECT")
        self.assertIn("README.md", review["out_of_scope_paths"])

    def test_test_integrity_rejects_skip_and_assertion_reduction(self) -> None:
        self._permit()
        _write(self.root / "src/module.py", "def value():\n    return 2\n")
        _write(
            self.root / "tests/test_module.py",
            "import unittest\n\nclass TestModule(unittest.TestCase):\n    @unittest.skip('avoid failure')\n    def test_value(self):\n        pass\n",
        )
        review = compute_diff_review(self.root, self.contract)
        self.assertEqual(review["decision"], "REJECT")
        self.assertTrue(any("assertion_count_decreased" in item for item in review["test_integrity_findings"]))
        self.assertTrue(any("skip_markers_increased" in item for item in review["test_integrity_findings"]))

    def test_complete_evidence_chain_can_close(self) -> None:
        self._permit()
        self._valid_candidate()
        write_diff_review(self.root, self.contract)
        evidence = self._write_evidence()
        write_closure_matrix(
            self.root,
            self.contract,
            result="CONVERGED",
            evidence=evidence,
            loop_outcome="CONVERGED",
        )
        result = validate_verification_ready(self.root, self.contract, expected_result="CONVERGED")
        self.assertEqual(result["final_decision"], "CLOSED_VERIFIED")

    def test_missing_counterexample_evidence_cannot_close(self) -> None:
        self._permit()
        self._valid_candidate()
        write_diff_review(self.root, self.contract)
        evidence = self._write_evidence()
        evidence.pop("counterexamples")
        with self.assertRaisesRegex(ValueError, "missing mandatory dimensions"):
            write_closure_matrix(
                self.root,
                self.contract,
                result="CONVERGED",
                evidence=evidence,
                loop_outcome="CONVERGED",
            )

    def test_max_repair_exhaustion_cannot_be_converged(self) -> None:
        self._permit()
        self._valid_candidate()
        write_diff_review(self.root, self.contract)
        with self.assertRaisesRegex(ValueError, "cannot imply convergence"):
            write_closure_matrix(
                self.root,
                self.contract,
                result="CONVERGED",
                evidence=self._write_evidence(),
                loop_outcome="STOPPED_MAX_REPAIRS",
            )

    def test_schema_declares_every_governance_record_type(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "repair-governance.schema.json"
        schema = json.loads(schema_path.read_text())
        references = {row.get("$ref") for row in schema.get("oneOf", [])}
        expected = {
            "#/$defs/failureCase", "#/$defs/rootCauseProof", "#/$defs/repairPlan",
            "#/$defs/planReview", "#/$defs/baselineManifest", "#/$defs/changePermit",
            "#/$defs/diffReview", "#/$defs/closureMatrix",
        }
        self.assertEqual(references, expected)

    def test_forbidden_implementation_pattern_is_rejected(self) -> None:
        plan = json.loads((self.case_dir / "repair-plan.json").read_text())
        plan["forbidden_patterns"] = [{
            "id": "no-eval", "pattern": r"\beval\(", "include": ["src/*.py"], "exclude": []
        }]
        _write(self.case_dir / "repair-plan.json", plan)
        review = json.loads((self.case_dir / "plan-review.json").read_text())
        review["repair_plan_sha256"] = file_sha256(self.case_dir / "repair-plan.json")
        _write(self.case_dir / "plan-review.json", review)
        self._permit()
        _write(self.root / "src/module.py", "def value():\n    return eval('2')\n")
        review_payload = compute_diff_review(self.root, self.contract)
        self.assertEqual(review_payload["decision"], "REJECT")
        self.assertTrue(any("forbidden_pattern:no-eval" in item for item in review_payload["forbidden_pattern_findings"]))

    def test_source_change_after_diff_review_invalidates_review(self) -> None:
        self._permit()
        self._valid_candidate()
        write_diff_review(self.root, self.contract)
        _write(self.root / "src/module.py", "def value():\n    return 3\n")
        with self.assertRaisesRegex(ValueError, "stale or forged"):
            load_chain(self.root, self.contract, include_diff=True)

    def test_environment_block_cannot_be_converged(self) -> None:
        self._permit()
        self._valid_candidate()
        write_diff_review(self.root, self.contract)
        with self.assertRaisesRegex(ValueError, "cannot imply convergence"):
            write_closure_matrix(
                self.root, self.contract, result="CONVERGED",
                evidence=self._write_evidence(), loop_outcome="BLOCKED_BY_ENVIRONMENT"
            )

    def test_current_diff_review_is_recomputed_not_trusted(self) -> None:
        self._permit()
        self._valid_candidate()
        path = write_diff_review(self.root, self.contract)
        payload = json.loads(path.read_text())
        payload["changed_paths"] = []
        _write(path, payload)
        with self.assertRaisesRegex(ValueError, "stale or forged"):
            load_chain(self.root, self.contract, include_diff=True)


if __name__ == "__main__":
    unittest.main()
