from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _closed_ledger(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "stages": [{"stage_id": "STAGE-5", "status": "CLOSED_VERIFIED"}],
        "work_packages": [{"work_package_id": "WP-08", "status": "CLOSED_VERIFIED"}],
    }), encoding="utf-8")


def _fake_runner(command, *, cwd: Path):
    command = [str(item) for item in command]
    if command[0] == "git":
        key = tuple(command[1:])
        values = {
            ("rev-parse", "--show-toplevel"): str(cwd),
            ("branch", "--show-current"): "main",
            ("rev-parse", "HEAD"): "a" * 40,
            ("status", "--porcelain=v1", "--untracked-files=all"): "",
            ("remote", "get-url", "origin"): "https://github.com/owner/repo.git",
        }
        return subprocess.CompletedProcess(command, 0, values[key] + ("\n" if values[key] else ""), "")
    return subprocess.CompletedProcess(command, 0, f"{Path(command[0]).name} 1.2.3\n", "")


def test_host_preflight_passes_only_with_closed_wp08_real_hosts_and_clean_main(tmp_path: Path) -> None:
    module = _load("host_execution_preflight.py")
    _closed_ledger(tmp_path / "governance/task-ledger.json")
    tools = tmp_path / "tools"; tools.mkdir()
    for name in ("codex", "claude"):
        path = tools / name; path.write_text("#!/bin/sh\n", encoding="utf-8"); path.chmod(0o755)
    result = module.evaluate(
        tmp_path,
        which=lambda name: str(tools / name) if (tools / name).exists() else None,
        runner=_fake_runner,
        host_conformance=lambda workspace: [],
    )
    assert result["status"] == "PASS"
    assert set(result["tools"]) == {"codex", "claude"}
    assert result["production_closed"] is False


