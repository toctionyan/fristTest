from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from agent_core import config
from agent_core.lifecycle.goal_planning import validate_goal_declaration
from agent_core.lifecycle.protocol import DECLARE_TURN_GOALS_SCHEMA, REFERENCE_EXPRESSION_SCHEMA

AGENT_ROOT = Path(__file__).resolve().parents[2]

@pytest.mark.parametrize(("raw", "expected"), [
    ("postgresql+psycopg://user:pass@127.0.0.1:5432/runtime?sslmode=disable", "postgresql://user:pass@127.0.0.1:5432/runtime?sslmode=disable"),
    ("postgresql+psycopg2://user:pass@127.0.0.1:5432/runtime", "postgresql://user:pass@127.0.0.1:5432/runtime"),
    ("postgresql://user:pass@127.0.0.1:5432/runtime", "postgresql://user:pass@127.0.0.1:5432/runtime"),
])
def test_psycopg_connection_url_normalization_preserves_connection_semantics(raw: str, expected: str) -> None:
    assert config._normalize_psycopg_connection_url(raw) == expected

@pytest.mark.parametrize("dialect", ["psycopg", "psycopg2"])
def test_postgres_checkpointer_setup_and_live_connect_share_normalized_uri(monkeypatch: pytest.MonkeyPatch, dialect: str) -> None:
    config.clear_checkpointer_cache()
    setup_urls: list[str] = []
    connect_urls: list[str] = []
    class SetupSaver:
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def setup(self) -> None: return None
    class FakePostgresSaver:
        @classmethod
        def from_conn_string(cls, url: str):
            setup_urls.append(url)
            return SetupSaver()
    class FakeConnection:
        def close(self) -> None: return None
    postgres_module = importlib.import_module("langgraph.checkpoint.postgres")
    psycopg_module = importlib.import_module("psycopg")
    fencing_module = importlib.import_module("agent_core.runtime.turn_fencing")
    monkeypatch.setattr(postgres_module, "PostgresSaver", FakePostgresSaver)
    monkeypatch.setattr(psycopg_module, "connect", lambda url, **kwargs: (connect_urls.append(url) or FakeConnection()))
    monkeypatch.setattr(fencing_module, "AtomicallyFencedPostgresSaver", lambda conn: conn)
    monkeypatch.setattr(fencing_module, "FencedCheckpointer", lambda saver: saver)
    monkeypatch.setenv("CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setenv("CHECKPOINT_SETUP", "true")
    monkeypatch.setenv("CHECKPOINT_DATABASE_URL", f"postgresql+{dialect}://user:pass@127.0.0.1:5432/runtime")
    try:
        checkpointer = config.build_checkpointer()
        assert isinstance(checkpointer, FakeConnection)
        assert setup_urls == ["postgresql://user:pass@127.0.0.1:5432/runtime"]
        assert connect_urls == ["postgresql://user:pass@127.0.0.1:5432/runtime"]
    finally:
        config.clear_checkpointer_cache()

def test_reference_expression_contract_separates_history_from_same_turn_dependencies() -> None:
    schema_description = str(REFERENCE_EXPRESSION_SCHEMA.get("description") or "")
    function_description = str(DECLARE_TURN_GOALS_SCHEMA["function"].get("description") or "")
    for text in (schema_description, function_description):
        assert "current_goal_output" in text
        assert "当前轮" in text
        assert "历史" in text
    goal_properties = DECLARE_TURN_GOALS_SCHEMA["function"]["parameters"]["properties"]["goals"]["items"]["properties"]
    assert "input_bindings" in goal_properties
    assert "depends_on" not in goal_properties
    smoke = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
    assert "禁止输出 depends_on" in smoke
    assert "source.kind=current_goal_output" in smoke
    assert "不能引用本轮尚未执行目标的未来结果" in smoke

def test_unresolved_historical_reference_on_same_turn_dependency_stays_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "local")
    monkeypatch.setenv("GOAL_ALIGNMENT_VERIFIER_MODE", "candidate")
    user_text = "查一下我的订单，再查下物流到哪了"
    result, declared = validate_goal_declaration(
        state={"current_user_input": user_text, "artifact_ledger": []},
        args={"goals": [
            {"goal_id": "g1", "description": "查订单", "evidence_span": "查一下我的订单", "goal_type": "query", "expected_result_cardinality": "collection", "required": True, "depends_on": [], "requested_effect": {"domain": "order", "operation": "list", "object_type": "order"}},
            {"goal_id": "g2", "description": "查物流", "evidence_span": "查下物流到哪了", "goal_type": "query", "expected_result_cardinality": "collection", "required": True, "depends_on": ["g1"], "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"}, "reference_expression": {"reference_type": "temporal_visible_result", "temporal_relation": "latest", "object_type": "order", "expected_cardinality": "collection", "evidence_span": "查下物流到哪了"}},
        ]},
        capability_registry=object(),
    )
    assert declared is None
    assert result["code"] == "GOAL_DECLARATION_INVALID"
    assert "reference_resolution_not_found:g2" in result["data"]["errors"]
