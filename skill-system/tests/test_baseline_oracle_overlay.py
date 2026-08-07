from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUALITY_LOOP = ROOT / "scripts" / "quality_loop.py"
BRIDGE = ROOT / "skill-system" / "controller" / "product_quality_bridge.py"


def _load_controller():
    spec = importlib.util.spec_from_file_location("quality_loop_c3g3", QUALITY_LOOP)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load quality controller")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical(payload: dict[str, object]) -> str:
    return _sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _git(workspace: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace), *args], text=True, capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr)
    return completed.stdout.strip()


def _build_workspace(root: Path) -> tuple[Path, Path, Path, Path, str, str]:
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "VERSION").write_text("c3g3-test\n", encoding="utf-8")
    (workspace / "source.txt").write_text("baseline\n", encoding="utf-8")
    tests = workspace / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("", encoding="utf-8")
    test_path = tests / "test_oracle_target.py"
    test_path.write_text("def test_preexisting_control():\n    assert True\n", encoding="utf-8")

    claim_id = "C3G3-TRANSITION-001"
    selector = "tests/test_oracle_target.py::test_new_test_first_oracle"
    claims_dir = workspace / "governance" / "claims"
    claims_dir.mkdir(parents=True)
    claims = {
        "schema_version": 1,
        "target_id": "c3g3-transition",
        "claims": [{
            "id": claim_id,
            "statement": "The test-first oracle must reproduce a real red transition.",
            "risk": "P2",
            "required_mode": "static",
            "evidence_kind": "static-contract",
            "required_gates": ["proof"],
            "evidence_refs": [selector],
            "owner": "quality-controller",
            "closure_requirement": "regression-transition",
        }],
    }
    (claims_dir / "c3g3.json").write_text(json.dumps(claims), encoding="utf-8")
    target = workspace / "governance" / "targets" / "c3g3-transition.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        f"""# 目标
- 目标 ID：c3g3-transition
- 变更标识：c3g3-transition-change
- 执行上下文：local-change
- 目标类型：migration

验证 test-first baseline oracle。

## 允许范围
- 允许变更路径：source.txt
- 新增抽象记录：无

## 禁止范围
不修改其他文件。

## 验收条件
- 最低质量模式：static
- 声明清单：governance/claims/c3g3.json
- 验收 ID：{claim_id}

## 基线
基线由精确源码 + immutable test-first oracle 组成。

