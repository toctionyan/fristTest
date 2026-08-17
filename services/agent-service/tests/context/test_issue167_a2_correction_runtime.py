"""Issue #167 A2: first real-lifecycle execution for the corrected two-goal case.

This test is intentionally product-facing.  The semantic oracle was authored in
A1 from the user utterances; A2 now feeds a corrected deterministic candidate
through the real lifecycle graph.  A failure is product evidence and must not be
turned green by weakening the oracle or substituting a nearby capability.
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


CASE_PATH = Path(__file__).parent / "issue167_cases" / "correction_earphone_to_keyboard_two_goals_a2.json"
ORACLE_PATH = Path(__file__).parent / "semantic_goal_oracle_evidence" / "correction_earphone_to_keyboard_two_goals_v20_4.json"
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


def test_issue167_correction_rebinds_both_goals_to_keyboard_through_real_lifecycle() -> None:
    assert ORACLE["case_id"] == CASE["id"] == "correction_earphone_to_keyboard_two_goals"
    assert ORACLE["authority"]["derived_from_runtime_output"] is False

    executed = run_conversation_case(CASE)
    assert len(executed.turns) == 2

    first, corrected = executed.turns
    assert first.user_text == "耳机物流查下，也看看能不能退"
    assert corrected.user_text == "不是耳机，是键盘"

    first_logistics = json.dumps(_tool_results(first.result, "get_order_logistics"), ensure_ascii=False)
    corrected_logistics = json.dumps(_tool_results(corrected.result, "get_order_logistics"), ensure_ascii=False)
    first_eligibility = _tool_results(first.result, "evaluate_refund_eligibility")
    corrected_eligibility = _tool_results(corrected.result, "evaluate_refund_eligibility")

    # Logistics returns the bound order identity directly.  Eligibility's public
    # result deliberately exposes a business preview instead of repeating the
    # order ID, so its target identity is asserted at the authoritative
    # BusinessPort boundary below rather than by searching presentation text.
    assert "10001" in first_logistics
    assert "10002" in corrected_logistics
    assert "10001" not in corrected_logistics

    assert len(first_eligibility) == 1 and first_eligibility[0].get("ok") is True
    assert len(corrected_eligibility) == 1 and corrected_eligibility[0].get("ok") is True
    assert (first_eligibility[0].get("data") or {}).get("target_label") == "蓝牙耳机"
    assert (corrected_eligibility[0].get("data") or {}).get("target_label") == "机械键盘"

    eligibility_previews = [
        call
        for call in executed.port.calls
        if call.get("kind") == "preview_operation" and call.get("operation") == "APPLY_REFUND"
    ]
    assert [str(call.get("resource_id") or "") for call in eligibility_previews] == ["10001", "10002"]

    corrected_trace_names = {
        str(row.get("name") or "")
        for row in list(corrected.result.get("tool_trace") or [])
        if isinstance(row, dict)
    }
    assert {"get_order_logistics", "evaluate_refund_eligibility"}.issubset(corrected_trace_names)
    assert "prepare_refund" not in corrected_trace_names
    assert executed.port.count("execute_command") == 0
