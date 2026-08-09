from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = ROOT / "skill-system" / "controller" / "product_quality_bridge.py"


def _load_bridge():
    name = "product_quality_bridge_a4_transport"
    spec = importlib.util.spec_from_file_location(name, BRIDGE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class BaselineOracleBridgeTransportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = _load_bridge()

    def test_cli_exposes_oracle_transport_on_baseline_only(self) -> None:
        baseline_help = subprocess.run(
            [sys.executable, "-B", str(BRIDGE_PATH), "baseline", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(baseline_help.returncode, 0, baseline_help.stderr)
        self.assertIn("--baseline-oracle-manifest", baseline_help.stdout)
        self.assertIn("--baseline-oracle-artifact", baseline_help.stdout)
        self.assertIn("--workspace-root", baseline_help.stdout)

        verify_help = subprocess.run(
            [sys.executable, "-B", str(BRIDGE_PATH), "verify", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(verify_help.returncode, 0, verify_help.stderr)
        self.assertNotIn("--baseline-oracle-manifest", verify_help.stdout)
        self.assertNotIn("--baseline-oracle-artifact", verify_help.stdout)
        self.assertIn("--workspace-root", verify_help.stdout)

    def test_run_quality_fails_closed_on_partial_or_nonbaseline_oracle_input(self) -> None:
        bridge = self.bridge
        manifest = Path("manifest.json")
        artifact = Path("overlay.zip")
        with self.assertRaisesRegex(ValueError, "requires both manifest and artifact"):
            bridge._run_quality(
                baseline=True,
                mode="static",
                evidence_dir=Path("evidence"),
                state_dir=Path("state"),
                baseline_oracle_manifest=manifest,
            )
        with self.assertRaisesRegex(ValueError, "valid only for product baseline"):
            bridge._run_quality(
                baseline=False,
                mode="static",
                evidence_dir=Path("evidence"),
                state_dir=Path("state"),
                baseline_oracle_manifest=manifest,
                baseline_oracle_artifact=artifact,
            )

    def test_run_quality_forwards_exact_oracle_paths_without_identity_synthesis(self) -> None:
        bridge = self.bridge
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "governance" / "target.md"
            target.parent.mkdir(parents=True)
            target.write_text("target\n", encoding="utf-8")
            manifest = root / ".quality" / "baseline-oracles" / "oracle" / "manifest.json"
            artifact = manifest.with_name("overlay.zip")
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{not parsed by bridge}\n", encoding="utf-8")
            artifact.write_bytes(b"opaque artifact bytes")
            evidence = root / ".quality" / "evidence" / "baseline"
            state = root / ".quality" / "state"
            captured: list[str] = []

            fake_contract = SimpleNamespace(
                payload={"quality_target": "governance/target.md"},
                target_kind=SimpleNamespace(value="migration"),
            )

            def fake_run(argv, **_kwargs):
                captured.extend(str(value) for value in argv)
                return SimpleNamespace(returncode=1, stdout="", stderr="expected red")

            with (
                mock.patch.object(bridge, "ROOT", root),
                mock.patch.object(bridge, "QUALITY_LOOP", root / "scripts" / "quality_loop.py"),
                mock.patch.object(bridge, "verify_product_contract", return_value=[]),
                mock.patch.object(bridge, "load_contract", return_value=fake_contract),
                mock.patch.object(bridge, "_resolve_python", return_value=sys.executable),
                mock.patch.object(bridge.subprocess, "run", side_effect=fake_run),
            ):
                result = bridge._run_quality(
                    baseline=True,
                    mode="static",
                    evidence_dir=evidence,
                    state_dir=state,
                    baseline_oracle_manifest=manifest,
                    baseline_oracle_artifact=artifact,
                )

            self.assertEqual(result["status"], "FAIL")
            manifest_index = captured.index("--baseline-oracle-manifest")
            artifact_index = captured.index("--baseline-oracle-artifact")
            self.assertEqual(captured[manifest_index + 1], str(manifest))
            self.assertEqual(captured[artifact_index + 1], str(artifact))
            self.assertNotIn("--acceptance-overlay", captured)
            self.assertNotIn("--test-overlay", captured)

    def test_baseline_resolves_both_files_before_deleting_existing_evidence(self) -> None:
        bridge = self.bridge
        fake_contract = SimpleNamespace(
            target_kind=SimpleNamespace(value="migration"),
            payload={"minimum_quality_mode": "static"},
            change_id="A4-TEST",
        )
        resolved: list[tuple[object, str]] = []

        def fake_relative(raw, *, label, workspace):
            resolved.append((raw, label, workspace))
            if label == "baseline_oracle_artifact":
                raise ValueError("artifact invalid")
            return Path("/tmp/manifest.json")

        with (
            mock.patch.object(bridge, "load_contract", return_value=fake_contract),
            mock.patch.object(bridge, "_relative_file", side_effect=fake_relative),
            mock.patch.object(bridge.shutil, "rmtree") as rmtree,
        ):
            with self.assertRaisesRegex(ValueError, "artifact invalid"):
                bridge.baseline(
                    force=True,
                    baseline_oracle_manifest="manifest.json",
                    baseline_oracle_artifact="overlay.zip",
                )
        self.assertEqual(
            resolved,
            [
                ("manifest.json", "baseline_oracle_manifest", bridge.ROOT.resolve()),
                ("overlay.zip", "baseline_oracle_artifact", bridge.ROOT.resolve()),
            ],
        )
        rmtree.assert_not_called()

    def test_run_quality_routes_all_product_authority_to_explicit_workspace(self) -> None:
        bridge = self.bridge
        with tempfile.TemporaryDirectory() as control_raw, tempfile.TemporaryDirectory() as product_raw:
            control_root = Path(control_raw)
            product_root = Path(product_raw).resolve()
            target = product_root / "governance" / "target.md"
            target.parent.mkdir(parents=True)
            target.write_text("target\n", encoding="utf-8")
            evidence = product_root / ".quality" / "product-code" / "change-1" / "baseline"
            state = product_root / ".quality" / "product-code" / "change-1" / "state"
            manifest = product_root / ".quality" / "oracle" / "manifest.json"
            artifact = product_root / ".quality" / "oracle" / "overlay.zip"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}\n", encoding="utf-8")
            artifact.write_bytes(b"overlay")
            fake_contract = SimpleNamespace(
                change_id="change-1",
                payload={"quality_target": "governance/target.md"},
                target_kind=SimpleNamespace(value="migration"),
            )
            calls: dict[str, object] = {}

            def fake_gate(workspace):
                calls["gate_workspace"] = workspace
                return []

            def fake_load(workspace, require_approved=False):
                calls["contract_workspace"] = workspace
                return fake_contract

            def fake_run(argv, **kwargs):
                calls["argv"] = [str(value) for value in argv]
                calls["cwd"] = kwargs.get("cwd")
                evidence.mkdir(parents=True, exist_ok=True)
                (evidence / "baseline-record.json").write_text("{}\n", encoding="utf-8")
                (evidence / "run-summary.json").write_text(
                    '{"run_kind":"baseline","loop_status":"BASELINE_RECORDED"}\n',
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=1, stdout="expected red", stderr="")

            with (
                mock.patch.object(bridge, "ROOT", control_root),
                mock.patch.object(bridge, "QUALITY_LOOP", control_root / "scripts" / "quality_loop.py"),
                mock.patch.object(bridge, "verify_product_contract", side_effect=fake_gate),
                mock.patch.object(bridge, "load_contract", side_effect=fake_load),
                mock.patch.object(bridge, "_resolve_python", return_value=sys.executable),
                mock.patch.object(bridge.subprocess, "run", side_effect=fake_run),
            ):
                result = bridge._run_quality(
                    baseline=True,
                    mode="static",
                    evidence_dir=evidence,
                    state_dir=state,
                    baseline_oracle_manifest=manifest,
                    baseline_oracle_artifact=artifact,
                    workspace=product_root,
                )

            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["expected_red_baseline"])
            self.assertEqual(calls["gate_workspace"], product_root)
            self.assertEqual(calls["contract_workspace"], product_root)
            self.assertEqual(calls["cwd"], product_root)
            argv = calls["argv"]
            workspace_index = argv.index("--workspace-root")
            self.assertEqual(argv[workspace_index + 1], str(product_root))
            self.assertEqual(result["workspace_root"], str(product_root))
            self.assertEqual(result["evidence_dir"], ".quality/product-code/change-1/baseline")

    def test_baseline_resolves_oracle_under_explicit_product_workspace(self) -> None:
        bridge = self.bridge
        with tempfile.TemporaryDirectory() as raw:
            product_root = Path(raw).resolve()
            manifest = product_root / ".quality" / "oracle" / "manifest.json"
            artifact = product_root / ".quality" / "oracle" / "overlay.zip"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}\n", encoding="utf-8")
            artifact.write_bytes(b"overlay")
            fake_contract = SimpleNamespace(
                target_kind=SimpleNamespace(value="migration"),
                payload={"minimum_quality_mode": "static"},
                change_id="change-1",
                path=product_root / "governance" / "active-change.json",
            )
            captured: dict[str, object] = {}

            def fake_run_quality(**kwargs):
                captured.update(kwargs)
                return {
                    "status": "FAIL",
                    "evidence_dir": ".quality/product-code/change-1/baseline",
                }

            with (
                mock.patch.object(bridge, "load_contract", return_value=fake_contract) as load_contract,
                mock.patch.object(bridge, "_run_quality", side_effect=fake_run_quality),
            ):
                result = bridge.baseline(
                    baseline_oracle_manifest=".quality/oracle/manifest.json",
                    baseline_oracle_artifact=".quality/oracle/overlay.zip",
                    workspace=product_root,
                )

            self.assertEqual(result["status"], "FAIL")
            load_contract.assert_called_once_with(product_root, require_approved=False)
            self.assertEqual(captured["workspace"], product_root)
            self.assertEqual(captured["baseline_oracle_manifest"], manifest)
            self.assertEqual(captured["baseline_oracle_artifact"], artifact)

    def test_bridge_does_not_own_or_recompute_oracle_identity(self) -> None:
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("baseline_oracle_overlay_identity", source)
        self.assertNotIn("canonical_fingerprint", source)
        self.assertNotIn("quality_control.baseline_oracle", source)
        self.assertNotIn("load_and_validate_baseline_oracle", source)


if __name__ == "__main__":
    unittest.main()
