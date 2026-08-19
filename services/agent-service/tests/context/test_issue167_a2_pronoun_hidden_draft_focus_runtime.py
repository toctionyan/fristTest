"""Issue #167 A2: a hidden refund Draft must not steal later pronoun focus.

A1 fixes the meaning from the three user utterances plus the declared fixture.
A2 runs the corrected candidate through the real lifecycle.  The mouse refund
Draft may remain pending, but the explicit earphone logistics result from turn 2
must become the latest relevant visible referent for turn 3.
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


CASE_PATH = Path(__file__).parent / "issue167_cases" / "pronoun_not_hidden_draft_focus_a2.json"
ORACLE_PATH = (
    Path(__file__).parent
    / "semantic_goal_oracle_evidence"
    / "pronoun_not_hidden_draft_focus_v20_4.json"
)
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


def _tool_results(turn_result: dict, tool_name: str) -> list[dict]:
    return [
        row.get("result") if isinstance(row.get("result"), dict) else {}
        for row in list(turn_result.get("tool_trace") or [])
        if isinstance(row, dict) and str(row.get("name") or "") == tool_name
    ]


def _offers(turn_result: dict) -> list[dict]:
    return [
        row
        for row in list(turn_result.get("artifact_ledger") or [])
        if isinstance(row, dict) and str(row.get("kind") or "") == "offer"
    ]


def test_issue167_hidden_refund_draft_does_not_steal_pronoun_focus() -> None:
    assert ORACLE["case_id"] == CASE["id"] == "pronoun_not_hidden_draft_focus"
    assert ORACLE["authority"]["derived_from_runtime_output"] is False

    executed = run_conversation_case(CASE)
    assert len(executed.turns) == 3

    refund_turn, earphone_turn, pronoun_turn = executed.turns
    assert refund_turn.user_text == "帮我申请鼠标退款"
    assert earphone_turn.user_text == "查一下耳机物流"
    assert pronoun_turn.user_text == "它什么时候到？"

    # Turn 1 is intentionally a pending transaction Draft for the mouse.
    first_offers = _offers(refund_turn.result)
    assert len(first_offers) == 1
    assert str(first_offers[0].get("target_handle") or "").endswith(":10003")
    assert str(first_offers[0].get("draft_state") or "") == "AWAITING_AUTHORIZATION"
    assert {"prepare_refund", "action_gateway"}.issubset(_trace_names(refund_turn.result))
    assert "commit_action" not in _trace_names(refund_turn.result)

    # Turn 2 explicitly moves the current visible read focus to the earphone.
    earphone_logistics = json.dumps(
        _tool_results(earphone_turn.result, "get_order_logistics"),
        ensure_ascii=False,
    )
    assert "10001" in earphone_logistics
    assert "蓝牙耳机" in earphone_logistics
    assert _trace_names(earphone_turn.result).isdisjoint({"prepare_refund", "commit_action"})

    # Turn 3 must follow the latest visible earphone result, never the hidden
    # mouse refund Draft that is still pending in transaction state.
    pronoun_logistics = json.dumps(
        _tool_results(pronoun_turn.result, "get_order_logistics"),
        ensure_ascii=False,
    )
    assert "10001" in pronoun_logistics
    assert "蓝牙耳机" in pronoun_logistics
    assert "10003" not in pronoun_logistics
    assert _trace_names(pronoun_turn.result).isdisjoint({"prepare_refund", "commit_action"})

    final_offers = _offers(pronoun_turn.result)
    assert len(final_offers) == 1
    assert str(final_offers[0].get("target_handle") or "").endswith(":10003")
    assert str(final_offers[0].get("draft_state") or "") == "AWAITING_AUTHORIZATION"

    logistics_reads = [
        str(call.get("resource_id") or "")
        for call in executed.port.calls
        if call.get("kind") == "read_resource" and call.get("resource_type") == "logistics"
    ]
    assert logistics_reads == ["10001", "10001"]
    assert executed.port.count("execute_command") == 0
