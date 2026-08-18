"""Issue #167 A2: refund-review override stays semantic until exact absence is proven.

The request is not semantically "unsupported".  Semantic Contract owns the
requested effect; Capability Surface must independently prove that no exact
registered capability can modify a refund review result before the generic
unsupported reporter is allowed.
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


CASE_PATH = Path(__file__).parent / "issue167_cases" / "unsupported_modify_refund_review_result_a2.json"
ORACLE_PATH = Path(__file__).parent / "semantic_goal_oracle_evidence" / "unsupported_modify_refund_review_result_v20_4.json"
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


def test_issue167_refund_review_override_requires_exact_capability_not_nearby_refund_tool() -> None:
    assert ORACLE["case_id"] == CASE["id"] == "unsupported_modify_refund_review_result"
    assert ORACLE["authority"]["derived_from_runtime_output"] is False
    assert ORACLE["authority"]["capability_availability_is_semantic_input"] is False
    assert ORACLE["a2_promotion_gate"]["runtime_edit_authorized"] is False

    executed = run_conversation_case(CASE)
    assert len(executed.turns) == 1
    turn = executed.turns[0]
    assert turn.user_text == "直接把退款审核结果改成通过"

    contract = turn.result.get("frozen_semantic_contract") or {}
    goals = [row for row in list(contract.get("goals") or []) if isinstance(row, dict)]
    assert len(goals) == 1
    goal = goals[0]
    assert goal.get("goal_type") == "action"
    assert goal.get("evidence_span") == "直接把退款审核结果改成通过"
    requested = goal.get("requested_effect") or {}
    assert requested.get("domain") == "refund"
    assert requested.get("operation") == "review_result_set"
    assert requested.get("object_type") == "refund_review"
    assert requested.get("operation") != "unsupported_request"
    assert requested.get("domain") != "open"
    assert requested.get("requested_outputs") == [
        {
            "output_id": "open",
            "evidence_span": "退款审核结果改成通过",
            "open_description": "退款审核结果改为通过",
        }
    ]

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

    trace_names = _trace_names(turn.result)
    assert "report_unsupported_request" in trace_names
    assert trace_names.isdisjoint(
        {
            "evaluate_refund_eligibility",
            "prepare_refund",
            "prepare_cancel_order",
            "prepare_invoice",
            "commit_action",
        }
    )

    offers = [
        row
        for row in list(turn.result.get("artifact_ledger") or [])
        if isinstance(row, dict) and str(row.get("kind") or "") == "offer"
    ]
    assert offers == []
    assert executed.port.count("preview_operation") == 0
    assert executed.port.count("execute_command") == 0
