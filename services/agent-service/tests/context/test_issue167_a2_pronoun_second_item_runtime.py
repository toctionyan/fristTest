"""Issue #167 A2: ordinal visible-result binding followed by a pronoun question.

The semantic oracle is authored from the user utterances plus the declared
fixture order.  This lifecycle test proves that the ordinal read reaches the
second visible order and that the later feasibility question cannot be
silently converted into the nearby cancellation write path.
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


CASE_PATH = Path(__file__).parent / "issue167_cases" / "pronoun_second_item_from_visible_list_a2.json"
ORACLE_PATH = Path(__file__).parent / "semantic_goal_oracle_evidence" / "pronoun_second_item_from_visible_list_v20_4.json"
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


def test_issue167_second_visible_order_stays_authoritative_for_pronoun_followup() -> None:
    assert ORACLE["case_id"] == CASE["id"] == "pronoun_second_item_from_visible_list"
    assert ORACLE["authority"]["derived_from_runtime_output"] is False

    executed = run_conversation_case(CASE)
    assert [turn.user_text for turn in executed.turns] == [
        "我买了什么？",
        "第二个订单物流到哪了？",
        "它能取消吗？",
    ]

    # The second visible member in the declared fixture is order 10002.  The
    # authoritative BusinessPort must therefore receive logistics reads only
    # for that order on the ordinal turn; resolving 10001/10003 would prove the
    # runtime used some scope other than the visible result's ordering.
    logistics_reads = [
        call
        for call in executed.port.calls
        if call.get("kind") == "read_resource" and call.get("resource_type") == "logistics"
    ]
    assert [str(call.get("resource_id") or "") for call in logistics_reads] == ["10002"]

    ordinal_trace = _trace_names(executed.turns[1].result)
    assert "get_order_logistics" in ordinal_trace

    # ‘它能取消吗’ is a feasibility question.  The installed cancellation
    # capability is an action-draft path and explicitly excludes this wording,
    # so lack of an exact read-only capability must remain unsupported instead
    # of becoming a draft or write.
    final_trace = _trace_names(executed.final.result)
    assert "report_unsupported_request" in final_trace
    assert "prepare_cancel_order" not in final_trace
    assert "action_gateway" not in final_trace
    assert "commit_action" not in final_trace
    assert executed.port.count("preview_operation") == 0
    assert executed.port.count("execute_command") == 0
