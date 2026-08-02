#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected exactly one replacement, found {count}: {old[:120]!r}"
        )
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    path = "services/agent-service/src/agent_core/model_calls/real_model_certification_bundle.py"
    marker = '''def run_certification_bundle(
    *,
    workspace_root: Path,
    env: Mapping[str, str] | None = None,
    component_runner: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
'''
    helper = '''def _safe_failed_component(
    component: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return bounded component diagnostics without retaining model or user text."""

    raw_error = str(payload.get("error") or "")
    error_code = str(payload.get("error_code") or "").strip()
    if not error_code:
        lifecycle_patterns = (
            ("real-model turn did not reach a safe answer", "lifecycle_safe_answer_not_reached"),
            ("real-model answer has neither narrative nor structured presentation", "lifecycle_answer_content_missing"),
            ("order-list turn did not publish structured evidence", "lifecycle_structured_evidence_missing"),
            ("dependent evidence turn degraded to a notice", "lifecycle_dependent_turn_degraded_to_notice"),
            ("public response leaked internal runtime fields", "lifecycle_public_response_leak"),
            ("durable transcript is incomplete", "lifecycle_transcript_incomplete"),
            ("read-only real-model canary created or removed a transaction", "lifecycle_transaction_delta_invalid"),
            ("completed lifecycle turn did not persist any model call records", "lifecycle_model_calls_missing"),
            ("completed lifecycle turn did not contain an attestable model call", "lifecycle_model_calls_missing"),
        )
        if component == "lifecycle":
            error_code = next(
                (code for marker, code in lifecycle_patterns if marker in raw_error),
                "lifecycle_component_failed",
            )
        else:
            error_code = f"{component}_component_failed"

    result: dict[str, Any] = {
        "component": component,
        "status": str(payload.get("status") or "FAIL"),
        "reason": str(payload.get("reason") or f"{component}_certification_failed"),
        "error_code": error_code,
    }
    for key in ("error_type", "error_category"):
        value = str(payload.get(key) or "").strip()
        if value:
            result[key] = value
    return result


''' + marker
    replace_once(path, marker, helper)

    old_blocked = '''        if status == "BLOCKED_BY_ENVIRONMENT":
            return {
                "contract": _BUNDLE_CONTRACT,
                "status": "BLOCKED_BY_ENVIRONMENT",
                "reason": str(payload.get("reason") or "real_model_environment_unavailable"),
                "error_code": str(payload.get("error_code") or "component_environment_blocked"),
                "blocked_component": component,
                "component_launch_count": launched,
            }
        if status != "PASS":
            return {
                "contract": _BUNDLE_CONTRACT,
                "status": "FAIL",
                "reason": "real_model_certification_component_failed",
                "failed_component": component,
                "error_code": str(payload.get("error_code") or "component_failed"),
                "component_launch_count": launched,
            }
'''
    new_blocked = '''        if status == "BLOCKED_BY_ENVIRONMENT":
            failure = _safe_failed_component(component, payload)
            return {
                "contract": _BUNDLE_CONTRACT,
                "status": "BLOCKED_BY_ENVIRONMENT",
                "reason": str(payload.get("reason") or "real_model_environment_unavailable"),
                "error_code": failure["error_code"],
                "blocked_component": component,
                "component_failure": failure,
                "component_launch_count": launched,
            }
        if status != "PASS":
            failure = _safe_failed_component(component, payload)
            return {
                "contract": _BUNDLE_CONTRACT,
                "status": "FAIL",
                "reason": "real_model_certification_component_failed",
                "failed_component": component,
                "error_code": failure["error_code"],
                "component_failure": failure,
                "component_launch_count": launched,
            }
'''
    replace_once(path, old_blocked, new_blocked)

    test_path = ROOT / "services/agent-service/tests/runtime/test_real_model_bundle_safe_failure_diagnostics.py"
    if test_path.exists():
        raise SystemExit(f"test already exists: {test_path}")
    test_path.write_text(
        '''from __future__ import annotations

from agent_core.model_calls.real_model_certification_bundle import _safe_failed_component


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
''',
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
