#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import json

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:140]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


config = ROOT / "services/agent-service/src/agent_core/config.py"
replace_once(
    config,
    '''    deepseek_v4_thinking = provider == "deepseek" and str(settings["model"]).strip().lower().startswith("deepseek-v4")
    return {
        "provider": provider,
        "model": settings["model"],
        "base_url_configured": bool(settings["base_url"]),
        "temperature": settings["temperature"],
        "timeout_seconds": settings["timeout_seconds"],
        "max_retries": settings["max_retries"],
        "tool_choice_supported": not deepseek_v4_thinking,
        "reasoning_content_required_for_tool_calls": deepseek_v4_thinking,
        "structured_output": "continuous_agent_loop_with_grounded_observations_and_action_gateway",
    }
''',
    '''    deepseek_v4 = provider == "deepseek" and str(settings["model"]).strip().lower().startswith("deepseek-v4")
    return {
        "provider": provider,
        "model": settings["model"],
        "base_url_configured": bool(settings["base_url"]),
        "temperature": settings["temperature"],
        "timeout_seconds": settings["timeout_seconds"],
        "max_retries": settings["max_retries"],
        # Agent planning, routing and verifier calls are latency-bounded control-plane work.
        # DeepSeek V4 defaults to thinking=enabled, so production opts out explicitly here.
        "thinking_mode": "disabled" if deepseek_v4 else "provider_default",
        # Keep the existing conservative V4 tool-choice boundary in this latency-only repair.
        "tool_choice_supported": not deepseek_v4,
        "reasoning_content_required_for_tool_calls": False,
        "structured_output": "continuous_agent_loop_with_grounded_observations_and_action_gateway",
    }
''',
)
replace_once(
    config,
    '''        return ChatDeepSeek(
            model=str(settings["model"]),
            api_key=api_key,
            api_base=str(settings["base_url"] or "https://api.deepseek.com"),
            temperature=float(settings["temperature"]),
            timeout=float(settings["timeout_seconds"]),
            max_retries=int(settings["max_retries"]),
        )
''',
    '''        deepseek_v4 = str(settings["model"]).strip().lower().startswith("deepseek-v4")
        return ChatDeepSeek(
            model=str(settings["model"]),
            api_key=api_key,
            api_base=str(settings["base_url"] or "https://api.deepseek.com"),
            temperature=float(settings["temperature"]),
            timeout=float(settings["timeout_seconds"]),
            max_retries=int(settings["max_retries"]),
            # V4 defaults to thinking=enabled. Control-plane calls must remain inside the
            # existing 25s x 2-attempt provider envelope, so disable thinking explicitly.
            extra_body={"thinking": {"type": "disabled"}} if deepseek_v4 else None,
        )
''',
)

provider_test = ROOT / "services/agent-service/tests/runtime/test_wp08_attempt6_release_repairs.py"
replace_once(
    provider_test,
    '''    assert profile["provider"] == "deepseek"
    assert profile["tool_choice_supported"] is False
    assert profile["reasoning_content_required_for_tool_calls"] is True
''',
    '''    assert profile["provider"] == "deepseek"
    assert profile["thinking_mode"] == "disabled"
    assert profile["tool_choice_supported"] is False
    assert profile["reasoning_content_required_for_tool_calls"] is False
''',
)
replace_once(
    provider_test,
    '''    monkeypatch.setenv("OPENAI_API_BASE", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    agent_config.get_model.cache_clear()
''',
    '''    monkeypatch.setenv("OPENAI_API_BASE", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    # Do not inherit timeout/retry overrides from unrelated standard-suite tests.
    # This provider-construction test proves the exact governed control-plane envelope.
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "25")
    monkeypatch.setenv("MODEL_MAX_RETRIES", "1")
    agent_config.get_model.cache_clear()
''',
)
replace_once(
    provider_test,
    '''        model = agent_config.get_model()
        assert model.__class__.__module__.startswith("langchain_deepseek")
        assert str(getattr(model, "model_name", "")) == "deepseek-v4-flash"
''',
    '''        model = agent_config.get_model()
        assert model.__class__.__module__.startswith("langchain_deepseek")
        assert str(getattr(model, "model_name", "")) == "deepseek-v4-flash"
        assert getattr(model, "extra_body", None) == {"thinking": {"type": "disabled"}}
        settings = agent_config.get_model_settings()
        assert settings["timeout_seconds"] == 25.0
        assert settings["max_retries"] == 1
''',
)

print(json.dumps({
    "status": "APPLIED",
    "changed": [
        str(config.relative_to(ROOT)),
        str(provider_test.relative_to(ROOT)),
    ],
    "invariants": {
        "deepseek_v4_control_thinking": "disabled",
        "provider_timeout_seconds": 25,
        "provider_max_retries": 1,
        "browser_response_sla_seconds": 120,
        "tool_choice_boundary_changed": False,
    },
}, ensure_ascii=False, indent=2))
