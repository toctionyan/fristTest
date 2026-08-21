from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_engineering_bounded_autonomy_closure.py"
SPEC = importlib.util.spec_from_file_location("verify_engineering_bounded_autonomy_closure", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class EngineeringBoundedAutonomyClosureTests(unittest.TestCase):
    def _copy_contract_root(self) -> Path:
        temp = Path(tempfile.mkdtemp(prefix="engineering-autonomy-closure-"))
        self.addCleanup(shutil.rmtree, temp, True)
        for relative in MODULE.WORKFLOWS.values():
            source = ROOT / relative
            target = temp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return temp

    def _mutate(self, root: Path, key: str, old: str, new: str) -> None:
        path = root / MODULE.WORKFLOWS[key]
        source = path.read_text(encoding="utf-8")
        self.assertIn(old, source)
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def test_current_bounded_autonomy_chain_closes_routine_clicks(self) -> None:
        result = MODULE.verify(ROOT)
        self.assertEqual(result["status"], "PASS", result["errors"])
        self.assertFalse(result["routine_manual_clicks_required_after_bounded_owner_authorization"])
        self.assertEqual(result["bounded_merge_authority"], "engineering-merge-grant@1")
        self.assertFalse(result["ordinary_autonomy_grant_merge_allowed"])
        self.assertFalse(result["deploy_allowed"])
        self.assertFalse(result["production_closed"])
        self.assertIn("independent_review_policy", result["true_human_gates"])
        self.assertIn("real_github_workflow_approval", result["true_human_gates"])

    def test_default_bounded_merge_policy_cannot_drift_back_to_manual_landing(self) -> None:
        root = self._copy_contract_root()
        self._mutate(root, "authorize", "default: bounded-auto-merge", "default: disabled")
        result = MODULE.verify(root)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("default: bounded-auto-merge" in error for error in result["errors"]))

    def test_solo_automation_cannot_drop_independent_review_guard(self) -> None:
        root = self._copy_contract_root()
        self._mutate(root, "solo_wakeup", "prevent_self_review", "self_review_not_checked")
        result = MODULE.verify(root)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("prevent_self_review" in error for error in result["errors"]))

    def test_resume_requires_real_successful_pull_request_workflow(self) -> None:
        root = self._copy_contract_root()
        self._mutate(
            root,
            "resume_wakeup",
            "github.event.workflow_run.event == 'pull_request'",
            "github.event.workflow_run.event != 'pull_request'",
        )
        result = MODULE.verify(root)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("pull_request" in error for error in result["errors"]))

    def test_final_landing_must_ready_then_reread_then_exact_merge(self) -> None:
        root = self._copy_contract_root()
        self._mutate(root, "authorized_merge", "Mark exact governed PR Ready", "Skip Ready transition")
        result = MODULE.verify(root)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(
            any(
                "Mark exact governed PR Ready" in error or "ready_reread_merge_order_drift" in error
                for error in result["errors"]
            )
        )

    def test_final_landing_cannot_switch_to_squash(self) -> None:
        root = self._copy_contract_root()
        self._mutate(
            root,
            "authorized_merge",
            '[[ "${expected_head}" == "${{ steps.g6.outputs.exact_head }}" && "${method}" == "merge" ]]',
            '[[ "${expected_head}" == "${{ steps.g6.outputs.exact_head }}" && "${method}" == "squash" ]]',
        )
        result = MODULE.verify(root)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any('"${method}" == "merge"' in error for error in result["errors"]))

    def test_wakeup_chain_cannot_gain_self_approval_capability(self) -> None:
        root = self._copy_contract_root()
        path = root / MODULE.WORKFLOWS["resume_wakeup"]
        path.write_text(
            path.read_text(encoding="utf-8") + "\n# gh pr review --approve forbidden\n",
            encoding="utf-8",
        )
        result = MODULE.verify(root)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(
            any("forbidden_human_gate_bypass" in error for error in result["errors"])
        )


if __name__ == "__main__":
    unittest.main()
