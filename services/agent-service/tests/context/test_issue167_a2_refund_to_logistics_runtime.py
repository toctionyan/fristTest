"""Issue #167 A2: real-lifecycle proof for refund-to-logistics correction.

A1 fixes the semantic authority from the user utterances and declared fixture
context.  A2 verifies that an unresolved refund target is never guessed and
that the second turn withdraws the refund effect while retaining a logistics
goal even when the target still requires clarification.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_core.business import configure_business_port
from agent_core.config import clear_checkpointer_cache
from agent_core.persistence.store_provider import get_store_provider, reset_store_provider_cache
from agent_modules.ecommerce.business_port import (
    get_ecommerce_business_port,
    reset_ecommerce_business_port_cache,
)
from tests.support.conversation_case_runner import run_conversation_case


CASE_PATH = Path(__file__).parent / "issue167_cases" / "correction_refund_to_logistics_a2.json"
ORACLE_PATH = Path(__file__).parent / "semantic_goal_oracle_evidence" / "correction_refund_to_logistics_v20_4.json"
CASE = json.loads(CASE_PATH.read_text(encoding="utf-8"))
ORACLE = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))


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
    monkeypatch.setenv("AGENT_BUSINESS_ADAPTER", "ecommerce_http")
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


def _trace_names(turn_result: dict) -> set[str]:
    return {
        str(row.get("name") or "")
        for row in list(turn_result.get("tool_trace") or [])
        if isinstance(row, dict)
    }


def test_issue167_refund_to_logistics_withdraws_write_effect_without_guessing_target() -> None:
    assert ORACLE["case_id"] == CASE["id"] == "correction_refund_to_logistics"
    assert ORACLE["authority"]["derived_from_runtime_output"] is False

    executed = run_conversation_case(CASE)
    assert len(executed.turns) == 2

    refund_turn, logistics_turn = executed.turns
    assert refund_turn.user_text == "帮我退了这个订单"
    assert logistics_turn.user_text == "算了，我只是想查物流"

    first_contract = json.dumps(
        refund_turn.result.get("frozen_semantic_contract") or {}, ensure_ascii=False
    )
    corrected_contract = json.dumps(
        logistics_turn.result.get("frozen_semantic_contract") or {}, ensure_ascii=False
    )

    assert "refund.request" in first_contract
    assert "shipment.tracking" in corrected_contract
    assert "refund.request" not in corrected_contract

    assert "请" in str(refund_turn.result.get("current_final_answer") or "")
    assert "请" in str(logistics_turn.result.get("current_final_answer") or "")

    assert _trace_names(refund_turn.result).isdisjoint(
        {"prepare_refund", "commit_action", "get_order_logistics"}
    )
    assert _trace_names(logistics_turn.result).isdisjoint(
        {"prepare_refund", "commit_action", "get_order_logistics"}
    )

    assert executed.port.count("preview_operation") == 0
    assert executed.port.count("execute_command") == 0
