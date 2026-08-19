"""Issue #167 A2: courier phone must not collapse into nearby logistics capability.

A1 fixes the requested user-visible result from the utterance itself. A2 then
runs that declaration through the real lifecycle and requires Capability
Surface to prove exact absence before the generic unsupported reporter is
allowed. No product-runtime source is changed by this enrollment commit.
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


CASE_PATH = Path(__file__).parent / "issue167_cases" / "similar_courier_phone_not_logistics_a2.json"
ORACLE_PATH = Path(__file__).parent / "semantic_goal_oracle_evidence" / "similar_courier_phone_not_logistics_v20_4.json"
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


def _trace_names(result: dict) -> set[str]:
    return {
        str(row.get("name") or "")
        for row in list(result.get("tool_trace") or [])
        if isinstance(row, dict)
    }


def test_issue167_courier_phone_is_exactly_unsupported_not_logistics() -> None:
    assert ORACLE["case_id"] == CASE["id"] == "similar_courier_phone_not_logistics"
    assert ORACLE["authority"]["derived_from_runtime_output"] is False
    assert ORACLE["authority"]["capability_availability_is_semantic_input"] is False

    executed = run_conversation_case(CASE)
    assert len(executed.turns) == 1
    turn = executed.turns[0]
    assert turn.user_text == "快递员手机号给我，不是物流状态"

    contract = turn.result.get("frozen_semantic_contract") or {}
    goals = [row for row in list(contract.get("goals") or []) if isinstance(row, dict)]
    assert len(goals) == 1
    goal = goals[0]
    assert goal["evidence_span"] == "快递员手机号给我，不是物流状态"
    requested = goal.get("requested_effect") or {}
    outputs = requested.get("requested_outputs") or []
    assert outputs == [
        {
            "output_id": "open",
            "evidence_span": "快递员手机号",
            "open_description": "快递员手机号",
        }
    ]
    assert all(str(row.get("output_id") or "") != "shipment.tracking" for row in outputs)

    surface = turn.result.get("capability_surface") or {}
    surface_goals = {
        str(row.get("goal_id") or ""): row
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict)
    }
    decision = surface_goals["g1"]
    assert decision["requested_effect_identity"] == "semantic-output:open"
    assert decision["status"] == "absent_proven"
    assert decision["similarity_used"] is False
    assert decision["candidate_tools"] == ["report_unsupported_request"]
    assert "get_order_logistics" not in set(surface.get("tool_names") or [])

    trace_names = _trace_names(turn.result)
    assert "report_unsupported_request" in trace_names
    assert trace_names.isdisjoint(
        {
            "get_order_logistics",
            "list_orders",
            "prepare_cancel_order",
            "prepare_refund",
            "prepare_invoice",
            "commit_action",
        }
    )
    assert "没有" in str(turn.result.get("current_final_answer") or "")
    assert turn.result.get("response_contract") is None

    offers = [
        row
        for row in list(turn.result.get("artifact_ledger") or [])
        if isinstance(row, dict) and str(row.get("kind") or "") == "offer"
    ]
    assert offers == []
    assert executed.port.count("preview_operation") == 0
    assert executed.port.count("execute_command") == 0
