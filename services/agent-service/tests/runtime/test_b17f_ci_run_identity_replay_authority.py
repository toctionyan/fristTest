from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_identity():
    return _load("release_run_identity_b17f", "scripts/release_run_identity.py")


def _toolchain():
    return _load("release_toolchain_contract_b17f", "scripts/release_toolchain_contract.py")


def _release_controller():
    return _load("run_production_release_b17f", "scripts/run_production_release.py")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "release@example.test")
    _git(repo, "config", "user.name", "Release Test")
    (repo / "VERSION").write_text("20.6.1\n", encoding="utf-8")
    _git(repo, "add", "VERSION")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", "https://github.com/owner/repo.git")
    return repo, _git(repo, "rev-parse", "HEAD")


def _env(sha: str, *, attempt: str = "1") -> dict[str, str]:
    return {
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REPOSITORY_ID": "123456",
        "GITHUB_SHA": sha,
        "GITHUB_WORKFLOW_SHA": sha,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_TYPE": "branch",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_WORKFLOW": "production-certification-release",
        "GITHUB_WORKFLOW_REF": "owner/repo/.github/workflows/release.yml@refs/heads/main",
        "GITHUB_JOB": "protected-release",
        "GITHUB_RUN_ID": "987654321",
        "GITHUB_RUN_ATTEMPT": attempt,
        "GITHUB_RUN_NUMBER": "42",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_API_URL": "https://api.github.com",
        "PRODUCTION_RELEASE_EXPECTED_EVENT": "workflow_dispatch",
        "PRODUCTION_RELEASE_EXPECTED_WORKFLOW": "production-certification-release",
        "PRODUCTION_RELEASE_EXPECTED_JOB": "protected-release",
        "PRODUCTION_RELEASE_EXPECTED_REF": "refs/heads/main",
    }


def _identity(run_fingerprint: str) -> dict[str, str]:
    return {
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "credential_fingerprint_sha256_16": "0123456789abcdef",
        "toolchain_fingerprint_sha256": "d" * 64,
        "run_identity_fingerprint_sha256": run_fingerprint,
    }


def _summary(run_fingerprint: str) -> dict:
    identity = _identity(run_fingerprint)
    return {
        "mode": "release",
        "decision": "PASS",
        "loop_status": "CI_VERIFIED",
        "completion_eligible": True,
        "missing_prerequisites": [],
        "unverified_claim_ids": [],
        "workspace_snapshot_fingerprint": "a" * 64,
        "ci_run_identity_fingerprint_sha256": run_fingerprint,
        "quality_dimensions": {
            "production_certification": {
                "status": "PASS",
                "contract": "production-certification-dimension@1",
                "session_id": "prodcert-" + "b" * 48,
                "workspace_fingerprint_sha256": "c" * 64,
                "toolchain_fingerprint_sha256": "d" * 64,
                "real_model_identity": identity,
            },
            "real_model_certification": {
                "status": "PASS",
                "contract": "real-model-certification-dimension@3",
                "bundle_contract": "production-certification-bundle@1",
                "session_id": "prodcert-" + "b" * 48,
                "workspace_fingerprint_sha256": "c" * 64,
                "toolchain_fingerprint_sha256": "d" * 64,
                "identity": identity,
            },
        },
    }


def test_protected_clean_checkout_produces_run_identity(tmp_path: Path) -> None:
    contract = _run_identity()
    repo, sha = _repository(tmp_path)
    payload = contract.capture_run_identity(repo, env=_env(sha))
    assert payload["status"] == "PASS"
    assert payload["commit_sha"] == sha
    assert payload["ref_protected"] is True
    assert payload["checkout"]["clean"] is True
    assert len(payload["run_identity_fingerprint_sha256"]) == 64


def test_unprotected_ref_is_rejected_before_certification(tmp_path: Path) -> None:
    contract = _run_identity()
    repo, sha = _repository(tmp_path)
    env = _env(sha)
    env["GITHUB_REF_PROTECTED"] = "false"
    with pytest.raises(Exception) as exc_info:
        contract.capture_run_identity(repo, env=env)
    assert exc_info.value.code == "release_ref_unprotected"


def test_checkout_head_must_equal_github_sha(tmp_path: Path) -> None:
    contract = _run_identity()
    repo, sha = _repository(tmp_path)
    wrong = "0" * 40 if sha != "0" * 40 else "1" * 40
    env = _env(wrong)
    env["GITHUB_WORKFLOW_SHA"] = wrong
    with pytest.raises(Exception) as exc_info:
        contract.capture_run_identity(repo, env=env)
    assert exc_info.value.code == "release_checkout_commit_mismatch"


def test_dirty_checkout_is_rejected(tmp_path: Path) -> None:
    contract = _run_identity()
    repo, sha = _repository(tmp_path)
    (repo / "VERSION").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(Exception) as exc_info:
        contract.capture_run_identity(repo, env=_env(sha))
    assert exc_info.value.code == "release_checkout_dirty"


def test_persisted_checkout_credentials_are_rejected(tmp_path: Path) -> None:
    contract = _run_identity()
    repo, sha = _repository(tmp_path)
    _git(repo, "config", "--local", "http.https://github.com/.extraheader", "AUTHORIZATION: basic secret")
    with pytest.raises(Exception) as exc_info:
        contract.capture_run_identity(repo, env=_env(sha))
    assert exc_info.value.code == "release_checkout_credentials_persisted"


def test_run_attempt_changes_identity_even_for_same_commit(tmp_path: Path) -> None:
    contract = _run_identity()
    repo, sha = _repository(tmp_path)
    first = contract.capture_run_identity(repo, env=_env(sha, attempt="1"), validate_git=False)
    second = contract.capture_run_identity(repo, env=_env(sha, attempt="2"), validate_git=False)
    assert first["commit_sha"] == second["commit_sha"]
    assert first["run_identity_fingerprint_sha256"] != second["run_identity_fingerprint_sha256"]


def test_prior_attempt_quality_summary_cannot_close_current_run() -> None:
    controller = _release_controller()
    current = "f" * 64
    prior = "e" * 64
    with pytest.raises(Exception, match="release_run_identity_replay_detected"):
        controller.validate_release_summary(
            _summary(prior),
            expected_identity=_identity(current),
        )


def test_toolchain_evidence_without_run_identity_is_rejected(tmp_path: Path) -> None:
    contract = _toolchain()
    payload = {
        "contract": contract.CONTRACT,
        "status": "PASS",
        "static_contract": contract.validate_static_contract(ROOT),
    }
    payload["toolchain_fingerprint_sha256"] = contract._canonical_sha256(payload)
    evidence = tmp_path / "toolchain.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(Exception) as exc_info:
        contract.validate_runtime_evidence(
            ROOT,
            evidence,
            expected_fingerprint=payload["toolchain_fingerprint_sha256"],
            validate_live_runtime=False,
        )
    assert exc_info.value.code == "release_run_identity_missing"


def test_release_workflow_binds_protected_ref_checkout_and_artifact_attempt() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "github.ref_protected == true" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "PRODUCTION_CERTIFICATION_RUN_IDENTITY_FINGERPRINT" in workflow
    assert "production-certification-evidence-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "production-closed-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
