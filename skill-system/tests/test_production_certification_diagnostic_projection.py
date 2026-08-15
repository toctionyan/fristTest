from __future__ import annotations

from pathlib import Path
import sys

import pytest


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPTS = WORKSPACE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_production_certification_bundle as CERT  # noqa: E402


def test_failed_real_model_component_projects_only_redacted_bounded_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "diagnostic-secret-value"
    fingerprint = "a" * 64

    monkeypatch.setattr(CERT, "validate_runtime_evidence", lambda *args, **kwargs: None)
    monkeypatch.setattr(CERT, "workspace_fingerprint", lambda _workspace: "b" * 64)

    def runner(**kwargs):
        assert kwargs["component"] == "real_model"
        return {
            "contract": "production-real-model-certification@1",
            "status": "FAIL",
            "reason": "real_model_certification_component_failed",
            "error_code": "component_failed",
            "production_session": {"must_not_escape": secret},
            "real_model_bundle": {
                "status": "FAIL",
                "reason": "real_model_certification_component_failed",
                "failed_component": "semantic",
                "error_code": "semantic_provider_error",
                "component_diagnostic": {
                    "reason": "provider_request_failed",
                    "error_code": "provider_http_error",
                    "error_type": "RuntimeError",
                    "error": f"provider rejected credential {secret}",
                    "raw_response": f"must not escape {secret}",
                },
            },
        }

    result = CERT.run_production_certification_bundle(
        workspace_root=WORKSPACE,
        env={
            CERT.TOOLCHAIN_EVIDENCE_ENV: str(tmp_path / "toolchain.json"),
            CERT.TOOLCHAIN_FINGERPRINT_ENV: fingerprint,
            "OPENAI_API_KEY": secret,
        },
        component_runner=runner,
    )

    assert result["status"] == "FAIL"
    assert result["failed_component"] == "real_model"
    assert result["component_diagnostic"]["failed_component"] == "semantic"
    assert result["component_diagnostic"]["error_code"] == "semantic_provider_error"
    assert result["component_diagnostic"]["detail"]["error_code"] == "provider_http_error"
    assert "***" in result["component_diagnostic"]["detail"]["error"]
    assert secret not in repr(result)
    assert "raw_response" not in repr(result)
    assert "production_session" not in repr(result)
