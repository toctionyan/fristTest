from __future__ import annotations

import importlib.util
import io
import json
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from types import SimpleNamespace


def _load_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "verify_preprod_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("verify_preprod_conversation_smoke_b15b1", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog() -> dict:
    cases = []
    for index in range(12):
        text = f"查询订单 {index}"
        cases.append({
            "id": f"b15b1-{index}",
            "turns": [{"role": "user", "text": text}],
            "execution_contract": {
                "preproduction_risk_prototype": True,
                "turn_contracts": [{
                    "user_text": text,
                    "goal_oracle": [{
                        "oracle_id": "goal-1",
                        "evidence_span": text,
                        "goal_type": "query",
                        "required": True,
                        "depends_on": [],
                        "required_tools": ["list_orders"],
                    }],
                }],
            },
        })
    return {"cases": cases}


def test_semantic_prototype_rejects_local_model_stub_before_invocation(monkeypatch, tmp_path: Path) -> None:
    script = _load_script()
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(script, "CATALOG", catalog)
    monkeypatch.setenv("OPENAI_API_KEY", "deterministic-test-not-a-real-key")
    monkeypatch.setenv("OPENAI_MODEL", "deterministic-test-model")
    monkeypatch.setenv("OPENAI_API_BASE", "http://127.0.0.1:9999/v1")

    calls = {"count": 0}

    class Model:
        def bind_tools(self, _schemas):
            return self

    @contextmanager
    def scope(**_kwargs):
        yield SimpleNamespace(summary=lambda: {"calls": calls["count"]})

    def invoke(**_kwargs):
        calls["count"] += 1
        return SimpleNamespace(content="", response_metadata={}), {"status": "stub"}

    monkeypatch.setattr(script, "get_model", lambda: Model())
    monkeypatch.setattr(script, "get_model_profile", lambda: {"model": "deterministic-test-model"})
    monkeypatch.setattr(script, "model_call_scope", scope)
    monkeypatch.setattr(script, "invoke_model", invoke)
    monkeypatch.setattr(script, "tool_calls", lambda _response: [{
        "name": "declare_turn_goals",
        "args": {"goals": [{
            "goal_id": "goal-1",
            "evidence_span": "查询订单",
            "goal_type": "query",
            "required": True,
            "depends_on": [],
        }]},
    }])
    monkeypatch.setattr(script, "_match_oracle", lambda **_kwargs: None)
    monkeypatch.setattr(script, "_validate_with_production_goal_contract", lambda **_kwargs: {
        "goals": [{"goal_id": "goal-1"}],
    })

    output = io.StringIO()
    with redirect_stdout(output):
        return_code = script.main()
    payload = json.loads(output.getvalue().strip().splitlines()[-1])

    assert return_code == 1
    assert payload["status"] == "FAIL"
    assert payload["reason"] == "real_model_identity_invalid"
    assert calls["count"] == 0


def _official_key() -> str:
    return "sk-live-" + ("b" * 48)


def _provider_response(model: str = "gpt-4o-mini-2024-07-18"):
    return SimpleNamespace(
        content="",
        id="msg-semantic-provider",
        response_metadata={
            "model_name": model,
            "finish_reason": "tool_calls",
            "token_usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
            },
        },
    )


