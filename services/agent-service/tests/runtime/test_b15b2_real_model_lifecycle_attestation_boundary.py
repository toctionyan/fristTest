from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "verify_preprod_full_lifecycle.py"
    spec = importlib.util.spec_from_file_location("verify_preprod_full_lifecycle_b15b2", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _official_key() -> str:
    return "sk-live-" + ("c" * 48)


class _FakeHarness:
    entered = 0
    run_calls = 0

    def __init__(self, *, deterministic_model: bool):
        assert deterministic_model is False
        self.agent_url = "http://127.0.0.1:9001"
        self.runtime_dir = Path("/tmp/b15b2-fake-runtime")
        self.env = {
            "OPENAI_API_KEY": "deterministic-test-not-a-real-key",
            "OPENAI_MODEL": "deterministic-test-model",
            "OPENAI_API_BASE": "http://127.0.0.1:9999/v1",
        }

    def __enter__(self):
        type(self).entered += 1
        return self

    def __exit__(self, *_args):
        return False

    def diagnostic_tails(self):
        return {}


def test_full_lifecycle_rejects_local_stub_before_harness_start(monkeypatch) -> None:
    script = _load_script()
    _FakeHarness.entered = 0
    _FakeHarness.run_calls = 0
    monkeypatch.setenv("OPENAI_API_KEY", "deterministic-test-not-a-real-key")
    monkeypatch.setenv("OPENAI_MODEL", "deterministic-test-model")
    monkeypatch.setenv("OPENAI_API_BASE", "http://127.0.0.1:9999/v1")
    monkeypatch.setattr(script, "ProductRuntimeHarness", _FakeHarness)

    def fake_run(**_kwargs):
        _FakeHarness.run_calls += 1
        return {"status": "PASS"}

    monkeypatch.setattr(script, "_run", fake_run)
    output = io.StringIO()
    with redirect_stdout(output):
        return_code = script.main()
    payload = json.loads(output.getvalue().strip().splitlines()[-1])

    assert return_code == 1
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "real_model_identity_invalid"
    assert _FakeHarness.entered == 0
    assert _FakeHarness.run_calls == 0


def test_full_lifecycle_missing_key_is_environment_blocked_before_start(monkeypatch) -> None:
    script = _load_script()

    class Harness(_FakeHarness):
        def __init__(self, *, deterministic_model: bool):
            super().__init__(deterministic_model=deterministic_model)
            self.env = {"OPENAI_MODEL": "gpt-4o-mini"}

    Harness.entered = 0
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(script, "ProductRuntimeHarness", Harness)
    output = io.StringIO()
    with redirect_stdout(output):
        return_code = script.main()
    payload = json.loads(output.getvalue().strip().splitlines()[-1])

    assert return_code == 78
    assert payload["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert payload["error_code"] == "api_key_missing"
    assert Harness.entered == 0


def test_gateway_records_provider_model_finish_reason_and_usage() -> None:
    from agent_core.model_calls import invoke_model, model_call_scope

    response = SimpleNamespace(
        content="ok",
        response_metadata={
            "model_name": "gpt-4o-mini-2024-07-18",
            "finish_reason": "stop",
            "token_usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    )

    class Model:
        model_name = "gpt-4o-mini"

        def invoke(self, _payload):
            return response

    with model_call_scope(max_calls=1) as ledger:
        _response, record = invoke_model(purpose="agent_loop", model=Model(), payload=[])

    assert record["provider_model"] == "gpt-4o-mini-2024-07-18"
    assert record["finish_reason"] == "stop"
    assert record["total_tokens"] == 5
    assert ledger.records[0]["provider_model"] == "gpt-4o-mini-2024-07-18"


def test_persistence_redaction_preserves_attestable_token_usage_and_masks_credentials() -> None:
    from agent_core.observability.redaction import redact_for_persistence

    script = _load_script()
    persisted = redact_for_persistence({
        "model_calls": [{
            "purpose": "agent_loop",
            "status": "ok",
            "provider_model": "gpt-4o-mini-2024-07-18",
            "finish_reason": "tool_calls",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "prompt_cache_hit_tokens": 4,
            "prompt_cache_miss_tokens": 6,
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "access_tokens": "plural-access-secret",
            "token": "opaque-secret",
        }],
    })
    record = persisted["model_calls"][0]

    assert record["prompt_tokens"] == 10
    assert record["completion_tokens"] == 5
    assert record["total_tokens"] == 15
    assert record["prompt_cache_hit_tokens"] == 4
    assert record["prompt_cache_miss_tokens"] == 6
    assert record["access_token"] == "[REDACTED]"
    assert record["refresh_token"] == "[REDACTED]"
    assert record["access_tokens"] == "[REDACTED]"
    assert record["token"] == "[REDACTED]"

    attestation = script._attest_lifecycle_model_calls(
        diagnostics=[persisted],
        identity={"provider": "openai", "model": "gpt-4o-mini"},
        turn_index=1,
    )
    assert attestation["call_count"] == 1
    assert attestation["total_tokens"] == 15


def test_lifecycle_attests_each_turn_model_call() -> None:
    script = _load_script()
    identity = {
        "provider": "openai",
        "model": "gpt-4o-mini",
    }
    diagnostics = [{
        "model_calls": [
            {
                "purpose": "agent_loop",
                "status": "ok",
                "provider_model": "gpt-4o-mini-2024-07-18",
                "finish_reason": "tool_calls",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
            {
                "purpose": "answer_release_alignment",
                "status": "ok",
                "provider_model": "gpt-4o-mini-2024-07-18",
                "finish_reason": "stop",
                "total_tokens": 8,
            },
        ],
    }]

    attestation = script._attest_lifecycle_model_calls(
        diagnostics=diagnostics,
        identity=identity,
        turn_index=2,
    )
    assert attestation["turn"] == 2
    assert attestation["call_count"] == 2
    assert attestation["total_tokens"] == 23
    assert attestation["purposes"] == ["agent_loop", "answer_release_alignment"]


def test_lifecycle_attestation_rejects_missing_usage() -> None:
    script = _load_script()
    diagnostics = [{
        "model_calls": [{
            "purpose": "agent_loop",
            "status": "ok",
            "provider_model": "gpt-4o-mini-2024-07-18",
            "finish_reason": "stop",
        }],
    }]
    with pytest.raises(Exception) as captured:
        script._attest_lifecycle_model_calls(
            diagnostics=diagnostics,
            identity={"provider": "openai", "model": "gpt-4o-mini"},
            turn_index=1,
        )
    assert getattr(captured.value, "code", "") == "token_usage_missing"
