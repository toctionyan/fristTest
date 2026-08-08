#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"replacement_anchor_count:{path}:{count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1. Use the provider-specific DeepSeek adapter so V4 thinking-mode
# reasoning_content survives tool-call round trips.
replace_once(
    "services/agent-service/pyproject.toml",
    '  "langchain-openai>=0.2.0",\n',
    '  "langchain-openai>=0.2.0",\n  "langchain-deepseek>=1.1.0,<2.0.0",\n',
)

replace_once(
    "services/agent-service/src/agent_core/config.py",
    '''def get_model_profile() -> dict[str, object]:\n''',
    '''def _use_deepseek_adapter(settings: dict[str, object] | None = None) -> bool:\n    resolved = settings or get_model_settings()\n    provider = (os.getenv("MODEL_PROVIDER") or "").strip().lower()\n    model = str(resolved.get("model") or "").strip().lower()\n    base_url = str(resolved.get("base_url") or "").strip().rstrip("/").lower()\n    official_endpoint = base_url in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}\n    return provider == "deepseek" or (official_endpoint and model.startswith("deepseek-"))\n\n\ndef get_model_profile() -> dict[str, object]:\n''',
)

replace_once(
    "services/agent-service/src/agent_core/config.py",
    '''    settings = get_model_settings()\n    return {\n        "provider": "openai_compatible",\n        "model": settings["model"],\n        "base_url_configured": bool(settings["base_url"]),\n        "temperature": settings["temperature"],\n        "timeout_seconds": settings["timeout_seconds"],\n        "max_retries": settings["max_retries"],\n        "structured_output": "continuous_agent_loop_with_grounded_observations_and_action_gateway",\n    }\n''',
    '''    settings = get_model_settings()\n    provider = "deepseek" if _use_deepseek_adapter(settings) else "openai_compatible"\n    deepseek_v4_thinking = provider == "deepseek" and str(settings["model"]).strip().lower().startswith("deepseek-v4")\n    return {\n        "provider": provider,\n        "model": settings["model"],\n        "base_url_configured": bool(settings["base_url"]),\n        "temperature": settings["temperature"],\n        "timeout_seconds": settings["timeout_seconds"],\n        "max_retries": settings["max_retries"],\n        "tool_choice_supported": not deepseek_v4_thinking,\n        "reasoning_content_required_for_tool_calls": deepseek_v4_thinking,\n        "structured_output": "continuous_agent_loop_with_grounded_observations_and_action_gateway",\n    }\n''',
)

replace_once(
    "services/agent-service/src/agent_core/config.py",
    '''@lru_cache(maxsize=1)\ndef get_model():\n    try:\n        from langchain_openai import ChatOpenAI\n    except Exception as e:\n        raise RuntimeError(\n            "缺少 langchain_openai，请先安装 requirements.txt 后再启动 Agent 服务。"\n        ) from e\n\n    api_key = os.getenv("OPENAI_API_KEY")\n    settings = get_model_settings()\n\n    if not api_key:\n''',
    '''@lru_cache(maxsize=1)\ndef get_model():\n    api_key = os.getenv("OPENAI_API_KEY")\n    settings = get_model_settings()\n\n    if not api_key:\n''',
)

replace_once(
    "services/agent-service/src/agent_core/config.py",
    '''    return ChatOpenAI(\n        model=str(settings["model"]),\n        api_key=api_key,\n        base_url=settings["base_url"],\n        temperature=float(settings["temperature"]),\n        timeout=float(settings["timeout_seconds"]),\n        max_retries=int(settings["max_retries"]),\n    )\n''',
    '''    if _use_deepseek_adapter(settings):\n        try:\n            from langchain_deepseek import ChatDeepSeek\n        except Exception as exc:\n            raise RuntimeError(\n                "DeepSeek provider requires the declared langchain-deepseek integration."\n            ) from exc\n        return ChatDeepSeek(\n            model=str(settings["model"]),\n            api_key=api_key,\n            api_base=str(settings["base_url"] or "https://api.deepseek.com"),\n            temperature=float(settings["temperature"]),\n            timeout=float(settings["timeout_seconds"]),\n            max_retries=int(settings["max_retries"]),\n        )\n\n    try:\n        from langchain_openai import ChatOpenAI\n    except Exception as exc:\n        raise RuntimeError(\n            "缺少 langchain_openai，请先安装 requirements.txt 后再启动 Agent 服务。"\n        ) from exc\n    return ChatOpenAI(\n        model=str(settings["model"]),\n        api_key=api_key,\n        base_url=settings["base_url"],\n        temperature=float(settings["temperature"]),\n        timeout=float(settings["timeout_seconds"]),\n        max_retries=int(settings["max_retries"]),\n    )\n''',
)