def _patch_successful_semantic_runtime(monkeypatch, script, tmp_path: Path, *, invoke):
    # A successful semantic-certification fixture must exercise the same protected
    # independent-verifier authority now required by the live bundle. This keeps
    # the provider-attestation test focused without reintroducing candidate-only mode.
    monkeypatch.setenv("APP_PROFILE", "preprod")
    monkeypatch.setenv("GOAL_ALIGNMENT_VERIFIER_MODE", "model")
    monkeypatch.setenv("GOAL_GRANULARITY_VERIFIER_MODE", "model")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(script, "CATALOG", catalog)

    class Model:
        def bind_tools(self, _schemas):
            return self

    @contextmanager
    def scope(**_kwargs):
        yield SimpleNamespace(summary=lambda: {"calls": 12})

    monkeypatch.setattr(script, "get_model", lambda: Model())
    monkeypatch.setattr(script, "get_model_profile", lambda: {"model": "gpt-4o-mini"})
    monkeypatch.setattr(script, "model_call_scope", scope)
    monkeypatch.setattr(script, "invoke_model", invoke)
    monkeypatch.setattr(script, "tool_calls", lambda _response: [{
        "name": "declare_turn_goals",
        "args": {"goals": [{
            "goal_id": "goal-1",
            "evidence_span": "查询订单",
            "goal_type": "query",
            "required": True,
            "depends_on": [],
        }]},
    }])
    monkeypatch.setattr(script, "_match_oracle", lambda **_kwargs: None)
    monkeypatch.setattr(script, "_validate_with_production_goal_contract", lambda **_kwargs: {
        "goals": [{"goal_id": "goal-1"}],
    })


def test_semantic_prototype_missing_key_is_environment_blocked_before_invocation(monkeypatch) -> None:
    script = _load_script()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    calls = {"count": 0}
    monkeypatch.setattr(script, "invoke_model", lambda **_kwargs: calls.__setitem__("count", calls["count"] + 1))

    output = io.StringIO()
    with redirect_stdout(output):
        return_code = script.main()
    payload = json.loads(output.getvalue().strip().splitlines()[-1])

    assert return_code == 78
    assert payload == {
        "status": "BLOCKED_BY_ENVIRONMENT",
        "error_type": "RealModelCertificationError",
        "error_code": "api_key_missing",
        "reason": "real_model_environment_unavailable",
    }
    assert calls["count"] == 0


def test_semantic_prototype_attests_every_official_provider_response(monkeypatch, tmp_path: Path) -> None:
    script = _load_script()
    monkeypatch.setenv("OPENAI_API_KEY", _official_key())
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setenv("REAL_MODEL_CERTIFICATION_PROVIDER", "openai")
    calls = {"count": 0}

    def invoke(**_kwargs):
        calls["count"] += 1
        return _provider_response(), {"purpose": "semantic-prototype"}

    _patch_successful_semantic_runtime(monkeypatch, script, tmp_path, invoke=invoke)
    output = io.StringIO()
    with redirect_stdout(output):
        return_code = script.main()
    payload = json.loads(output.getvalue().strip().splitlines()[-1])

    assert return_code == 0
    assert payload["status"] == "PASS"
    assert payload["identity"]["provider"] == "openai"
    assert calls["count"] == 12
    assert len(payload["cases"]) == 12
    for case in payload["cases"]:
        attestation = case["provider_attestation"]
        assert attestation["contract"] == "real-model-metadata-attestation@1"
        assert attestation["reported_model"] == "gpt-4o-mini-2024-07-18"
        assert attestation["token_usage"]["total_tokens"] == 30
        assert attestation["finish_reason"] == "tool_calls"
    assert _official_key() not in output.getvalue()


def test_metadata_attestation_rejects_missing_usage() -> None:
    from agent_core.model_calls import (
        RealModelCertificationError,
        attest_real_model_metadata,
        resolve_real_model_identity,
    )

    identity = resolve_real_model_identity({
        "OPENAI_API_KEY": _official_key(),
        "OPENAI_MODEL": "gpt-4o-mini",
        "REAL_MODEL_CERTIFICATION_PROVIDER": "openai",
    })
    response = SimpleNamespace(
        content="",
        response_metadata={
            "model_name": "gpt-4o-mini-2024-07-18",
            "finish_reason": "tool_calls",
            "token_usage": {},
        },
    )
    try:
        attest_real_model_metadata(response=response, identity=identity)
    except RealModelCertificationError as exc:
        assert exc.code == "token_usage_missing"
    else:
        raise AssertionError("missing usage must fail closed")
