from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_core.model_calls.real_model_identity import (
    RealModelCertificationError,
    attest_real_model_response,
    resolve_real_model_identity,
)


def _load_smoke_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "verify_model_smoke.py"
    spec = importlib.util.spec_from_file_location("verify_model_smoke_b15a", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _credential() -> str:
    return "sk-live-" + ("a" * 48)


def _response(*, content: str, model: str, usage: bool = True):
    token_usage = {
        "prompt_tokens": 8,
        "completion_tokens": 5,
        "total_tokens": 13,
    } if usage else {}
    return SimpleNamespace(
        content=content,
        id="msg-provider-response",
        response_metadata={
            "model_name": model,
            "finish_reason": "stop",
            "token_usage": token_usage,
        },
    )


def test_model_smoke_rejects_local_deterministic_stub_identity(monkeypatch) -> None:
    smoke = _load_smoke_script()
    monkeypatch.setenv("OPENAI_API_KEY", "deterministic-test-not-a-real-key")
    monkeypatch.setenv("OPENAI_MODEL", "deterministic-test-model")
    monkeypatch.setenv("OPENAI_API_BASE", "http://127.0.0.1:9999/v1")
    monkeypatch.setattr(smoke, "get_model", lambda: object())
    monkeypatch.setattr(smoke, "invoke_model", lambda **_kwargs: (_response(
        content="model-smoke-ok",
        model="deterministic-test-model",
    ), {"purpose": "preprod_model_smoke"}))

    output = io.StringIO()
    with redirect_stdout(output):
        return_code = smoke.main()

    payload = json.loads(output.getvalue().strip().splitlines()[-1])
    assert return_code == 1
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "real_model_identity_invalid"
    assert "OPENAI_API_KEY" not in output.getvalue()
    assert "deterministic-test-not-a-real-key" not in output.getvalue()


def test_openai_default_official_identity_is_accepted_without_exposing_key() -> None:
    key = _credential()
    identity = resolve_real_model_identity({
        "OPENAI_API_KEY": key,
        "OPENAI_MODEL": "gpt-4o-mini",
        "REAL_MODEL_CERTIFICATION_PROVIDER": "openai",
    })
    assert identity["provider"] == "openai"
    assert identity["endpoint"] == "https://api.openai.com/v1"
    assert identity["official_endpoint"] is True
    assert key not in json.dumps(identity)
    assert len(identity["credential_fingerprint_sha256_16"]) == 16


def test_deepseek_official_identity_accepts_current_model() -> None:
    identity = resolve_real_model_identity({
        "OPENAI_API_KEY": _credential(),
        "OPENAI_MODEL": "deepseek-v4-flash",
        "OPENAI_API_BASE": "https://api.deepseek.com",
        "REAL_MODEL_CERTIFICATION_PROVIDER": "deepseek",
    })
    assert identity["provider"] == "deepseek"
    assert identity["endpoint"] == "https://api.deepseek.com"
    assert identity["model"] == "deepseek-v4-flash"


@pytest.mark.parametrize("model", ["deepseek-chat", "deepseek-reasoner"])
def test_deepseek_deprecated_compatibility_aliases_are_rejected(model: str) -> None:
    with pytest.raises(RealModelCertificationError) as captured:
        resolve_real_model_identity({
            "OPENAI_API_KEY": _credential(),
            "OPENAI_MODEL": model,
            "OPENAI_API_BASE": "https://api.deepseek.com",
            "REAL_MODEL_CERTIFICATION_PROVIDER": "deepseek",
        })
    assert captured.value.code == "deprecated_deepseek_model_alias"


@pytest.mark.parametrize("base_url", [
    "http://api.deepseek.com",
    "https://127.0.0.1:9999/v1",
    "https://localhost/v1",
    "https://models.example.com/v1",
])
def test_unofficial_or_non_https_endpoints_are_rejected(base_url: str) -> None:
    with pytest.raises(RealModelCertificationError):
        resolve_real_model_identity({
            "OPENAI_API_KEY": _credential(),
            "OPENAI_MODEL": "deepseek-v4-flash",
            "OPENAI_API_BASE": base_url,
            "REAL_MODEL_CERTIFICATION_PROVIDER": "deepseek",
        })


def test_missing_credential_is_environment_blocked() -> None:
    with pytest.raises(RealModelCertificationError) as captured:
        resolve_real_model_identity({
            "OPENAI_MODEL": "gpt-4o-mini",
            "REAL_MODEL_CERTIFICATION_PROVIDER": "openai",
        })
    assert captured.value.code == "api_key_missing"
    assert captured.value.environment_blocked is True


def test_dynamic_response_attestation_requires_model_usage_and_finish_reason() -> None:
    identity = resolve_real_model_identity({
        "OPENAI_API_KEY": _credential(),
        "OPENAI_MODEL": "gpt-4o-mini",
        "REAL_MODEL_CERTIFICATION_PROVIDER": "openai",
    })
    challenge = "model-smoke-ok:0123456789abcdef"
    attestation = attest_real_model_response(
        response=_response(
            content=challenge,
            model="gpt-4o-mini-2024-07-18",
        ),
        identity=identity,
        expected_content=challenge,
    )
    assert attestation["reported_model"] == "gpt-4o-mini-2024-07-18"
    assert attestation["token_usage"]["total_tokens"] == 13
    assert attestation["response_id_present"] is True
    assert challenge not in json.dumps(attestation)


def test_response_attestation_rejects_static_reply_and_missing_usage() -> None:
    identity = resolve_real_model_identity({
        "OPENAI_API_KEY": _credential(),
        "OPENAI_MODEL": "deepseek-v4-flash",
        "OPENAI_API_BASE": "https://api.deepseek.com/v1",
        "REAL_MODEL_CERTIFICATION_PROVIDER": "deepseek",
    })
    challenge = "model-smoke-ok:unique-run-token"
    with pytest.raises(RealModelCertificationError) as mismatch:
        attest_real_model_response(
            response=_response(content="model-smoke-ok", model="deepseek-v4-flash"),
            identity=identity,
            expected_content=challenge,
        )
    assert mismatch.value.code == "dynamic_challenge_mismatch"

    with pytest.raises(RealModelCertificationError) as missing_usage:
        attest_real_model_response(
            response=_response(content=challenge, model="deepseek-v4-flash", usage=False),
            identity=identity,
            expected_content=challenge,
        )
    assert missing_usage.value.code == "token_usage_missing"


def test_model_smoke_passes_only_after_dynamic_official_attestation(monkeypatch) -> None:
    smoke = _load_smoke_script()
    monkeypatch.setenv("OPENAI_API_KEY", _credential())
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setenv("REAL_MODEL_CERTIFICATION_PROVIDER", "openai")
    monkeypatch.setattr(smoke, "get_model", lambda: object())
    monkeypatch.setattr(smoke, "get_model_profile", lambda: {"model": "gpt-4o-mini"})

    def invoke(**kwargs):
        prompt = str(kwargs["payload"][0].content)
        challenge = prompt.split("Respond with exactly: ", 1)[1]
        return _response(content=challenge, model="gpt-4o-mini-2024-07-18"), {
            "purpose": "preprod_model_smoke",
            "status": "ok",
        }

    monkeypatch.setattr(smoke, "invoke_model", invoke)
    output = io.StringIO()
    with redirect_stdout(output):
        return_code = smoke.main()
    payload = json.loads(output.getvalue().strip().splitlines()[-1])
    assert return_code == 0
    assert payload["status"] == "PASS"
    assert payload["identity"]["provider"] == "openai"
    assert payload["attestation"]["token_usage"]["total_tokens"] == 13
    assert _credential() not in output.getvalue()


def test_model_smoke_reports_missing_credential_as_environment_block(monkeypatch) -> None:
    smoke = _load_smoke_script()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setenv("REAL_MODEL_CERTIFICATION_PROVIDER", "openai")
    output = io.StringIO()
    with redirect_stdout(output):
        return_code = smoke.main()
    payload = json.loads(output.getvalue().strip().splitlines()[-1])
    assert return_code == 78
    assert payload == {
        "status": "BLOCKED_BY_ENVIRONMENT",
        "error_type": "RealModelCertificationError",
        "error_code": "api_key_missing",
        "reason": "real_model_environment_unavailable",
    }