# 2. DeepSeek V4 thinking mode rejects tool_choice. Omit provider-side forcing
# while retaining the runtime's exact-call validation and bounded retries.
replace_once(
    "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
    '''def _bind_loop_tools(\n    model: Any,\n    schemas: list[dict[str, Any]],\n    *,\n    require_tool_call: bool,\n    required_tool_name: str | None = None,\n) -> Any:\n''',
    '''def _bind_loop_tools(\n    model: Any,\n    schemas: list[dict[str, Any]],\n    *,\n    require_tool_call: bool,\n    required_tool_name: str | None = None,\n    allow_tool_choice: bool = True,\n) -> Any:\n''',
)

replace_once(
    "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
    '''    if require_tool_call:\n        try:\n''',
    '''    if require_tool_call and allow_tool_choice:\n        try:\n''',
)

replace_once(
    "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
    '''            required_tool_name="declare_turn_goals" if planning_phase else None,\n        )\n''',
    '''            required_tool_name="declare_turn_goals" if planning_phase else None,\n            allow_tool_choice=bool(get_model_profile().get("tool_choice_supported", True)),\n        )\n''',
)

# 3. Structured command ingress must persist the verified base identity, target
# and offer alongside the formal node update before graph routing resumes.
replace_once(
    "services/agent-service/app/services/lifecycle_command_runner.py",
    '''        merged = {**base_state, **dict(update or {})}\n        graph.update_state(config, update, as_node=node_name)\n''',
    '''        merged = {**base_state, **dict(update or {})}\n        # Structured API commands enter between normal graph nodes. Persist the\n        # verified ingress state together with the formal node update so scope\n        # identity, target artifacts and transaction controls survive the\n        # checkpoint before outgoing edges are scheduled. The node update wins\n        # on overlap and remains the transition authority.\n        graph.update_state(config, merged, as_node=node_name)\n''',
)

# 4. The semantic oracle must certify the authoritative requested_effect. The
# legacy goal_type hint is optional and cannot be a hard production criterion.
replace_once(
    "services/agent-service/scripts/verify_preprod_conversation_smoke.py",
    '''def _user_turns(case: dict[str, Any]) -> list[str]:\n''',
    '''_EFFECT_KEYS = ("domain", "operation", "object_type")\n\n\ndef _effect_identity(value: Any) -> tuple[str, str, str]:\n    source = value if isinstance(value, dict) else {}\n    return tuple(str(source.get(key) or "").strip().casefold() for key in _EFFECT_KEYS)  # type: ignore[return-value]\n\n\ndef _user_turns(case: dict[str, Any]) -> list[str]:\n''',
)

