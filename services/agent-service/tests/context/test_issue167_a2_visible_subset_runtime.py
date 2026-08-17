"""Issue #167 A2: a visible signed subset remains the scope for later eligibility.

The independent A1 oracle is derived from the user utterances plus the declared
fixture. A2 exercises the real lifecycle so a product failure cannot be hidden
by the catalog candidate: the first turn must materialize only signed orders and
the second turn must assess exactly that visible collection without a refund write.
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


CASE_PATH = Path(__file__).parent / "issue167_cases" / "visible_subset_then_action_clarify_a2.json"
ORACLE_PATH = (
    Path(__file__).parent
    / "semantic_goal_oracle_evidence"
    / "visible_subset_then_action_clarify_v20_4.json"
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


def _tool_results(turn_result: dict, tool_name: str) -> list[dict]:
    return [
        row.get("result") if isinstance(row.get("result"), dict) else {}
        for row in list(turn_result.get("tool_trace") or [])
        if isinstance(row, dict) and str(row.get("name") or "") == tool_name
    ]


def _trace_names(turn_result: dict) -> set[str]:
    return {
        str(row.get("name") or "")
        for row in list(turn_result.get("tool_trace") or [])
        if isinstance(row, dict)
    }


def test_issue167_visible_signed_subset_remains_eligibility_scope() -> None:
    assert ORACLE["case_id"] == CASE["id"] == "visible_subset_then_action_clarify"
    assert ORACLE["authority"]["derived_from_runtime_output"] is False

    executed = run_conversation_case(CASE)
    assert len(executed.turns) == 2

    signed_turn, eligibility_turn = executed.turns
    assert signed_turn.user_text == "列出已签收订单"
    assert eligibility_turn.user_text == "它们里面能退的有哪些？"

    signed_results = _tool_results(signed_turn.result, "list_orders")
    assert len(signed_results) == 1 and signed_results[0].get("ok") is True
    signed_data = signed_results[0].get("data") or {}
    assert int(signed_data.get("count") or 0) == 2
    signed_orders = list(signed_data.get("orders") or [])
    assert [str(row.get("order_id") or "") for row in signed_orders] == ["10001", "10002"]
    assert all(str(row.get("status") or "") == "已签收" for row in signed_orders)
    assert "10003" not in json.dumps(signed_results, ensure_ascii=False)

    eligibility_results = _tool_results(eligibility_turn.result, "evaluate_refund_eligibility")
    assert len(eligibility_results) == 1 and eligibility_results[0].get("ok") is True
    eligibility_data = eligibility_results[0].get("data") or {}
    assert int(eligibility_data.get("count") or 0) == 2
    member_handles = [str(value) for value in list(eligibility_data.get("member_handles") or [])]
    assert member_handles == [
        "artifact:fixture:order:10001",
        "artifact:fixture:order:10002",
    ]
    assert "10003" not in json.dumps(eligibility_results, ensure_ascii=False)

    preview_ids = [
        str(call.get("resource_id") or "")
        for call in executed.port.calls
        if call.get("kind") == "preview_operation" and call.get("operation") == "APPLY_REFUND"
    ]
    assert preview_ids == ["10001", "10002"]

    assert _trace_names(eligibility_turn.result).isdisjoint(
        {"prepare_refund", "action_gateway", "commit_action"}
    )
    assert executed.port.count("execute_command") == 0
