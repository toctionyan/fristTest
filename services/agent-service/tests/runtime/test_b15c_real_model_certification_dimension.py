from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_quality_loop():
    path = Path(__file__).resolve().parents[4] / "scripts" / "quality_loop.py"
    spec = importlib.util.spec_from_file_location("quality_loop_b15c", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _identity() -> dict:
    return {
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "credential_fingerprint_sha256_16": "0123456789abcdef",
        "official_endpoint": True,
        "https": True,
    }


def _bundle() -> dict:
    return {
        "contract": "real-model-certification-bundle@1",
        "status": "PASS",
        "session_id": "rmcert-0123456789abcdef0123456789abcdef",
        "workspace_fingerprint_sha256": "a" * 64,
        "identity": _identity(),
        "component_count": 3,
        "components": ["smoke", "semantic", "lifecycle"],
        "attested_model_calls_by_component": {"smoke": 1, "semantic": 12, "lifecycle": 2},
        "total_attested_model_calls": 15,
    }


def _result(gate_id: str, status: str = "PASS", *, assessment: dict | None = None) -> dict:
    metadata = {"structured_assessment": assessment} if assessment is not None else {}
    return {"id": gate_id, "status": status, "category": "preproduction", "metadata": metadata}


def _release_results() -> list[dict]:
    return [
        _result("preproduction-real-model-certification-bundle", assessment=_bundle()),
        _result("configured-model-browser-conversation"),
        _result("configured-model-browser-campaign"),
    ]


def test_release_dimension_passes_only_complete_consistent_real_model_evidence() -> None:
    module = _load_quality_loop()
    dimension = module._quality_dimensions(_release_results(), mode="release")["real_model_certification"]
    assert dimension["status"] == "PASS"
    assert dimension["contract"] == "real-model-certification-dimension@2"
    assert dimension["identity"]["provider"] == "openai"
    assert dimension["component_count"] == 3


def test_non_release_mode_never_declares_real_model_certification() -> None:
    module = _load_quality_loop()
    assert module._quality_dimensions(_release_results(), mode="quick")["real_model_certification"]["status"] == "NOT_DECLARED"


def test_release_dimension_propagates_environment_block() -> None:
    module = _load_quality_loop()
    results = _release_results()
    results[0]["status"] = "BLOCKED_BY_ENVIRONMENT"
    assert module._quality_dimensions(results, mode="release")["real_model_certification"]["status"] == "BLOCKED_BY_ENVIRONMENT"


def test_release_dimension_rejects_skipped_or_missing_gate() -> None:
    module = _load_quality_loop()
    skipped = _release_results()
    skipped[-1]["status"] = "SKIPPED_UPSTREAM_FAILURE"
    assert module._quality_dimensions(skipped, mode="release")["real_model_certification"]["status"] == "FAIL"
    assert module._quality_dimensions(_release_results()[:-1], mode="release")["real_model_certification"]["status"] == "FAIL"


def test_release_dimension_rejects_missing_or_invalid_bundle() -> None:
    module = _load_quality_loop()
    missing = _release_results()[1:]
    assert module._quality_dimensions(missing, mode="release")["real_model_certification"]["reason"] == "required_real_model_bundle_gate_missing"
    invalid = _release_results()
    invalid[0]["metadata"]["structured_assessment"]["session_id"] = ""
    assert module._quality_dimensions(invalid, mode="release")["real_model_certification"]["reason"] == "real_model_bundle_evidence_invalid"