old_match = '''    for expected in oracle:\n        evidence = str(expected.get("evidence_span") or "")\n        goal_type = str(expected.get("goal_type") or "")\n        required = bool(expected.get("required", True))\n        exact_matches = [\n            row for row in unmatched\n            if str(row.get("evidence_span") or "") == evidence\n            and str(row.get("goal_type") or "") == goal_type\n            and bool(row.get("required", True)) == required\n        ]\n        matches = exact_matches or [\n            row for row in unmatched\n            if _span_matches_oracle(\n                expected=evidence,\n                actual=row.get("evidence_span"),\n            )\n            and str(row.get("goal_type") or "") == goal_type\n            and bool(row.get("required", True)) == required\n        ]\n        if len(matches) != 1:\n            candidates = [\n                {\n                    "goal_id": str(row.get("goal_id") or ""),\n                    "evidence_span": str(row.get("evidence_span") or ""),\n                    "goal_type": str(row.get("goal_type") or ""),\n                }\n                for row in unmatched\n            ]\n            raise RuntimeError(\n                f"{case_id}: no unique model goal matches oracle span={evidence!r}, "\n                f"type={goal_type!r}, candidates={candidates!r}"\n            )\n'''
new_match = '''    for expected in oracle:\n        evidence = str(expected.get("evidence_span") or "")\n        required = bool(expected.get("required", True))\n        expected_effect = _effect_identity(expected.get("requested_effect"))\n        if not all(expected_effect):\n            raise RuntimeError(\n                f"{case_id}: oracle goal {expected.get('oracle_id')!r} lacks authoritative requested_effect identity"\n            )\n\n        def candidate_matches(row: dict[str, Any], *, fuzzy_span: bool) -> bool:\n            span_ok = (\n                _span_matches_oracle(expected=evidence, actual=row.get("evidence_span"))\n                if fuzzy_span\n                else str(row.get("evidence_span") or "") == evidence\n            )\n            return (\n                span_ok\n                and bool(row.get("required", True)) == required\n                and _effect_identity(row.get("requested_effect")) == expected_effect\n            )\n\n        exact_matches = [row for row in unmatched if candidate_matches(row, fuzzy_span=False)]\n        matches = exact_matches or [row for row in unmatched if candidate_matches(row, fuzzy_span=True)]\n        if len(matches) != 1:\n            candidates = [\n                {\n                    "goal_id": str(row.get("goal_id") or ""),\n                    "evidence_span": str(row.get("evidence_span") or ""),\n                    "goal_type": str(row.get("goal_type") or ""),\n                    "requested_effect": {\n                        key: str((row.get("requested_effect") or {}).get(key) or "")\n                        for key in _EFFECT_KEYS\n                    } if isinstance(row.get("requested_effect"), dict) else {},\n                }\n                for row in unmatched\n            ]\n            raise RuntimeError(\n                f"{case_id}: no unique model goal matches oracle span={evidence!r}, "\n                f"requested_effect={expected_effect!r}, candidates={candidates!r}"\n            )\n'''
replace_once("services/agent-service/scripts/verify_preprod_conversation_smoke.py", old_match, new_match)

# 5. Focused executable regressions for all three attempt-6 owners.
test_path = ROOT / "services/agent-service/tests/runtime/test_wp08_attempt6_release_repairs.py"
if test_path.exists():
    raise SystemExit("test_path_already_exists")
