from __future__ import annotations

import json
import sys
import tempfile
import unittest
import runpy
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from architecture_policy import apply_delta, load_effective_policy, promote_delta, validate_delta  # type: ignore


class ArchitecturePolicyTest(unittest.TestCase):
    def baseline(self) -> dict:
        return {
            "schema_version": 2,
            "policy_kind": "project-architecture-baseline",
            "policy_id": "baseline-r1",
            "baseline_revision": 1,
            "project_version": "1.0.0",
            "version": "1.0.0",
            "non_variance_fields": [
                "policy_kind", "project_version", "source_roots", "runtime_roots",
                "configuration", "banned_universal_tools",
            ],
            "required_workspace_paths": ["old/required.py"],
            "forbidden_paths": ["old/retired"],
            "core_root": "src/core",
            "allowed_core_dirs": ["lifecycle"],
            "allowed_core_root_modules": ["__init__.py"],
            "composition_dir": "composition",
            "source_roots": ["src"],
            "single_graph_update_owner": "app/runner.py",
            "banned_universal_tools": [],
            "modules_root": "src/modules",
            "module_manifests": [],
            "line_limits": [],
            "runtime_roots": [],
            "convergence_matrix": "docs/matrix.md",
            "configuration": {},
        }

    def delta(self) -> dict:
        return {
            "schema_version": 1,
            "delta_id": "delta-1",
            "change_id": "change-1",
            "base_policy_id": "baseline-r1",
            "rationale": "Allow a selected evolutionary architecture without making names universal.",
            "operations": {
                "add_required_workspace_paths": ["new/semantic_contract.py"],
                "retire_required_workspace_paths": ["old/required.py"],
                "add_forbidden_paths": [],
                "retire_forbidden_paths": [],
                "add_allowed_core_dirs": ["semantics"],
                "retire_allowed_core_dirs": [],
                "add_allowed_core_root_modules": [],
                "retire_allowed_core_root_modules": [],
                "upsert_line_limits": [],
                "retire_line_limit_paths": [],
                "owner_changes": {},
            },
            "cutover": {
                "current_formal_owner": "old semantic owner",
                "target_formal_owner": "new semantic owner",
                "shadow_is_read_only": True,
                "cutover_condition": "Shadow evidence passes and the target becomes the only formal reader.",
                "rollback_condition": "Any P0 semantic regression restores the previous formal owner.",
                "cleanup_condition": "The old formal read and write paths are removed after certification.",
                "sunset_date": "2026-10-26",
            },
            "required_evidence": ["shadow comparison", "cutover regression", "old path removal"],
            "rollback_plan": "Restore the previous baseline and implementation from the trusted release.",
            "expiry_or_review_date": "2026-10-26",
            "status": "approved",
        }

    def test_delta_changes_only_effective_project_baseline(self) -> None:
        result = apply_delta(self.baseline(), self.delta())
        self.assertIn("semantics", result["allowed_core_dirs"])
        self.assertNotIn("old/required.py", result["required_workspace_paths"])
        self.assertIn("new/semantic_contract.py", result["required_workspace_paths"])
        self.assertEqual(result["effective_policy"]["delta_id"], "delta-1")

    def test_unknown_delta_operation_is_rejected(self) -> None:
        delta = self.delta()
        delta["operations"]["replace_business_authority"] = True
        errors = validate_delta(delta, base_policy=self.baseline())
        self.assertTrue(any("unknown_operations" in error for error in errors))

    def test_active_migration_contract_applies_reviewed_delta(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "governance/decisions").mkdir(parents=True)
            (root / "governance/variances").mkdir(parents=True)
            (root / "governance/deltas").mkdir(parents=True)
            (root / "governance/architecture-policy.json").write_text(json.dumps(self.baseline()), encoding="utf-8")
            decision = root / "governance/decisions/change-1.json"
            decision.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
            delta_path = root / "governance/deltas/change-1.json"
            delta_path.write_text(json.dumps(self.delta()), encoding="utf-8")
            variance = root / "governance/variances/change-1.json"
            variance.write_text(json.dumps({
                "schema_version": 1,
                "policy_delta": "governance/deltas/change-1.json",
            }), encoding="utf-8")
            (root / "governance/active-change.json").write_text(json.dumps({
                "schema_version": 1,
                "change_id": "change-1",
                "target_kind": "migration",
                "status": "implementing",
                "decision_record": "governance/decisions/change-1.json",
                "variance_records": ["governance/variances/change-1.json"],
                "architecture_policy_delta": "governance/deltas/change-1.json",
            }), encoding="utf-8")
            effective, metadata = load_effective_policy(root)
            self.assertEqual(metadata["mode"], "baseline+approved-delta")
            self.assertIn("semantics", effective["allowed_core_dirs"])

    def test_promotion_creates_new_baseline_and_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "governance/deltas").mkdir(parents=True)
            baseline_path = root / "governance/architecture-policy.json"
            baseline_path.write_text(json.dumps(self.baseline()), encoding="utf-8")
            delta_path = root / "governance/deltas/change-1.json"
            delta_path.write_text(json.dumps(self.delta()), encoding="utf-8")
            evidence = root / "certification.json"
            evidence.write_text(json.dumps({"result": "CONVERGED"}), encoding="utf-8")
            record = promote_delta(root, delta_path=delta_path, certification_evidence=[evidence], new_policy_id="baseline-r2")
            promoted = json.loads(baseline_path.read_text(encoding="utf-8"))
            self.assertEqual(promoted["policy_id"], "baseline-r2")
            self.assertEqual(promoted["supersedes_policy_id"], "baseline-r1")
            self.assertTrue(record.is_file())

    def test_effective_delta_changes_the_real_architecture_gate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "governance/decisions").mkdir(parents=True)
            (root / "governance/variances").mkdir(parents=True)
            (root / "governance/deltas").mkdir(parents=True)
            (root / "src/core/lifecycle").mkdir(parents=True)
            (root / "src/core/semantics").mkdir(parents=True)
            (root / "docs").mkdir(parents=True)
            (root / "docs/matrix.md").write_text("新增抽象 替换或删除 允许变更路径", encoding="utf-8")
            baseline = self.baseline()
            baseline["new_abstraction_rule"] = "target_snapshot_record"
            (root / "governance/architecture-policy.json").write_text(json.dumps(baseline), encoding="utf-8")
            (root / "governance/decisions/change-1.json").write_text("{}", encoding="utf-8")
            (root / "governance/deltas/change-1.json").write_text(json.dumps(self.delta()), encoding="utf-8")
            (root / "governance/variances/change-1.json").write_text(json.dumps({
                "policy_delta": "governance/deltas/change-1.json"
            }), encoding="utf-8")
            (root / "governance/active-change.json").write_text(json.dumps({
                "change_id": "change-1",
                "target_kind": "migration",
                "status": "implementing",
                "decision_record": "governance/decisions/change-1.json",
                "variance_records": ["governance/variances/change-1.json"],
                "architecture_policy_delta": "governance/deltas/change-1.json"
            }), encoding="utf-8")
            effective, _meta = load_effective_policy(root)
            verifier_path = Path(__file__).resolve().parents[2] / "architecture-skill/scripts/verify_convergence.py"
            verify = runpy.run_path(str(verifier_path))["verify"]
            result = verify(root, effective)
            self.assertEqual(result["checks"]["unexpected_core_dirs"], [])


if __name__ == "__main__":
    unittest.main()
