from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "release_admission_contract.py"


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_env(**overrides: str) -> dict[str, str]:
    payload = {
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_TYPE": "branch",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_WORKFLOW": "production-certification-release",
        "GITHUB_RUN_ID": "123456",
        "GITHUB_RUN_ATTEMPT": "2",
        "PRODUCTION_RELEASE_EXPECTED_EVENT": "workflow_dispatch",
        "PRODUCTION_RELEASE_EXPECTED_WORKFLOW": "production-certification-release",
        "PRODUCTION_RELEASE_EXPECTED_REF": "refs/heads/main",
        "RELEASE_INPUT_PROVIDER": "deepseek",
        "RELEASE_INPUT_MODEL": "deepseek-v4-flash",
        "RELEASE_INPUT_EMBEDDING_MODEL": "text-embedding-3-small",
        "RELEASE_INPUT_EMBEDDING_DIMENSION": "1536",
    }
    payload.update(overrides)
    return payload


def _run(output: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ)
    merged.update(env)
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--output", str(output)],
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


def test_admission_cli_persists_pass_result_atomically(tmp_path: Path) -> None:
    output = tmp_path / "release-admission-result.json"
    completed = _run(output, _valid_env())
    assert completed.returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["contract"] == "release-workflow-admission@1"
    assert payload["status"] == "PASS"
    assert payload["run_id"] == "123456"
    assert payload["run_attempt"] == "2"
    assert payload["credential_values_emitted"] is False
    assert list(tmp_path.glob("*.tmp")) == []


def test_admission_cli_persists_fail_result_for_unprotected_ref(tmp_path: Path) -> None:
    output = tmp_path / "release-admission-result.json"
    completed = _run(output, _valid_env(GITHUB_REF_PROTECTED="false"))
    assert completed.returncode == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "release_admission_ref_unprotected"
    assert payload["git_ref"] == "refs/heads/main"
    assert payload["credential_values_emitted"] is False


def test_admission_cli_persists_environment_block_without_credentials(tmp_path: Path) -> None:
    output = tmp_path / "release-admission-result.json"
    completed = _run(output, {})
    assert completed.returncode == 78
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert payload["reason"] == "release_admission_ci_context_missing"
    serialized = json.dumps(payload, sort_keys=True)
    assert "PRODUCTION_MODEL_API_KEY" not in serialized
    assert "PRODUCTION_EMBEDDING_API_KEY" not in serialized
    assert "QUALITY_EVIDENCE_SIGNING_KEY" not in serialized


def test_workflow_always_uploads_secret_free_admission_result() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    admission = workflow.split("  release-admission:", 1)[1].split("  protected-release:", 1)[0]
    assert "--output \"$RELEASE_ADMISSION_RESULT\"" in admission
    assert "Upload sanitized release admission evidence" in admission
    assert "if: always()" in admission
    assert "production-release-admission-${{ github.run_id }}-${{ github.run_attempt }}" in admission
    assert "${{ runner.temp }}/release-admission-result.json" in admission
    assert "if-no-files-found: error" in admission
    assert "secrets." not in admission
    assert "environment:" not in admission


def test_supply_chain_contract_locks_admission_evidence() -> None:
    module = _load("release_toolchain_contract_b17i", "scripts/release_toolchain_contract.py")
    result = module.validate_static_contract(ROOT)
    assert result["status"] == "PASS"
    assert result["release_admission"] == {
        "artifact_name_prefix": "production-release-admission",
        "always_upload": True,
        "contract": "release-workflow-admission@1",
        "fail_closed_on_unprotected_ref": True,
        "job": "release-admission",
        "protected_job": "protected-release",
        "sanitized_result_artifact": "release-admission-result.json",
    }


def test_final_runbook_contains_repository_environment_and_artifact_checks() -> None:
    text = (ROOT / "docs/operations/B17I_FINAL_PRODUCTION_EXECUTION_RUNBOOK.md").read_text(encoding="utf-8")
    for required in (
        "production-certification",
        "PRODUCTION_MODEL_API_KEY",
        "PRODUCTION_EMBEDDING_API_KEY",
        "QUALITY_EVIDENCE_SIGNING_KEY",
        "production-release-admission-",
        "production-certification-evidence-",
        "production-closed-",
        "production_closed",
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ):
        assert required in text
