from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from project_compatibility import evaluate  # type: ignore
from repair_governance import create_permit, file_sha256  # type: ignore


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ProjectCompatibilityPermitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        write(self.root / "services/app.py", "VALUE = 1\n")
        for path in (
            "scripts/quality_loop.py",
            "scripts/repair_loop.py",
            "architecture-skill/scripts/verify_skill_package.py",
        ):
            write(self.root / path, "# entrypoint\n")
        write(
            self.root / "skill-system/registry/product-source-baseline.json",
            {"files": {"services/obsolete.py": "deadbeef"}},
        )
        self.change_id = "skill-repair-compat-001"
        case = self.root / "governance/repair-cases" / self.change_id
        self.contract = {
            "schema_version": 1,
            "change_id": self.change_id,
            "target_kind": "repair",
            "goal": "keep product tree unchanged",
            "profile": "skill-only",
            "allowed_paths": ["skill-system/controller/example.py"],
            "forbidden_paths": ["services/**", "web/**", "contracts/**"],
            "invariants": ["product unchanged"],
            "required_profiles": ["skill-static", "skill-unit", "skill-host-integration", "skill-security", "project-compatibility-smoke"],
            "writer_role": "skill-implementer",
            "review_roles": ["scope-planner", "adversarial-reviewer", "release-judge"],
            "review_attestations": [],
            "status": "approved",
            "result": "PENDING",
            "repair_governance": case.relative_to(self.root).as_posix(),
        }
        failure = {
            "schema_version": 1, "record_type": "failure-case", "change_id": self.change_id,
            "classification": "implementation-defect",
            "reproduction": {"status": "REPRODUCED", "expected": "current baseline", "actual": "stale baseline", "evidence_refs": ["services/app.py"]},
            "violated_invariants": ["scope evidence"], "affected_boundaries": ["compatibility"],
        }
        write(case / "failure-case.json", failure)
        root_cause = {
            "schema_version": 1, "record_type": "root-cause-proof", "change_id": self.change_id,
            "failure_case_sha256": file_sha256(case / "failure-case.json"), "decision": "PROVEN",
            "root_cause": "the historical registry is older than the active transition baseline",
            "causal_chain": ["old snapshot remains", "current approved files look changed"],
            "evidence_refs": ["skill-system/registry/product-source-baseline.json"],
            "rejected_hypotheses": ["current repair changed product code"], "affected_boundaries": ["compatibility"],
        }
        write(case / "root-cause-proof.json", root_cause)
        tests = {"focused": ["baseline authority"], "counterexamples": ["no drift"], "regression": ["profile"], "negative_path": ["real drift"]}
        plan = {
            "schema_version": 1, "record_type": "repair-plan", "change_id": self.change_id,
            "root_cause_proof_sha256": file_sha256(case / "root-cause-proof.json"), "status": "APPROVED",
            "strategy": "use the permit-time full workspace manifest for protected files",
            "changes": [{"path": "skill-system/controller/example.py", "responsibility": "control", "reason": "test"}],
            "unchanged_boundaries": ["services"], "forbidden_repairs": ["do not refresh from candidate"],
            "forbidden_patterns": [], "required_invariants": ["real drift fails"], "required_tests": tests,
            "rollback_plan": "restore historical compatibility behavior", "risks": ["wrong baseline authority"],
        }
        write(case / "repair-plan.json", plan)
        review = {
            "schema_version": 1, "record_type": "plan-review", "change_id": self.change_id,
            "repair_plan_sha256": file_sha256(case / "repair-plan.json"), "reviewer_role": "repair-plan-reviewer",
            "decision": "APPROVED", "skill_rule_mappings": [{"rule": "baseline-bound", "plan_evidence": "permit"}],
            "approved_paths": ["skill-system/controller/example.py"],
        }
        write(case / "plan-review.json", review)
        create_permit(self.root, self.contract)
        write(self.root / "governance/active-change.json", self.contract)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_stale_historical_baseline_is_not_used_for_active_skill_repair(self) -> None:
        result = evaluate(self.root)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["baseline_authority"].startswith("change-permit:"))


    def test_closed_change_uses_promoted_historical_baseline(self) -> None:
        current_hash = file_sha256(self.root / "services/app.py")
        write(
            self.root / "skill-system/registry/product-source-baseline.json",
            {"files": {"services/app.py": current_hash}},
        )
        closed = dict(self.contract)
        closed["status"] = "closed"
        closed["result"] = "CONVERGED"
        write(self.root / "governance/active-change.json", closed)

        result = evaluate(self.root)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["baseline_authority"], "historical-registry-baseline")

    def test_rejected_change_cannot_override_historical_baseline(self) -> None:
        current_hash = file_sha256(self.root / "services/app.py")
        write(
            self.root / "skill-system/registry/product-source-baseline.json",
            {"files": {"services/app.py": current_hash}},
        )
        rejected = dict(self.contract)
        rejected["status"] = "rejected"
        rejected["result"] = "FAILED"
        write(self.root / "governance/active-change.json", rejected)

        result = evaluate(self.root)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["baseline_authority"], "historical-registry-baseline")


    def test_pytest_cache_is_not_product_source(self) -> None:
        current_hash = file_sha256(self.root / "services/app.py")
        write(
            self.root / "skill-system/registry/product-source-baseline.json",
            {"files": {"services/app.py": current_hash}},
        )
        closed = dict(self.contract)
        closed["status"] = "closed"
        closed["result"] = "CONVERGED"
        write(self.root / "governance/active-change.json", closed)
        write(self.root / "services/.pytest_cache/v/cache/nodeids", "[]")

        result = evaluate(self.root)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["protected_file_count"], 1)

    def test_real_product_drift_after_permit_is_rejected(self) -> None:
        write(self.root / "services/app.py", "VALUE = 2\n")
        result = evaluate(self.root)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("product_source_changed:services/app.py" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
