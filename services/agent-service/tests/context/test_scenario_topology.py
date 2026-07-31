from __future__ import annotations

import pytest

from agent_core.business import configure_business_port
from agent_core.config import clear_checkpointer_cache
from agent_core.persistence.store_provider import get_store_provider, reset_store_provider_cache
from agent_modules.ecommerce.business_port import (
    get_ecommerce_business_port,
    reset_ecommerce_business_port_cache,
)
from tests.support.conversation_case_runner import run_conversation_case


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch, tmp_path):
    database = tmp_path / "agent.sqlite3"
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("APP_PROFILE", "local")
    monkeypatch.setenv("AGENT_DB_BACKEND", "sqlite")
    monkeypatch.setenv("AGENT_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("SQLITE_DB_PATH", str(database))
    monkeypatch.setenv("CHECKPOINT_BACKEND", "memory")
    monkeypatch.setenv("CAPABILITY_SEMANTIC_VERIFIER_MODE", "candidate")
    monkeypatch.setenv("GOAL_ALIGNMENT_VERIFIER_MODE", "candidate")
    monkeypatch.setenv("ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE", "candidate")
    monkeypatch.setenv("OPENAI_API_KEY", "deterministic-test-not-a-real-key")
    monkeypatch.setenv("OPENAI_API_BASE", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OPENAI_MODEL", "deterministic-test-model")
    reset_store_provider_cache()
    clear_checkpointer_cache()
    try:
        yield
    finally:
        try:
            get_store_provider().close()
        except Exception:
            pass
        configure_business_port(get_ecommerce_business_port)
        reset_ecommerce_business_port_cache()
        reset_store_provider_cache()
        clear_checkpointer_cache()


def _turn(text: str, turn_id: str, thread: str) -> dict:
    return {
        "thread": thread,
        "user_text": text,
        "model_steps": [
            {
                "tool_calls": [{
                    "name": "declare_turn_goals",
                    "args": {
                        "summary": text,
                        "goals": [{
                            "goal_id": "g1",
                            "description": text,
                            "evidence_span": text,
                            "goal_type": "narrative",
                            "requested_effect": {
                                "domain": "conversation",
                                "operation": "continue_request",
                                "object_type": "goal",
                                "raw_description": text,
                            },
                            "expected_result_cardinality": "none",
                            "required": True,
                            "depends_on": [],
                        }],
                    },
                    "id": f"{turn_id}:goals",
                }]
            },
            {
                "tool_calls": [{
                    "name": "respond_to_user",
                    "args": {"answer": f"已收到：{text}", "evidence_handles": [], "goal_ids": ["g1"]},
                    "id": f"{turn_id}:answer",
                }]
            },
        ],
        "allowed_tools": ["respond_to_user"],
        "required_tools": ["respond_to_user"],
        "expected": {
            "terminal_statuses": ["GeneralFinalAnswer"],
            "workflow_levels": ["L0_DIRECT"],
            "workflow_statuses": ["NOT_REQUIRED"],
            "public_interaction": "answer",
            "trace": {"must_include": [], "must_not_include": []},
            "draft": {"count": 0},
            "port_calls": {},
            "result_assertions": [],
            "goal_count": 1,
        },
    }


def test_runner_uses_two_real_checkpoint_threads() -> None:
    texts = ["A 的第一轮", "B 的第一轮", "A 的第二轮", "B 的第二轮"]
    case = {
        "id": "real_two_thread_topology",
        "category": "thread_isolation",
        "turns": [{"role": "user", "text": text} for text in texts],
        "forbidden_behavior": [],
        "execution_contract": {
            "schema_version": 5,
            "fixture": {
                "id": "customer_orders_v1",
                "state": {"tenant_id": "tenant-a", "user_id": "u001", "role": "customer"},
            },
            "topology": {"threads": ["thread-a", "thread-b"]},
            "turn_contracts": [
                _turn(texts[0], "a1", "thread-a"),
                _turn(texts[1], "b1", "thread-b"),
                _turn(texts[2], "a2", "thread-a"),
                _turn(texts[3], "b2", "thread-b"),
            ],
            "forbidden_assertions": [],
        },
    }
    executed = run_conversation_case(case)
    assert [turn.thread_alias for turn in executed.turns] == ["thread-a", "thread-b", "thread-a", "thread-b"]
    assert executed.turns[0].thread_id != executed.turns[1].thread_id
    assert [turn.result["turn_index"] for turn in executed.turns] == [1, 1, 2, 2]
    assert "A 的第一轮" in [message.content for message in executed.turns[2].result["messages"] if hasattr(message, "content")]
    assert "B 的第一轮" not in [message.content for message in executed.turns[2].result["messages"] if hasattr(message, "content")]
