from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load_module():
    path = SCRIPTS / "verify_production_certification_bundle.py"
    spec = importlib.util.spec_from_file_location("production_component_failure_evidence_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_safe_component_failure_preserves_nested_real_model_code_without_raw_data() -> None:
    module = _load_module()

    result = module._safe_component_failure(
        "real_model",
        {
            "status": "FAIL",
            "reason": "real_model_certification_component_failed",
            "error_code": "component_failed",
            "real_model_bundle": {
                "failed_component": "smoke",
                "error_code": "dynamic_challenge_mismatch",
                "component_failure": {
                    "component": "smoke",
                    "status": "FAIL",
                    "reason": "real_model_attestation_invalid",
                    "error_code": "dynamic_challenge_mismatch",
                    "error_type": "RealModelCertificationError",
                    "raw_response": "must-not-propagate",
                    "api_key": "must-not-propagate",
                },
            },
            "stdout": "must-not-propagate",
            "stderr": "must-not-propagate",
        },
    )

    assert result == {
        "component": "real_model",
        "status": "FAIL",
        "reason": "real_model_certification_component_failed",
        "error_code": "dynamic_challenge_mismatch",
        "failed_subcomponent": "smoke",
        "subcomponent_failure": {
            "component": "smoke",
            "status": "FAIL",
            "reason": "real_model_attestation_invalid",
            "error_code": "dynamic_challenge_mismatch",
            "error_type": "RealModelCertificationError",
        },
    }
    serialized = str(result)
    assert "raw_response" not in serialized
    assert "api_key" not in serialized
    assert "stdout" not in serialized
    assert "stderr" not in serialized


def test_production_bundle_exposes_safe_nested_real_model_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    toolchain = tmp_path / "toolchain.json"
    toolchain.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "validate_runtime_evidence", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "workspace_fingerprint", lambda _workspace: "a" * 64)

    def runner(**kwargs):
        assert kwargs["component"] == "real_model"
        return {
            "status": "FAIL",
            "reason": "real_model_certification_component_failed",
            "real_model_bundle": {
                "failed_component": "smoke",
                "error_code": "provider_model_metadata_mismatch",
            },
        }

    result = module.run_production_certification_bundle(
        workspace_root=ROOT,
        env={
            module.TOOLCHAIN_EVIDENCE_ENV: str(toolchain),
            module.TOOLCHAIN_FINGERPRINT_ENV: "b" * 64,
        },
        component_runner=runner,
    )

    assert result["status"] == "FAIL"
    assert result["failed_component"] == "real_model"
    assert result["component_error_code"] == "provider_model_metadata_mismatch"
    assert result["component_failure"]["failed_subcomponent"] == "smoke"