## 修复轮次
- 最大轮次：2
- 当前轮次：1
- 失败后：修改唯一 Owner。
""",
        encoding="utf-8",
    )
    gate_script = (
        "from pathlib import Path; import os, pytest; "
        "j=Path(os.environ['QUALITY_EVIDENCE_DIR'])/'junit'; j.mkdir(parents=True, exist_ok=True); "
        "raise SystemExit(pytest.main(['-q','tests/test_oracle_target.py','--junitxml='+str(j/'proof.xml')]))"
    )
    policy = workspace / "policy.json"
    policy.write_text(json.dumps({
        "version": "c3g3-test",
        "steps": [{
            "id": "proof", "name": "proof", "modes": ["static"], "kind": "shell",
            "argv": [sys.executable, "-B", "-c", gate_script],
            "owner": "quality-controller", "category": "unit-contract", "blocking_level": "required",
            "repair_playbook": "repair product code, never the oracle",
            "rerun_contract": "dependency_closure_then_downstream", "depends_on": [],
            "environment": {}, "timeout_seconds": 30,
        }],
    }), encoding="utf-8")

    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "c3g3@example.invalid")
    _git(workspace, "config", "user.name", "C3G3 Test")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-q", "-m", "exact baseline")
    return workspace, policy, target, test_path, claim_id, selector


def _write_oracle(controller, workspace: Path, test_path: Path, claim_id: str, selector: str, mutate: str | None = None) -> Path:
    snapshot = controller._workspace_snapshot(workspace)
    overlay_payload = (
        test_path.read_text(encoding="utf-8")
        + "\ndef test_new_test_first_oracle():\n    assert False, 'real RED from immutable overlay'\n"
    ).encode("utf-8")
    oracle_root = workspace / ".quality" / "baseline-oracles" / "c3g3"
    oracle_root.mkdir(parents=True, exist_ok=True)
    artifact = oracle_root / "overlay.zip"
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("tests/test_oracle_target.py", overlay_payload)
    identity: dict[str, object] = {
        "schema_version": 1,
        "oracle_id": "c3g3-test-first-oracle",
        "base_source_identity": _git(workspace, "rev-parse", "HEAD"),
        "base_workspace_fingerprint": snapshot["fingerprint"],
        "overlay_artifact_sha256": _sha256_file(artifact),
        "overlay_file_map": [{
            "path": "tests/test_oracle_target.py",
            "base_file_sha256": snapshot["files"]["tests/test_oracle_target.py"],
            "overlay_file_sha256": _sha256_bytes(overlay_payload),
        }],
        "claim_bindings": [{"claim_id": claim_id, "selector": selector}],
        "provenance": {
            "repository": "test/c3g3", "run_id": "1", "artifact_id": "1",
            "artifact_digest": "sha256:test-provenance",
        },
        "execution_mode": "ephemeral_overlay_view",
    }
    if mutate == "base-workspace-fingerprint":
        identity["base_workspace_fingerprint"] = "0" * 64
    elif mutate == "base-source-identity":
        identity["base_source_identity"] = "0" * 40
    elif mutate == "base-file-sha":
        identity["overlay_file_map"][0]["base_file_sha256"] = "3" * 64  # type: ignore[index]
    elif mutate == "artifact-sha":
        identity["overlay_artifact_sha256"] = "2" * 64
    elif mutate == "overlay-file-sha":
        identity["overlay_file_map"][0]["overlay_file_sha256"] = "1" * 64  # type: ignore[index]
    elif mutate == "selector-binding":
        identity["claim_bindings"][0]["selector"] = "tests/test_oracle_target.py::test_preexisting_control"  # type: ignore[index]
    manifest = dict(identity)
    manifest["overlay_artifact"] = ".quality/baseline-oracles/c3g3/overlay.zip"
    manifest["canonical_fingerprint"] = _canonical(identity)
    manifest_path = oracle_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


class BaselineOracleOverlayTest(unittest.TestCase):
    def test_oracle_replays_missing_selector_and_preserves_exact_source(self) -> None:
        controller = _load_controller()
        with tempfile.TemporaryDirectory() as directory:
            workspace, policy, target, test_path, claim_id, selector = _build_workspace(Path(directory))
            before_file = _sha256_file(test_path)
            before_snapshot = controller._workspace_snapshot(workspace)
            with self.assertRaisesRegex(ValueError, "test selector does not exist"):
                controller.run_loop(
                    workspace, policy, mode="static", evidence_dir=workspace / ".quality/without-oracle",
                    rerun_from=None, target_path=target, baseline=True, baseline_evidence=None,
                    prior_evidence=None, state_dir=workspace / ".quality/state-without-oracle",
                )
            manifest = _write_oracle(controller, workspace, test_path, claim_id, selector)
            evidence = workspace / ".quality/with-oracle"
            summary = controller.run_loop(
                workspace, policy, mode="static", evidence_dir=evidence, rerun_from=None,
                target_path=target, baseline=True, baseline_evidence=None, prior_evidence=None,
                state_dir=workspace / ".quality/state-with-oracle", baseline_oracle=manifest,
            )
            self.assertEqual(summary["decision"], controller.FAIL)
            self.assertEqual(summary["loop_status"], "BASELINE_RECORDED")
            claim = next(item for item in summary["claim_results"] if item["id"] == claim_id)
            self.assertEqual(claim["status"], "FAILED")
            self.assertEqual(claim["evidence_ref_statuses"][selector], "FAILED")
            record = json.loads((evidence / "baseline-record.json").read_text(encoding="utf-8"))
            self.assertEqual(record["workspace_snapshot_fingerprint"], before_snapshot["fingerprint"])
            self.assertEqual(
                record["baseline_oracle_overlay_identity"]["canonical_fingerprint"],
                summary["baseline_oracle_overlay_identity"]["canonical_fingerprint"],
            )
            self.assertEqual(_sha256_file(test_path), before_file)
            self.assertEqual(controller._workspace_snapshot(workspace)["fingerprint"], before_snapshot["fingerprint"])
            self.assertNotIn("test_new_test_first_oracle", test_path.read_text(encoding="utf-8"))

    def test_oracle_identity_components_fail_closed(self) -> None:
        controller = _load_controller()
        cases = {
            "base-workspace-fingerprint": "base_workspace_fingerprint",
            "base-source-identity": "base_source_identity does not match workspace HEAD",
            "base-file-sha": "base file is not bound to source snapshot",
            "artifact-sha": "overlay_artifact_sha256 does not match artifact bytes",
            "overlay-file-sha": "overlay file digest mismatch",
            "selector-binding": "selector is not declared by transition claim",
        }
        for mutation, message in cases.items():
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                workspace, policy, target, test_path, claim_id, selector = _build_workspace(Path(directory))
                manifest = _write_oracle(controller, workspace, test_path, claim_id, selector, mutate=mutation)
                with self.assertRaisesRegex(ValueError, message):
                    controller.run_loop(
                        workspace, policy, mode="static", evidence_dir=workspace / f".quality/bad-{mutation}",
                        rerun_from=None, target_path=target, baseline=True, baseline_evidence=None,
                        prior_evidence=None, state_dir=workspace / f".quality/state-{mutation}",
                        baseline_oracle=manifest,
                    )

    def test_oracle_is_baseline_only(self) -> None:
        controller = _load_controller()
        with tempfile.TemporaryDirectory() as directory:
            workspace, policy, target, test_path, claim_id, selector = _build_workspace(Path(directory))
            manifest = _write_oracle(controller, workspace, test_path, claim_id, selector)
            with self.assertRaisesRegex(ValueError, "valid only together with --baseline"):
                controller.run_loop(
                    workspace, policy, mode="static", evidence_dir=workspace / ".quality/verification",
                    rerun_from=None, target_path=target, baseline=False, baseline_evidence=None,
                    prior_evidence=None, state_dir=workspace / ".quality/state-verification",
                    baseline_oracle=manifest,
                )

    def test_product_bridge_exposes_oracle_transport_only_on_baseline(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-B", str(BRIDGE), "baseline", "--help"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--baseline-oracle", completed.stdout)
        verify_help = subprocess.run(
            [sys.executable, "-B", str(BRIDGE), "verify", "--help"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(verify_help.returncode, 0, verify_help.stderr)
        self.assertNotIn("--baseline-oracle", verify_help.stdout)


if __name__ == "__main__":
    unittest.main()