test_path.write_text(
    '''from __future__ import annotations\n\nimport importlib.util\nimport os\nfrom pathlib import Path\nimport sys\n\nimport pytest\n\nROOT = Path(__file__).resolve().parents[4]\nAGENT_ROOT = ROOT / "services" / "agent-service"\nfor path in (ROOT / "scripts", AGENT_ROOT, AGENT_ROOT / "src"):\n    if str(path) not in sys.path:\n        sys.path.insert(0, str(path))\n\nfrom app.services.lifecycle_command_runner import LifecycleCommandRunner\nfrom agent_core import config as agent_config\nfrom agent_core.lifecycle import dialogue_runtime\n\n\ndef _load_semantic_smoke():\n    path = AGENT_ROOT / "scripts" / "verify_preprod_conversation_smoke.py"\n    spec = importlib.util.spec_from_file_location("wp08_attempt6_semantic_smoke", path)\n    assert spec is not None and spec.loader is not None\n    module = importlib.util.module_from_spec(spec)\n    sys.modules[spec.name] = module\n    spec.loader.exec_module(module)\n    return module\n\n\ndef test_structured_boundary_persists_verified_ingress_and_formal_update() -> None:\n    calls = []\n\n    class Snapshot:\n        values = {}\n\n    class Graph:\n        def update_state(self, config, update, as_node=None):\n            calls.append((config, dict(update), as_node))\n        def invoke(self, value, config=None):\n            return dict(calls[-1][1])\n        def get_state(self, config):\n            return Snapshot()\n\n    runner = LifecycleCommandRunner(object())\n    base = {\n        "current_thread_id": "thread-1",\n        "current_user_id": "u001",\n        "current_tenant_id": "default",\n        "artifact_ledger": [{"handle": "artifact:1", "kind": "artifact"}],\n        "status": "ActionProposalReady",\n    }\n    update = {\n        "artifact_ledger": [\n            {"handle": "artifact:1", "kind": "artifact"},\n            {"handle": "offer:1", "kind": "offer", "draft_state": "NEEDS_INPUT"},\n        ],\n        "focused_draft_id": "offer:1",\n        "active_draft_id": "offer:1",\n        "status": "ActionInputRequired",\n    }\n    result = runner._resume_from_named_boundary(\n        graph=Graph(), config={"configurable": {"thread_id": "secure"}},\n        node_name="action_gateway", update=update, base_state=base,\n    )\n    persisted = calls[0][1]\n    assert calls[0][2] == "action_gateway"\n    assert persisted["current_thread_id"] == "thread-1"\n    assert persisted["current_user_id"] == "u001"\n    assert persisted["current_tenant_id"] == "default"\n    assert [row["handle"] for row in persisted["artifact_ledger"]] == ["artifact:1", "offer:1"]\n    assert persisted["status"] == "ActionInputRequired"\n    assert result["focused_draft_id"] == "offer:1"\n\n\ndef test_deepseek_v4_profile_uses_provider_adapter_without_tool_choice(monkeypatch: pytest.MonkeyPatch) -> None:\n    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")\n    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")\n    monkeypatch.setenv("OPENAI_API_BASE", "https://api.deepseek.com")\n    profile = agent_config.get_model_profile()\n    assert profile["provider"] == "deepseek"\n    assert profile["tool_choice_supported"] is False\n    assert profile["reasoning_content_required_for_tool_calls"] is True\n\n\ndef test_get_model_uses_chatdeepseek_for_official_deepseek_v4(monkeypatch: pytest.MonkeyPatch) -> None:\n    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")\n    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")\n    monkeypatch.setenv("OPENAI_API_BASE", "https://api.deepseek.com")\n    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")\n    agent_config.get_model.cache_clear()\n    try:\n        model = agent_config.get_model()\n        assert model.__class__.__module__.startswith("langchain_deepseek")\n        assert str(getattr(model, "model_name", "")) == "deepseek-v4-flash"\n    finally:\n        agent_config.get_model.cache_clear()\n\n\ndef test_deepseek_v4_binding_omits_provider_tool_choice() -> None:\n    class Model:\n        def __init__(self): self.kwargs = None\n        def bind_tools(self, schemas, **kwargs):\n            self.kwargs = dict(kwargs)\n            return self\n    model = Model()\n    dialogue_runtime._bind_loop_tools(\n        model, [{"type": "function", "function": {"name": "declare_turn_goals"}}],\n        require_tool_call=True, required_tool_name="declare_turn_goals", allow_tool_choice=False,\n    )\n    assert model.kwargs == {}\n\n\ndef test_supported_provider_binding_keeps_named_tool_choice() -> None:\n    class Model:\n        def __init__(self): self.kwargs = None\n        def bind_tools(self, schemas, **kwargs):\n            self.kwargs = dict(kwargs)\n            return self\n    model = Model()\n    dialogue_runtime._bind_loop_tools(\n        model, [{"type": "function", "function": {"name": "declare_turn_goals"}}],\n        require_tool_call=True, required_tool_name="declare_turn_goals", allow_tool_choice=True,\n    )\n    assert model.kwargs == {"tool_choice": "declare_turn_goals"}\n\n\ndef test_semantic_oracle_accepts_optional_legacy_goal_type_when_effect_matches() -> None:\n    smoke = _load_semantic_smoke()\n    oracle = [{\n        "oracle_id": "g1", "goal_type": "query", "evidence_span": "查一下键盘订单",\n        "required": True, "depends_on": [],\n        "requested_effect": {"domain": "order", "operation": "query", "object_type": "order"},\n    }]\n    goals = [{\n        "goal_id": "m1", "goal_type": "", "evidence_span": "查一下键盘订单",\n        "required": True, "depends_on": [],\n        "requested_effect": {"domain": "order", "operation": "query", "object_type": "order"},\n    }]\n    smoke._match_oracle(case_id="optional-goal-type", oracle=oracle, goals=goals)\n\n\ndef test_semantic_oracle_rejects_wrong_authoritative_requested_effect() -> None:\n    smoke = _load_semantic_smoke()\n    oracle = [{\n        "oracle_id": "g1", "goal_type": "query", "evidence_span": "查一下键盘订单",\n        "required": True, "depends_on": [],\n        "requested_effect": {"domain": "order", "operation": "query", "object_type": "order"},\n    }]\n    goals = [{\n        "goal_id": "m1", "goal_type": "query", "evidence_span": "查一下键盘订单",\n        "required": True, "depends_on": [],\n        "requested_effect": {"domain": "logistics", "operation": "query", "object_type": "order"},\n    }]\n    with pytest.raises(RuntimeError, match="requested_effect"):\n        smoke._match_oracle(case_id="wrong-effect", oracle=oracle, goals=goals)\n''',
    encoding="utf-8",
)

print("attempt6 repair patch applied")
