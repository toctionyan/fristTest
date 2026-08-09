from __future__ import annotations

import contextlib
import importlib.util
import json
import os
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

from quality_control.baseline_oracle import BaselineOracleOverlayIdentity


def _load_quality_loop():
    name = "quality_loop_a3_2b_evidence"
    spec = importlib.util.spec_from_file_location(name, QUALITY_LOOP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class BaselineOracleEvidenceBindingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.q = _load_quality_loop()

    def _identity(self) -> BaselineOracleOverlayIdentity:
        return BaselineOracleOverlayIdentity(
            payload={
                "schema_version": 1,
                "oracle_id": "a3-2b-test",
                "base_source_identity": {
                    "repository": "toctionyan/fristTest",
                    "commit_sha": "e0e04d51e9da9790bef7bd0482584f60b8e975a9",
                },
                "base_workspace_fingerprint": "a" * 64,
                "overlay_artifact_sha256": "b" * 64,
                "overlay_file_map": [
                    {
                        "path": "tests/test_behavior.py",
                        "base_file_sha256": "c" * 64,
                        "overlay_file_sha256": "d" * 64,
                    }
                ],
                "claim_bindings": [
                    {
                        "claim_id": "CLAIM.RED",
                        "selector": "tests/test_behavior.py::test_red",
                    }
                ],
                "provenance": {
                    "provider": "github-actions",
                    "run_id": 1,
                    "job_id": 2,
                    "artifact_id": 3,
                    "artifact_digest": "b" * 64,
                },
                "execution_mode": "ephemeral_overlay_view",
                "canonical_fingerprint": "e" * 64,
            }
        )

    def _target(self) -> dict:
        return {
            "id": "a3-2b-target",
            "change_ref": "a3-2b-change",
            "context": "local-change",
            "kind": "migration",
            "minimum_mode": "static",
            "minimum_mode_declared": "static",
            "minimum_mode_derived": "static",
            "claim_manifest": "governance/claims/a3-2b.json",
            "claim_manifest_fingerprint": "f" * 64,
            "claims": [
                {
                    "id": "CLAIM.RED",
                    "closure_requirement": "regression-transition",
                    "evidence_refs": ["tests/test_behavior.py::test_red"],
                }
            ],
            "requirement_catalog": None,
            "requirement_profile": None,
            "requirement_catalog_fingerprint": None,
            "current_round": 1,
            "max_rounds": 2,
        }

    def test_baseline_record_summary_and_identity_file_bind_same_oracle(self) -> None:
        q = self.q
        identity = self._identity()
        target_contract = self._target()
        snapshot = {"fingerprint": "a" * 64, "files": {"source.py": "1" * 64}}
        failed_result = {
            "id": "proof",
            "name": "proof",
            "status": q.FAIL,
            "owner": "test",
            "category": "unit-contract",
            "blocking_level": "required",
            "repair_playbook": "repair source",
            "depends_on": [],
            "started_at": "2026-08-07T00:00:00+00:00",
            "ended_at": "2026-08-07T00:00:00+00:00",
            "exit_code": 1,
            "duration_ms": 1,
            "stdout": "",
            "stderr": "red",
            "metadata": {},
        }
        claim_result = {
            "id": "CLAIM.RED",
            "status": "FAILED",
            "evidence_ref_statuses": {"tests/test_behavior.py::test_red": "FAILED"},
        }

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as view_tmp:
            workspace = Path(tmp)
            (workspace / "VERSION").write_text("test\n", encoding="utf-8")
            target = workspace / "governance" / "targets" / "a3-2b.md"
            target.parent.mkdir(parents=True)
            target.write_text("target\n", encoding="utf-8")
            claims = workspace / "governance" / "claims" / "a3-2b.json"
            claims.parent.mkdir(parents=True)
            claims.write_text("{}\n", encoding="utf-8")
            policy = workspace / "policy.json"
            policy.write_text("{}\n", encoding="utf-8")
            evidence = workspace / ".quality" / "evidence" / "baseline"
            state_dir = workspace / ".quality" / "state"
            view = Path(view_tmp)
            (view / "governance" / "targets").mkdir(parents=True)
            (view / "governance" / "targets" / "a3-2b.md").write_text("target\n", encoding="utf-8")

            @contextlib.contextmanager
            def fake_oracle_view(**_kwargs):
                yield SimpleNamespace(path=view, identity=identity)

            patches = [
                mock.patch.object(q, "_load_json", return_value={"version": "test"}),
                mock.patch.object(q, "_validate_policy", return_value=[{"id": "proof", "blocking_level": "required"}]),
                mock.patch.object(q, "_steps_for_mode", side_effect=lambda steps, _mode: steps),
                mock.patch.object(q, "_workspace_snapshot", return_value=snapshot),
                mock.patch.object(q, "baseline_oracle_execution_view", fake_oracle_view),
                mock.patch.object(q, "_parse_target", return_value=target_contract),
                mock.patch.object(q, "validate_baseline_oracle_claim_bindings"),
                mock.patch.object(q, "_validate_replan_predecessor", return_value=None),
                mock.patch.object(q, "_validate_claim_gate_contracts"),
                mock.patch.object(q, "_verify_loop_round", return_value=(None, None)),
                mock.patch.object(q, "_run_step", return_value=failed_result),
                mock.patch.object(q, "_write_step_evidence"),
                mock.patch.object(q, "_decision", return_value=q.FAIL),
                mock.patch.object(q, "_claim_results", return_value=[claim_result]),
                mock.patch.object(q, "_target_identity", return_value={"id": "a3-2b-target", "fingerprint": "9" * 64}),
                mock.patch.object(q, "_write_loop_state"),
                mock.patch.object(q, "_quality_dimensions", return_value=[]),
                mock.patch.object(q, "_gate_contract_fingerprints", return_value={"proof": "8" * 64}),
                mock.patch.object(q, "_repair_plan", return_value={}),
                mock.patch.object(q, "_write_evidence_attestation"),
            ]
            with contextlib.ExitStack() as stack:
                for patch in patches:
                    stack.enter_context(patch)
                summary = q.run_loop(
                    workspace,
                    policy,
                    mode="static",
                    evidence_dir=evidence,
                    rerun_from=None,
                    target_path=target,
                    baseline=True,
                    baseline_evidence=None,
                    prior_evidence=None,
                    state_dir=state_dir,
                    baseline_oracle_manifest=workspace / "oracle.json",
                    baseline_oracle_artifact=workspace / "oracle.zip",
                )

            record = json.loads((evidence / "baseline-record.json").read_text(encoding="utf-8"))
            persisted = json.loads((evidence / "run-summary.json").read_text(encoding="utf-8"))
            identity_file = json.loads(
                (evidence / "baseline-oracle-overlay-identity.json").read_text(encoding="utf-8")
            )
            expected = identity.payload
            self.assertEqual(record["baseline_oracle_overlay_identity"], expected)
            self.assertEqual(summary["baseline_oracle_overlay_identity"], expected)
            self.assertEqual(persisted["baseline_oracle_overlay_identity"], expected)
            self.assertEqual(identity_file, expected)
            self.assertEqual(
                record["baseline_oracle_identity_evidence_file"],
                "baseline-oracle-overlay-identity.json",
            )
            self.assertEqual(
                summary["baseline_oracle_identity_evidence_file"],
                "baseline-oracle-overlay-identity.json",
            )
            self.assertEqual(
                record["baseline_oracle_overlay_identity"]["canonical_fingerprint"],
                identity_file["canonical_fingerprint"],
            )
            self.assertEqual(record["workspace_snapshot_fingerprint"], snapshot["fingerprint"])

    def test_cli_transports_both_oracle_inputs_into_run_loop(self) -> None:
        q = self.q
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            target = workspace / "target.md"
            target.write_text("target\n", encoding="utf-8")
            manifest = workspace / "manifest.json"
            artifact = workspace / "overlay.zip"
            manifest.write_text("{}\n", encoding="utf-8")
            artifact.write_bytes(b"zip")
            argv = [
                str(QUALITY_LOOP),
                "--workspace-root", str(workspace),
                "--target", str(target),
                "--baseline",
                "--baseline-oracle-manifest", str(manifest),
                "--baseline-oracle-artifact", str(artifact),
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.dict(
                os.environ, {"SKILL_JUDGE_TRUST_MODE": "workspace-fallback"}, clear=False
            ), mock.patch.object(q, "run_loop", return_value={"decision": q.PASS}) as run:
                exit_code = q.main()
            self.assertEqual(exit_code, 0)
            kwargs = run.call_args.kwargs
            self.assertEqual(kwargs["baseline_oracle_manifest"], manifest.resolve())
            self.assertEqual(kwargs["baseline_oracle_artifact"], artifact.resolve())
            self.assertTrue(kwargs["baseline"])

    def test_cli_help_exposes_narrow_baseline_oracle_surface(self) -> None:
        text = QUALITY_LOOP.read_text(encoding="utf-8")
        self.assertIn('"--baseline-oracle-manifest"', text)
        self.assertIn('"--baseline-oracle-artifact"', text)
        self.assertNotIn('"--acceptance-overlay"', text)
        self.assertNotIn('"--test-overlay"', text)


if __name__ == "__main__":
    unittest.main()
