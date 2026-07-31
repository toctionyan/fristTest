"""Regression for planning-to-execution capability-surface binding.

A proven unsupported goal is a successful boundary outcome, not an execution
failure. The exact discovery surface used to build the plan must survive the
LangGraph State channel and be consumed by CapabilityGate unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_core.business import configure_business_port
from agent_core.config import clear_checkpointer_cache
from agent_core.lifecycle.state import State
from agent_core.persistence.store_provider import get_store_provider, reset_store_provider_cache
from agent_modules.ecommerce.business_port import (
    get_ecommerce_business_port,
    reset_ecommerce_business_port_cache,
)
from tests.support.conversation_case_runner import run_conversation_case

CATALOG = (
    Path(__file__).parents[1]
    / "context"
    / "strong_context_cases"
    / "semantic_goal_coverage_suite_v20_4.json"
)
CASE = next(
    row
    for row in json.loads(CATALOG.read_text(encoding="utf-8"))["cases"]
    if row["id"] == "semantic_unsupported_courier_phone"
)


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


def test_capability_surface_is_a_declared_state_channel() -> None:
    assert "capability_surface" in State.__annotations__


def test_proven_unsupported_goal_completes_without_similar_capability() -> None:
    executed = run_conversation_case(CASE)
    result = executed.final.result
    workflow = result.get("grounded_execution_plan") or {}

    assert result["status"] == "GeneralFinalAnswer"
    assert workflow["status"] == "SUCCEEDED"
    assert [row["tool_name"] for row in workflow["steps"]] == [
        "report_unsupported_request"
    ]
    proof = next(
        row["match_proof"]
        for row in result["tool_trace"]
        if row.get("name") == "report_unsupported_request"
    )
    assert proof["capability_surface"] == {
        "required": True,
        "allowed": True,
        "goal_ids": ["g1"],
        "candidate_tools": ["report_unsupported_request"],
    }
    assert proof["goal_effect_identity"]["allowed"] is True
    assert proof["goal_effect_identity"]["goals"][0]["role"] == "unsupported_report"
    assert not any(
        row.get("name") in {"prepare_cancel_order", "prepare_refund"}
        for row in result["tool_trace"]
    )