def test_host_preflight_records_missing_hosts_and_open_stage_without_crashing(tmp_path: Path) -> None:
    module = _load("host_execution_preflight.py")
    (tmp_path / "governance").mkdir()
    (tmp_path / "governance/task-ledger.json").write_text(json.dumps({
        "stages": [{"stage_id": "STAGE-5", "status": "BLOCKED"}],
        "work_packages": [{"work_package_id": "WP-08", "status": "BLOCKED"}],
    }), encoding="utf-8")
    result = module.evaluate(tmp_path, which=lambda name: None, runner=_fake_runner, host_conformance=lambda workspace: [])
    assert result["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert "stage5_not_closed" in result["blockers"]
    assert "wp08_not_closed" in result["blockers"]
    assert "host_binary_missing:codex" in result["blockers"]
    assert "host_binary_missing:claude" in result["blockers"]


def test_host_preflight_fails_on_adapter_drift_even_when_binaries_exist(tmp_path: Path) -> None:
    module = _load("host_execution_preflight.py")
    _closed_ledger(tmp_path / "governance/task-ledger.json")
    tool = tmp_path / "tool"; tool.write_text("x", encoding="utf-8"); tool.chmod(0o755)
    result = module.evaluate(
        tmp_path, which=lambda name: str(tool), runner=_fake_runner,
        host_conformance=lambda workspace: ["codex_agent_not_read_only:release-judge"],
    )
    assert result["status"] == "FAIL"
    assert result["errors"] == ["codex_agent_not_read_only:release-judge"]


def _closure_fixture(tmp_path: Path) -> dict[str, Path | str]:
    repository = "owner/repo"; commit = "a" * 40; run_id = "123"; attempt = "2"
    identity = {
        "contract": "release-run-identity@1", "status": "PASS", "event_name": "workflow_dispatch",
        "repository": repository, "repository_id": "1", "workflow": "production-certification-release",
        "workflow_ref": f"{repository}/.github/workflows/release.yml@refs/heads/main",
        "workflow_file": ".github/workflows/release.yml", "workflow_sha": commit,
        "job": "protected-release", "git_ref": "refs/heads/main", "ref_type": "branch",
        "ref_protected": True, "commit_sha": commit, "run_id": run_id, "run_attempt": attempt,
        "run_number": "9", "server_url": "https://github.com", "api_url": "https://api.github.com",
        "checkout": {"head_sha": commit, "origin": f"https://github.com/{repository}", "clean": True, "credential_headers_present": False},
    }
    identity["run_identity_fingerprint_sha256"] = _canonical(identity)
    toolchain = {"contract": "release-toolchain-provenance@1", "status": "PASS", "versions": {"python": "3.12.13"}, "ci_run_identity": identity}
    toolchain["toolchain_fingerprint_sha256"] = _canonical(toolchain)
    toolchain_path = tmp_path / "release-toolchain-provenance.json"
    toolchain_path.write_text(json.dumps(toolchain), encoding="utf-8")

    artifact_dir = tmp_path / "artifacts"; artifact_dir.mkdir()
    base = "customer_agent_workspace_v20_17_production_closed"
    source = artifact_dir / f"{base}.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(f"{base}/VERSION", "20.6.1\n")
    sidecar = artifact_dir / f"{base}.zip.sha256"
    sidecar.write_text(f"{hashlib.sha256(source.read_bytes()).hexdigest()}  {source.name}\n", encoding="utf-8")
    evidence = artifact_dir / f"{base}-quality-evidence.zip"
    summary = {"decision": "PASS", "loop_status": "CI_VERIFIED", "ci_run_identity_fingerprint_sha256": identity["run_identity_fingerprint_sha256"]}
    with zipfile.ZipFile(evidence, "w") as archive:
        archive.writestr("quality-evidence/run-summary.json", json.dumps(summary))
    rows = [
        {"kind": "protected-source", "filename": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "size_bytes": source.stat().st_size},
        {"kind": "quality-evidence", "filename": evidence.name, "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(), "size_bytes": evidence.stat().st_size},
        {"kind": "source-sha256-sidecar", "filename": sidecar.name, "sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(), "size_bytes": sidecar.stat().st_size},
    ]
    result = {
        "contract": "production-release-execution@2", "status": "PASS", "stage": "closed",
        "reason": "production_release_closed", "authority_gate": "production-certification-bundle",
        "identity": {"repository": repository, "commit_sha": commit, "workflow_run_id": run_id,
            "workflow_run_attempt": attempt, "git_ref": "refs/heads/main",
            "run_identity_fingerprint_sha256": identity["run_identity_fingerprint_sha256"],
            "toolchain_fingerprint_sha256": toolchain["toolchain_fingerprint_sha256"]},
        "artifacts": rows,
    }
    result_path = tmp_path / "production-release-result.json"; result_path.write_text(json.dumps(result), encoding="utf-8")
    return {"repository": repository, "commit": commit, "run_id": run_id, "attempt": attempt,
            "result": result_path, "artifact_dir": artifact_dir, "toolchain": toolchain_path, "source": source}


def _verify(module, fixture: dict[str, Path | str]):
    return module.verify(
        fixture["result"], fixture["artifact_dir"], toolchain_evidence_path=fixture["toolchain"],
        expected_repository=str(fixture["repository"]), expected_commit=str(fixture["commit"]),
        expected_run_id=str(fixture["run_id"]), expected_run_attempt=str(fixture["attempt"]),
    )


def test_production_closure_consumer_accepts_only_exact_same_run_artifacts(tmp_path: Path) -> None:
    module = _load("verify_production_closure_artifact.py")
    fixture = _closure_fixture(tmp_path)
    result = _verify(module, fixture)
    assert result["status"] == "PASS"
    assert result["production_closed"] is True


def test_production_closure_consumer_rejects_wrong_commit(tmp_path: Path) -> None:
    module = _load("verify_production_closure_artifact.py")
    fixture = _closure_fixture(tmp_path)
    with pytest.raises(module.ClosureArtifactError, match="commit_sha"):
        module.verify(
            fixture["result"], fixture["artifact_dir"], toolchain_evidence_path=fixture["toolchain"],
            expected_repository=str(fixture["repository"]), expected_commit="b" * 40,
            expected_run_id=str(fixture["run_id"]), expected_run_attempt=str(fixture["attempt"]),
        )


def test_production_closure_consumer_rejects_tampered_artifact(tmp_path: Path) -> None:
    module = _load("verify_production_closure_artifact.py")
    fixture = _closure_fixture(tmp_path)
    Path(fixture["source"]).write_bytes(Path(fixture["source"]).read_bytes() + b"tamper")
    with pytest.raises(module.ClosureArtifactError, match="digest mismatch"):
        _verify(module, fixture)


def test_production_closure_consumer_rejects_unsafe_zip_entry(tmp_path: Path) -> None:
    module = _load("verify_production_closure_artifact.py")
    fixture = _closure_fixture(tmp_path)
    source = Path(fixture["source"])
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("../escape", "bad")
    result = json.loads(Path(fixture["result"]).read_text())
    row = next(item for item in result["artifacts"] if item["kind"] == "protected-source")
    row["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest(); row["size_bytes"] = source.stat().st_size
    Path(fixture["result"]).write_text(json.dumps(result), encoding="utf-8")
    sidecar = Path(fixture["artifact_dir"]) / "customer_agent_workspace_v20_17_production_closed.zip.sha256"
    sidecar.write_text(f"{row['sha256']}  {source.name}\n", encoding="utf-8")
    sidecar_row = next(item for item in result["artifacts"] if item["kind"] == "source-sha256-sidecar")
    sidecar_row["sha256"] = hashlib.sha256(sidecar.read_bytes()).hexdigest(); sidecar_row["size_bytes"] = sidecar.stat().st_size
    Path(fixture["result"]).write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(module.ClosureArtifactError, match="unsafe ZIP entry"):
        _verify(module, fixture)


def test_release_oracle_tracks_current_protected_runtime_steps() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    test_file = (ROOT / "services/agent-service/tests/architecture/test_quality_loop_governance.py").read_text(encoding="utf-8")
    assert "Start actual protected-profile services" not in test_file
    assert "Validate protected runtime prerequisites" in workflow
    assert "Run every release gate" in workflow
