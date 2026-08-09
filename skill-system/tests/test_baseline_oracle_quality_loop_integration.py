from __future__ import annotations

import contextlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
QUALITY_LOOP = SCRIPTS / "quality_loop.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from quality_control.baseline_oracle import (
    BaselineOracleError,
    BaselineOracleOverlayIdentity,
    validate_baseline_oracle_claim_bindings,
)


def _load_quality_loop():
    name = "quality_loop_a3_1_integration"
    spec = importlib.util.spec_from_file_location(name, QUALITY_LOOP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class BaselineOracleClaimBindingIntegrationTest(unittest.TestCase):
    def _identity(self, selector: str = "tests/test_behavior.py::test_red") -> BaselineOracleOverlayIdentity:
        return BaselineOracleOverlayIdentity(
            payload={
                "overlay_file_map": [
                    {
                        "path": "tests/test_behavior.py",
                        "base_file_sha256": "0" * 64,
                        "overlay_file_sha256": "1" * 64,
                    }
                ],
                "claim_bindings": [{"claim_id": "CLAIM.RED", "selector": selector}],
                "canonical_fingerprint": "2" * 64,
            }
        )

    def _target(self, selector: str = "tests/test_behavior.py::test_red") -> dict:
        return {
            "claims": [
                {
                    "id": "CLAIM.RED",
                    "closure_requirement": "regression-transition",
                    "evidence_refs": [selector],
                }
            ]
        }

    def test_exact_transition_selector_binding_passes(self) -> None:
        validate_baseline_oracle_claim_bindings(self._target(), self._identity())

    def test_selector_substitution_fails_closed(self) -> None:
        with self.assertRaisesRegex(BaselineOracleError, "not declared by claim"):
            validate_baseline_oracle_claim_bindings(
                self._target(), self._identity("tests/test_behavior.py::test_other")
            )

    def test_non_transition_claim_cannot_consume_overlay(self) -> None:
        target = self._target()
        target["claims"][0]["closure_requirement"] = "current-source"
        with self.assertRaisesRegex(BaselineOracleError, "regression-transition"):
            validate_baseline_oracle_claim_bindings(target, self._identity())

    def test_every_overlay_file_must_be_bound(self) -> None:
        identity = self._identity()
        identity.payload["overlay_file_map"].append(
            {
                "path": "tests/test_extra.py",
                "base_file_sha256": "3" * 64,
                "overlay_file_sha256": "4" * 64,
            }
        )
        with self.assertRaisesRegex(BaselineOracleError, "every baseline oracle overlay file"):
            validate_baseline_oracle_claim_bindings(self._target(), identity)


class QualityLoopExecutionScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.quality_loop = _load_quality_loop()

    def test_without_oracle_uses_source_workspace_and_target(self) -> None:
        q = self.quality_loop
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            target = workspace / "target.md"
            target.write_text("target", encoding="utf-8")
            with q._baseline_execution_scope(
                workspace,
                target,
                baseline=False,
                baseline_oracle_manifest=None,
                baseline_oracle_artifact=None,
            ) as (execution_workspace, execution_target, identity):
                self.assertEqual(execution_workspace, workspace)
                self.assertEqual(execution_target, target)
                self.assertIsNone(identity)

    def test_partial_oracle_input_fails_closed(self) -> None:
        q = self.quality_loop
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            target = workspace / "target.md"
            target.write_text("target", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requires both manifest and artifact"):
                with q._baseline_execution_scope(
                    workspace,
                    target,
                    baseline=True,
                    baseline_oracle_manifest=workspace / "oracle.json",
                    baseline_oracle_artifact=None,
                ):
                    pass

    def test_oracle_is_baseline_only(self) -> None:
        q = self.quality_loop
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            target = workspace / "target.md"
            target.write_text("target", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "only for --baseline"):
                with q._baseline_execution_scope(
                    workspace,
                    target,
                    baseline=False,
                    baseline_oracle_manifest=workspace / "oracle.json",
                    baseline_oracle_artifact=workspace / "oracle.zip",
                ):
                    pass

    def test_target_is_remapped_into_ephemeral_execution_view(self) -> None:
        q = self.quality_loop
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as view_tmp:
            workspace = Path(source_tmp)
            target = workspace / "governance" / "target.md"
            target.parent.mkdir(parents=True)
            target.write_text("source target", encoding="utf-8")
            view = Path(view_tmp)
            (view / "governance").mkdir(parents=True)
            (view / "governance" / "target.md").write_text("view target", encoding="utf-8")
            identity = BaselineOracleOverlayIdentity(
                payload={"canonical_fingerprint": "a" * 64, "overlay_file_map": [], "claim_bindings": []}
            )

            @contextlib.contextmanager
            def fake_view(**_kwargs):
                yield SimpleNamespace(path=view, identity=identity)

            with mock.patch.object(q, "baseline_oracle_execution_view", fake_view):
                with q._baseline_execution_scope(
                    workspace,
                    target,
                    baseline=True,
                    baseline_oracle_manifest=workspace / "oracle.json",
                    baseline_oracle_artifact=workspace / "oracle.zip",
                ) as (execution_workspace, execution_target, actual_identity):
                    self.assertEqual(execution_workspace, view)
                    self.assertEqual(execution_target, view / "governance" / "target.md")
                    self.assertIs(actual_identity, identity)

    def test_target_outside_source_workspace_fails_before_oracle_materialization(self) -> None:
        q = self.quality_loop
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as outside_tmp:
            workspace = Path(source_tmp)
            target = Path(outside_tmp) / "target.md"
            target.write_text("target", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "target inside the source workspace"):
                with q._baseline_execution_scope(
                    workspace,
                    target,
                    baseline=True,
                    baseline_oracle_manifest=workspace / "oracle.json",
                    baseline_oracle_artifact=workspace / "oracle.zip",
                ):
                    pass

    def test_quality_loop_source_contains_execution_workspace_wiring(self) -> None:
        text = QUALITY_LOOP.read_text(encoding="utf-8")
        self.assertIn("_parse_target(execution_target_path, workspace=execution_workspace)", text)
        self.assertIn("_run_step(execution_workspace, evidence_dir, mode, step)", text)
        self.assertIn("run_start_snapshot = _workspace_snapshot(workspace", text)
        self.assertIn("run_end_snapshot = _workspace_snapshot(workspace", text)
        self.assertNotIn("_run_step(workspace, evidence_dir, mode, step)", text)


if __name__ == "__main__":
    unittest.main()
