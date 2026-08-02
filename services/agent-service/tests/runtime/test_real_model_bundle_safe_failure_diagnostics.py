from __future__ import annotations

from agent_core.model_calls.real_model_certification_bundle import _safe_failed_component


# Raw failure bodies are process-local diagnostics and must never cross the bundle boundary.
def test_lifecycle_failure_diagnostic_is_precise_and_drops_raw_response() -> None:
    raw = (
        "dependent evidence turn degraded to a notice: "
        "secret-model-output-and-customer-text-must-not-escape"
    )
    result = _safe_failed_component(
        "lifecycle",
        {"status": "FAIL", "error_type": "RuntimeError", "error": raw},
    )

    assert result == {
        "component": "lifecycle",
        "status": "FAIL",
        "reason": "lifecycle_certification_failed",
        "error_code": "lifecycle_dependent_turn_degraded_to_notice",
        "error_type": "RuntimeError",
    }
    assert "secret-model-output" not in str(result)
    assert "customer-text" not in str(result)


def test_unknown_lifecycle_failure_remains_bounded() -> None:
    result = _safe_failed_component(
        "lifecycle",
        {
            "status": "FAIL",
            "error_type": "RuntimeError",
            "error": "unknown raw provider response must not escape",
        },
    )

    assert result["error_code"] == "lifecycle_component_failed"
    assert "unknown raw provider response" not in str(result)


def test_existing_safe_error_code_is_preserved() -> None:
    result = _safe_failed_component(
        "lifecycle",
        {
            "status": "FAIL",
            "reason": "real_model_attestation_invalid",
            "error_code": "lifecycle_model_calls_missing",
            "error_type": "RealModelCertificationError",
        },
    )

    assert result["error_code"] == "lifecycle_model_calls_missing"
    assert result["reason"] == "real_model_attestation_invalid"
