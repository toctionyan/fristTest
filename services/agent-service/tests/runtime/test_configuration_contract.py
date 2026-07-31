from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from agent_core import config


SERVICE_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = SERVICE_ROOT.parents[1]


def _template_keys(path: Path) -> set[str]:
    return {
        line.split("=", 1)[0].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.lstrip().startswith("#") and "=" in line
    }


def test_agent_template_exposes_model_settings_without_secrets() -> None:
    template = SERVICE_ROOT / ".env.example"
    assert template.is_file()
    keys = _template_keys(template)
    assert {"OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_API_BASE"} <= keys
    assert {
        "MODEL_TEMPERATURE",
        "MODEL_TIMEOUT_SECONDS",
        "MODEL_MAX_RETRIES",
        "MODEL_CALL_MAX_PER_TURN",
        "MODEL_CALL_MAX_PLANNER_PER_TURN",
        "MODEL_CALL_MAX_VERIFIER_PER_TURN",
        "MODEL_CALL_MAX_SUPPORT_PER_TURN",
    } <= keys
    assert "你的" not in template.read_text(encoding="utf-8")


def test_model_profile_reflects_externalized_runtime_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # This contract deliberately verifies the provider-default branch.  Do not
    # inherit a developer, CI stub or dotenv endpoint into that assertion.
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("MODEL_TEMPERATURE", "0.35")
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("MODEL_MAX_RETRIES", "3")

    settings = config.get_model_settings()
    profile = config.get_model_profile()

    assert settings == {
        "model": "test-model",
        "base_url": None,
        "temperature": 0.35,
        "timeout_seconds": 45.0,
        "max_retries": 3,
    }
    assert profile["temperature"] == 0.35
    assert profile["timeout_seconds"] == 45.0
    assert profile["max_retries"] == 3


def test_invalid_model_temperature_fails_before_model_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_TEMPERATURE", "2.1")
    with pytest.raises(RuntimeError, match="MODEL_TEMPERATURE"):
        config.get_model_settings()


def test_frontend_config_reads_service_local_env_template() -> None:
    vite = (SERVICE_ROOT / "frontend" / "vite.config.js").read_text(encoding="utf-8")
    assert "loadEnv" in vite
    assert "VITE_AGENT_DEV_TARGET" in vite


def test_workspace_convergence_policy_covers_configuration_templates() -> None:
    policy = json.loads((WORKSPACE_ROOT / "governance" / "architecture-policy.json").read_text(encoding="utf-8"))
    templates = {item["path"] for item in policy["configuration"]["templates"]}
    assert templates == {
        "services/agent-service/.env.example",
        "services/business-service/.env.example",
        "services/agent-service/frontend/.env.example",
        "deployment/.env.example",
    }
