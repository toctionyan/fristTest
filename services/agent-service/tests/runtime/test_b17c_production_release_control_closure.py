from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load_runner():
    path = ROOT / "scripts" / "run_production_release.py"
    spec = importlib.util.spec_from_file_location("run_production_release_b17c", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _identity() -> dict[str, str]:
    return {
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "credential_fingerprint_sha256_16": hashlib.sha256(
            b"sk-production-value-12345678901234567890"
        ).hexdigest()[:16],
        "toolchain_fingerprint_sha256": "4" * 64,
        "run_identity_fingerprint_sha256": "5" * 64,
    }


def _summary(*, loop_status: str = "CI_VERIFIED") -> dict:
    identity = _identity()
    return {
        "mode": "release",
        "decision": "PASS",
        "loop_status": loop_status,
        "completion_eligible": True,
        "missing_prerequisites": [],
        "unverified_claim_ids": [],
        "workspace_snapshot_fingerprint": "1" * 64,
        "ci_run_identity_fingerprint_sha256": "5" * 64,
        "quality_dimensions": {
            "production_certification": {
                "status": "PASS",
                "contract": "production-certification-dimension@1",
                "session_id": "prodcert-" + "2" * 48,
                "workspace_fingerprint_sha256": "3" * 64,
                "toolchain_fingerprint_sha256": "4" * 64,
                "real_model_identity": identity,
            },
            "real_model_certification": {
                "status": "PASS",
                "contract": "real-model-certification-dimension@3",
                "bundle_contract": "production-certification-bundle@1",
                "session_id": "prodcert-" + "2" * 48,
                "workspace_fingerprint_sha256": "3" * 64,
                "toolchain_fingerprint_sha256": "4" * 64,
                "identity": identity,
            },
        },
    }


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / ".quality" / "targets" / "quality-target-release.md"
    target.parent.mkdir(parents=True)
    target.write_text("release target\n", encoding="utf-8")
    python = workspace / "services" / "agent-service" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    return workspace, target


def _env() -> dict[str, str]:
    return {
        "OPENAI_API_KEY": "sk-production-value-12345678901234567890",
        "OPENAI_MODEL": "gpt-4o-mini",
        "OPENAI_API_BASE": "https://api.openai.com/v1",
        "REAL_MODEL_CERTIFICATION_PROVIDER": "openai",
        "EMBEDDING_PROVIDER": "openai",
        "EMBEDDING_API_KEY": "sk-embedding-value-12345678901234567890",
        "EMBEDDING_API_BASE": "https://api.openai.com/v1",
        "EMBEDDING_MODEL": "text-embedding-3-small",
        "EMBEDDING_DIM": "1536",
        "QUALITY_EVIDENCE_SIGNING_KEY": "s" * 40,
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_WORKFLOW_REF": "owner/repo/.github/workflows/release.yml@refs/heads/main",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REF": "refs/heads/main",
        "PRODUCTION_CERTIFICATION_TOOLCHAIN_EVIDENCE": "/tmp/release-toolchain-provenance.json",
        "PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT": "4" * 64,
        "PRODUCTION_CERTIFICATION_RUN_IDENTITY_FINGERPRINT": "5" * 64,
    }


def _success_runner(summary: dict, *, corrupt_sidecar: bool = False):
    def run(command, *, cwd: Path, env: dict[str, str]) -> int:
        command = [str(item) for item in command]
        evidence = Path(command[command.index("--evidence-dir") + 1])
        if any(item.endswith("quality_loop.py") for item in command):
            (evidence / "run-summary.json").write_text(
                json.dumps(summary), encoding="utf-8"
            )
            return 0
        output = Path(command[command.index("--output-dir") + 1])
        artifact_name = command[command.index("--artifact-name") + 1]
        source = output / f"{artifact_name}.zip"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr(f"{artifact_name}/VERSION", "20.6.1\n")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if corrupt_sidecar:
            digest = "0" * 64
        (output / f"{artifact_name}.zip.sha256").write_text(
            f"{digest}  {artifact_name}.zip\n", encoding="utf-8"
        )
        with zipfile.ZipFile(
            output / f"{artifact_name}-quality-evidence.zip", "w"
        ) as archive:
            archive.writestr("quality-evidence/run-summary.json", json.dumps(summary))
        return 0

    return run


def test_release_controller_imports_without_agent_runtime_dependencies() -> None:
    runner = _load_runner()
    assert runner.CONTRACT == "production-release-execution@2"
    assert callable(runner.resolve_real_model_identity)


def test_ci_verified_summary_closes_against_real_dimension_contracts() -> None:
    runner = _load_runner()
    closure = runner.validate_release_summary(
        _summary(loop_status="CI_VERIFIED"), expected_identity=_identity()
    )
    assert closure["loop_status"] == "CI_VERIFIED"
    assert closure["production_session_id"].startswith("prodcert-")


def test_b17b_legacy_summary_field_cannot_fake_production_closure() -> None:
    runner = _load_runner()
    payload = _summary()
    production = payload["quality_dimensions"]["production_certification"]
    production.pop("contract")
    production["bundle_contract"] = "production-certification-bundle@1"
    with pytest.raises(Exception, match="did not converge"):
        runner.validate_release_summary(payload, expected_identity=_identity())


def test_release_summary_identity_must_match_protected_preflight() -> None:
    runner = _load_runner()
    payload = _summary()
    payload["quality_dimensions"]["production_certification"][
        "real_model_identity"
    ]["model"] = "gpt-different"
    with pytest.raises(Exception, match="release_identity_mismatch"):
        runner.validate_release_summary(payload, expected_identity=_identity())


def test_preflight_failure_writes_sanitized_control_result(monkeypatch, tmp_path: Path) -> None:
    runner = _load_runner()
    workspace, target = _workspace(tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner, "validate_runtime_evidence", lambda *args, **kwargs: {"status": "PASS", "ci_run_identity": {"run_identity_fingerprint_sha256": "5" * 64, "commit_sha": "a" * 40, "run_id": "12345", "run_attempt": "2", "workflow_ref": "owner/repo/.github/workflows/release.yml@refs/heads/main", "repository": "owner/repo", "git_ref": "refs/heads/main"}})
    secret = "sk-never-write-this-secret-12345678901234567890"
    env = _env()
    env["OPENAI_API_KEY"] = secret
    env["QUALITY_EVIDENCE_SIGNING_KEY"] = "short"
    result_path = tmp_path / "control" / "production-release-result.json"
    result = runner.execute_production_release(
        workspace_root=workspace,
        target_path=target,
        evidence_dir=tmp_path / "evidence",
        output_dir=tmp_path / "output",
        artifact_name="customer_agent_workspace_v20_17_production_closed",
        result_path=result_path,
        env=env,
    )
    serialized = result_path.read_text(encoding="utf-8")
    assert result["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert result["stage"] == "preflight"
    assert result["reason"] == "quality_evidence_signing_key_invalid"
    assert secret not in serialized
    assert "ssss" not in serialized
    assert result["artifacts"] == []


def test_success_closes_only_exact_content_addressed_artifact_set(monkeypatch, tmp_path: Path) -> None:
    runner = _load_runner()
    workspace, target = _workspace(tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner, "validate_runtime_evidence", lambda *args, **kwargs: {"status": "PASS", "ci_run_identity": {"run_identity_fingerprint_sha256": "5" * 64, "commit_sha": "a" * 40, "run_id": "12345", "run_attempt": "2", "workflow_ref": "owner/repo/.github/workflows/release.yml@refs/heads/main", "repository": "owner/repo", "git_ref": "refs/heads/main"}})
    result_path = tmp_path / "control" / "production-release-result.json"
    result = runner.execute_production_release(
        workspace_root=workspace,
        target_path=target,
        evidence_dir=tmp_path / "evidence",
        output_dir=tmp_path / "output",
        artifact_name="customer_agent_workspace_v20_17_production_closed",
        result_path=result_path,
        env=_env(),
        command_runner=_success_runner(_summary()),
    )
    assert result["status"] == "PASS"
    assert result["stage"] == "closed"
    assert result["loop_status"] == "CI_VERIFIED"
    assert {item["kind"] for item in result["artifacts"]} == {
        "protected-source",
        "quality-evidence",
        "source-sha256-sidecar",
    }
    assert json.loads(result_path.read_text(encoding="utf-8"))["status"] == "PASS"


def test_corrupt_artifact_sidecar_fails_closed_and_is_recorded(monkeypatch, tmp_path: Path) -> None:
    runner = _load_runner()
    workspace, target = _workspace(tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner, "validate_runtime_evidence", lambda *args, **kwargs: {"status": "PASS", "ci_run_identity": {"run_identity_fingerprint_sha256": "5" * 64, "commit_sha": "a" * 40, "run_id": "12345", "run_attempt": "2", "workflow_ref": "owner/repo/.github/workflows/release.yml@refs/heads/main", "repository": "owner/repo", "git_ref": "refs/heads/main"}})
    result_path = tmp_path / "control" / "production-release-result.json"
    result = runner.execute_production_release(
        workspace_root=workspace,
        target_path=target,
        evidence_dir=tmp_path / "evidence",
        output_dir=tmp_path / "output",
        artifact_name="customer_agent_workspace_v20_17_production_closed",
        result_path=result_path,
        env=_env(),
        command_runner=_success_runner(_summary(), corrupt_sidecar=True),
    )
    assert result["status"] == "FAIL"
    assert result["stage"] == "artifact_validation"
    assert result["reason"] == "protected_artifact_sha256_mismatch"
    assert result["artifacts"] == []


def test_unsafe_result_path_never_mutates_workspace(monkeypatch, tmp_path: Path) -> None:
    runner = _load_runner()
    workspace, target = _workspace(tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner, "validate_runtime_evidence", lambda *args, **kwargs: {"status": "PASS", "ci_run_identity": {"run_identity_fingerprint_sha256": "5" * 64, "commit_sha": "a" * 40, "run_id": "12345", "run_attempt": "2", "workflow_ref": "owner/repo/.github/workflows/release.yml@refs/heads/main", "repository": "owner/repo", "git_ref": "refs/heads/main"}})
    result_path = workspace / "production-release-result.json"
    result = runner.execute_production_release(
        workspace_root=workspace,
        target_path=target,
        evidence_dir=tmp_path / "evidence",
        output_dir=tmp_path / "output",
        artifact_name="customer_agent_workspace_v20_17_production_closed",
        result_path=result_path,
        env=_env(),
        command_runner=_success_runner(_summary()),
    )
    assert result["status"] == "FAIL"
    assert result["reason"] == "release_path_inside_workspace"
    assert not result_path.exists()


def test_release_workflow_uploads_control_ledger_and_exact_claim_manifest() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert '--result-path "$RELEASE_CONTROL_DIR/production-release-result.json"' in workflow
    assert "production-release-control/production-release-result.json" in workflow
    assert ".quality/targets/quality-target-release.claims.json" in workflow
    assert "production-release-target.claims.json" not in workflow
    assert "if-no-files-found: error" in workflow
