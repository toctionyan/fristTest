from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
AGENT_ROOT = ROOT / "services" / "agent-service"
for path in (ROOT / "scripts", AGENT_ROOT, AGENT_ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.services.lifecycle_command_runner import LifecycleCommandRunner
from agent_core import config as agent_config
from agent_core.lifecycle import dialogue_runtime


def _load_semantic_smoke():
    path = AGENT_ROOT / "scripts" / "verify_preprod_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("wp08_attempt6_semantic_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_structured_boundary_persists_verified_ingress_and_formal_update() -> None:
    calls = []

    class Snapshot:
        values = {}

    class Graph:
        def update_state(self, config, update, as_node=None):
            calls.append((config, dict(update), as_node))
        def invoke(self, value, config=None):
            return dict(calls[-1][1])
        def get_state(self, config):
            return Snapshot()

    runner = LifecycleCommandRunner(object())
    base = {
        "current_thread_id": "thread-1",
        "current_user_id": "u001",
        "current_role": "customer",
        "current_tenant_id": "default",
        "current_subject": "u001",
        "turn_index": 1,
        "ledger_schema_version": 2,
        "artifact_ledger": [{"handle": "artifact:1", "kind": "artifact"}],
        "phase": "action_gateway",
        "status": "ActionProposalReady",
    }
    update = {
        "artifact_ledger": [
            {"handle": "artifact:1", "kind": "artifact"},
            {"handle": "offer:1", "kind": "offer", "draft_state": "NEEDS_INPUT"},
        ],
        "focused_draft_id": "offer:1",
        "active_draft_id": "offer:1",
        "status": "ActionInputRequired",
    }
    result = runner._resume_from_named_boundary(
        graph=Graph(), config={"configurable": {"thread_id": "secure"}},
        node_name="action_gateway", update=update, base_state=base,
    )
    persisted = calls[0][1]
    assert calls[0][2] == "action_gateway"
    assert persisted["current_thread_id"] == "thread-1"
    assert persisted["current_user_id"] == "u001"
    assert persisted["current_role"] == "customer"
    assert persisted["current_tenant_id"] == "default"
    assert persisted["current_subject"] == "u001"
    assert persisted["turn_index"] == 1
    assert persisted["ledger_schema_version"] == 2
    assert "phase" not in persisted
    assert [row["handle"] for row in persisted["artifact_ledger"]] == ["artifact:1", "offer:1"]
    assert persisted["status"] == "ActionInputRequired"
    assert result["focused_draft_id"] == "offer:1"


def test_deepseek_v4_profile_uses_provider_adapter_without_tool_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.deepseek.com")
    profile = agent_config.get_model_profile()
    assert profile["provider"] == "deepseek"
    assert profile["thinking_mode"] == "disabled"
    assert profile["tool_choice_supported"] is False
    assert profile["reasoning_content_required_for_tool_calls"] is False


def test_get_model_uses_chatdeepseek_for_official_deepseek_v4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    # Do not inherit timeout/retry overrides from unrelated standard-suite tests.
    # This provider-construction test proves the exact governed control-plane envelope.
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "25")
    monkeypatch.setenv("MODEL_MAX_RETRIES", "1")
    agent_config.get_model.cache_clear()
    try:
        model = agent_config.get_model()
        assert model.__class__.__module__.startswith("langchain_deepseek")
        assert str(getattr(model, "model_name", "")) == "deepseek-v4-flash"
        assert getattr(model, "extra_body", None) == {"thinking": {"type": "disabled"}}
        settings = agent_config.get_model_settings()
        assert settings["timeout_seconds"] == 25.0
        assert settings["max_retries"] == 1
    finally:
        agent_config.get_model.cache_clear()


def test_deepseek_v4_binding_omits_provider_tool_choice() -> None:
    class Model:
        def __init__(self): self.kwargs = None
        def bind_tools(self, schemas, **kwargs):
            self.kwargs = dict(kwargs)
            return self
    model = Model()
    dialogue_runtime._bind_loop_tools(
        model, [{"type": "function", "function": {"name": "declare_turn_goals"}}],
        require_tool_call=True, required_tool_name="declare_turn_goals", allow_tool_choice=False,
    )
    assert model.kwargs == {}


def test_supported_provider_binding_keeps_named_tool_choice() -> None:
    class Model:
        def __init__(self): self.kwargs = None
        def bind_tools(self, schemas, **kwargs):
            self.kwargs = dict(kwargs)
            return self
    model = Model()
    dialogue_runtime._bind_loop_tools(
        model, [{"type": "function", "function": {"name": "declare_turn_goals"}}],
        require_tool_call=True, required_tool_name="declare_turn_goals", allow_tool_choice=True,
    )
    assert model.kwargs == {"tool_choice": "declare_turn_goals"}


def test_semantic_oracle_accepts_optional_legacy_goal_type_when_effect_matches() -> None:
    smoke = _load_semantic_smoke()
    oracle = [{
        "oracle_id": "g1", "goal_type": "query", "evidence_span": "查一下键盘订单",
        "required": True, "depends_on": [],
        "requested_effect": {"domain": "order", "operation": "query", "object_type": "order"},
    }]
    goals = [{
        "goal_id": "m1", "goal_type": "", "evidence_span": "查一下键盘订单",
        "required": True, "depends_on": [],
        "requested_effect": {"domain": "order", "operation": "query", "object_type": "order"},
    }]
    smoke._match_oracle(case_id="optional-goal-type", oracle=oracle, goals=goals)


def test_semantic_oracle_rejects_wrong_authoritative_requested_effect() -> None:
    smoke = _load_semantic_smoke()
    oracle = [{
        "oracle_id": "g1", "goal_type": "query", "evidence_span": "查一下键盘订单",
        "required": True, "depends_on": [],
        "requested_effect": {"domain": "order", "operation": "query", "object_type": "order"},
    }]
    goals = [{
        "goal_id": "m1", "goal_type": "query", "evidence_span": "查一下键盘订单",
        "required": True, "depends_on": [],
        "requested_effect": {"domain": "logistics", "operation": "query", "object_type": "order"},
    }]
    with pytest.raises(RuntimeError, match="requested_effect"):
        smoke._match_oracle(case_id="wrong-effect", oracle=oracle, goals=goals)


def test_recovery_input_values_are_scoped_to_current_form_step() -> None:
    import verify_managed_postgres_recovery as recovery

    interaction = {
        "current_step": 2,
        "fields": [
            {"name": "reason_code", "required": True, "step": 1, "options": [{"value": "QUALITY_ISSUE"}]},
            {"name": "reason", "required": True, "step": 2, "options": []},
        ],
    }
    assert recovery._input_values(interaction) == {
        "reason": "managed-postgres-restart-recovery"
    }
