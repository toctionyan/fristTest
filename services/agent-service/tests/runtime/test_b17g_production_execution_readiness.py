from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _admission():
    return _load("release_admission_contract_b17g", "scripts/release_admission_contract.py")


def _toolchain():
    return _load("release_toolchain_contract_b17g", "scripts/release_toolchain_contract.py")


def _env(**overrides: str) -> dict[str, str]:
    payload = {
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_TYPE": "branch",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_WORKFLOW": "production-certification-release",
        "GITHUB_RUN_ID": "123456",
        "GITHUB_RUN_ATTEMPT": "1",
        "PRODUCTION_RELEASE_EXPECTED_EVENT": "workflow_dispatch",
        "PRODUCTION_RELEASE_EXPECTED_WORKFLOW": "production-certification-release",
        "PRODUCTION_RELEASE_EXPECTED_REF": "refs/heads/main",
        "RELEASE_INPUT_PROVIDER": "deepseek",
        "RELEASE_INPUT_MODEL": "deepseek-v4-flash",
        "RELEASE_INPUT_EMBEDDING_MODEL": "text-embedding-v4",
        "RELEASE_INPUT_EMBEDDING_DIMENSION": "1024",
    }
    payload.update(overrides)
    return payload


def test_valid_protected_dispatch_is_admitted() -> None:
    result = _admission().validate_release_admission(_env())
    assert result["contract"] == "release-workflow-admission@1"
    assert result["status"] == "PASS"
    assert result["ref_protected"] is True
    assert result["embedding_dimension"] == 1024


def test_unprotected_dispatch_fails_instead_of_being_silently_skipped() -> None:
    contract = _admission()
    with pytest.raises(contract.ReleaseAdmissionError) as exc_info:
        contract.validate_release_admission(_env(GITHUB_REF_PROTECTED="false"))
    assert exc_info.value.code == "release_admission_ref_unprotected"
    assert exc_info.value.environment_blocked is False


def test_wrong_branch_is_rejected_before_secret_bearing_job() -> None:
    contract = _admission()
    with pytest.raises(contract.ReleaseAdmissionError) as exc_info:
        contract.validate_release_admission(_env(GITHUB_REF="refs/heads/feature"))
    assert exc_info.value.code == "release_admission_ref_mismatch"


def test_blank_model_input_is_rejected_before_expensive_install() -> None:
    contract = _admission()
    with pytest.raises(contract.ReleaseAdmissionError) as exc_info:
        contract.validate_release_admission(_env(RELEASE_INPUT_MODEL="   "))
    assert exc_info.value.code == "release_input_model_missing"


@pytest.mark.parametrize("value", ["0", "65536", "abc", "-1"])
def test_invalid_embedding_dimension_is_rejected(value: str) -> None:
    contract = _admission()
    with pytest.raises(contract.ReleaseAdmissionError) as exc_info:
        contract.validate_release_admission(
            _env(RELEASE_INPUT_EMBEDDING_DIMENSION=value)
        )
    assert exc_info.value.code == "release_admission_embedding_dimension_invalid"


def test_release_workflow_has_explicit_admission_dependency_and_defense_in_depth() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    admission = workflow.split("  release-admission:", 1)[1].split("  protected-release:", 1)[0]
    protected = workflow.split("  protected-release:", 1)[1]
    assert "environment:" not in admission
    assert "secrets." not in admission
    assert "scripts/release_admission_contract.py" in admission
    assert "Fail closed on invalid release admission" in admission
    assert "needs: release-admission" in protected
    assert "github.ref_protected == true" in protected
    assert "environment: production-certification" in protected


def test_supply_chain_contract_includes_release_admission() -> None:
    result = _toolchain().validate_static_contract(ROOT)
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
    assert "scripts/release_admission_contract.py" in result["locked_source_sha256"]


def test_candidate_metadata_preserves_b17g_history_and_identifies_current_phase() -> None:
    manifest = json.loads((ROOT / "release" / "MANIFEST.json").read_text(encoding="utf-8"))
    current_phase = str(manifest["phase"])
    notice = (ROOT / "PHASE_CANDIDATE_NOTICE.md").read_text(encoding="utf-8")
    readme_head = "\n".join((ROOT / "README.md").read_text(encoding="utf-8").splitlines()[:30])
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    b17g_summary = json.loads((ROOT / "B17G_STAGE_SUMMARY.json").read_text(encoding="utf-8"))
    assert current_phase in notice
    assert current_phase in readme_head
    assert f"V20.17 {current_phase}" in "\n".join(changelog.splitlines()[:20])
    assert "V20.17 B17g" in changelog
    assert b17g_summary["phase"] == "B17g"
    assert "本轮没有修改 Agent、Business Service、前端或共享业务合同" not in readme_head
