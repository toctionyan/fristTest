from __future__ import annotations

import importlib.util
import json
from pathlib import Path


OLD_COMPONENT_GATES = {
    "preproduction-model-base-smoke",
    "preproduction-conversation-prototypes",
    "preproduction-full-lifecycle-model",
}
BUNDLE_GATE = "preproduction-real-model-certification-bundle"
BROWSER_GATES = (
    "configured-model-browser-conversation",
    "configured-model-browser-campaign",
)


def _root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_quality_loop():
    path = _root() / "scripts" / "quality_loop.py"
    spec = importlib.util.spec_from_file_location("quality_loop_b15c3", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _identity() -> dict[str, object]:
    return {
        "contract": "real-model-identity@1",
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "credential_fingerprint_sha256_16": "0123456789abcdef",
        "official_endpoint": True,
        "https": True,
    }


def _bundle(*, status: str = "PASS") -> dict[str, object]:
    return {
        "contract": "real-model-certification-bundle@1",
        "status": status,
        "session_id": "rmcert-0123456789abcdef0123456789abcdef",
        "workspace_fingerprint_sha256": "a" * 64,
        "identity": _identity(),
        "component_count": 3,
        "components": ["smoke", "semantic", "lifecycle"],
        "attested_model_calls_by_component": {
            "smoke": 1,
            "semantic": 12,
            "lifecycle": 2,
        },
        "total_attested_model_calls": 15,
    }


def _result(gate_id: str, *, status: str = "PASS", assessment: dict | None = None) -> dict:
    metadata = {}
    if assessment is not None:
        metadata["structured_assessment"] = assessment
    return {
        "id": gate_id,
        "status": status,
        "category": "preproduction" if gate_id.startswith("preproduction-") else "integration",
        "metadata": metadata,
    }


def _bundle_release_results(*, bundle_status: str = "PASS") -> list[dict]:
    assessment = _bundle(status=bundle_status)
    return [
        _result(BUNDLE_GATE, status=bundle_status, assessment=assessment),
        *[_result(gate_id) for gate_id in BROWSER_GATES],
    ]


def _legacy_independent_results() -> list[dict]:
    return [
        *[
            _result(gate_id, assessment={"status": "PASS", "identity": _identity()})
            for gate_id in sorted(OLD_COMPONENT_GATES)
        ],
        *[_result(gate_id) for gate_id in BROWSER_GATES],
    ]


def test_release_policy_supersedes_real_model_bundle_with_one_production_authority() -> None:
    policy = json.loads((_root() / "governance" / "quality-loop-policy.json").read_text(encoding="utf-8"))
    release = {step["id"]: step for step in policy["steps"] if "release" in step.get("modes", [])}
    assert "production-certification-bundle" in release
    assert BUNDLE_GATE not in release
    assert not (OLD_COMPONENT_GATES & set(release))
    assert release["production-certification-bundle"]["argv"][-1].endswith("verify_production_certification_bundle.py")
    assert "production-certification-bundle" in release["clean-release-preflight"]["depends_on"]
    assert not (OLD_COMPONENT_GATES & set(release["clean-release-preflight"]["depends_on"]))


def test_release_dimension_passes_only_bundle_authority_and_browser_gates() -> None:
    module = _load_quality_loop()
    dimension = module._quality_dimensions(
        _bundle_release_results(),
        mode="release",
    )["real_model_certification"]
    assert dimension["status"] == "PASS"
    assert dimension["contract"] == "real-model-certification-dimension@2"
    assert dimension["bundle_contract"] == "real-model-certification-bundle@1"
    assert dimension["identity"]["provider"] == "openai"
    assert dimension["component_count"] == 3
    assert dimension["total_attested_model_calls"] == 15
    assert dimension["gate_ids"] == [BUNDLE_GATE, *BROWSER_GATES]


def test_three_independent_green_components_cannot_form_release_certification() -> None:
    module = _load_quality_loop()
    dimension = module._quality_dimensions(
        _legacy_independent_results(),
        mode="release",
    )["real_model_certification"]
    assert dimension["status"] == "FAIL"
    assert dimension["reason"] == "required_real_model_bundle_gate_missing"
    assert dimension["missing_gate_ids"] == [BUNDLE_GATE]


def test_release_dimension_rejects_invalid_or_incomplete_bundle_assessment() -> None:
    module = _load_quality_loop()
    cases = []
    wrong_contract = _bundle()
    wrong_contract["contract"] = "real-model-certification-bundle@0"
    cases.append(wrong_contract)
    standalone = _bundle()
    standalone.pop("session_id")
    cases.append(standalone)
    incomplete = _bundle()
    incomplete["components"] = ["smoke", "semantic"]
    cases.append(incomplete)
    weak_calls = _bundle()
    weak_calls["total_attested_model_calls"] = 14
    cases.append(weak_calls)

    for assessment in cases:
        results = [
            _result(BUNDLE_GATE, assessment=assessment),
            *[_result(gate_id) for gate_id in BROWSER_GATES],
        ]
        dimension = module._quality_dimensions(results, mode="release")["real_model_certification"]
        assert dimension["status"] == "FAIL"
        assert dimension["reason"] == "real_model_bundle_evidence_invalid"


def test_release_dimension_propagates_bundle_environment_block() -> None:
    module = _load_quality_loop()
    dimension = module._quality_dimensions(
        _bundle_release_results(bundle_status="BLOCKED_BY_ENVIRONMENT"),
        mode="release",
    )["real_model_certification"]
    assert dimension["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert dimension["blocked_gate_ids"] == [BUNDLE_GATE]


def test_non_release_mode_never_declares_bundle_certification() -> None:
    module = _load_quality_loop()
    dimension = module._quality_dimensions(
        _bundle_release_results(),
        mode="quick",
    )["real_model_certification"]
    assert dimension["status"] == "NOT_DECLARED"
