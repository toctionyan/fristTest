"""Execute the curated high-signal conversation matrix through the real graph.

The deterministic model is only a candidate-input source.  The runner asserts
the actual lifecycle trace, capability permits, WorkflowPlan, draft/receipt
state, BusinessPort calls and public result for each declared user turn.  The
larger catalog remains a scenario inventory; entries not named by
``executable_case_ids`` are not counted as passing runtime evidence.
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


CATALOG = Path(__file__).parent / "strong_context_cases" / "conversation_runtime_contract_suite_v20_4.json"
SUITE = json.loads(CATALOG.read_text(encoding="utf-8"))
CASE_BY_ID = {str(row["id"]): row for row in SUITE["cases"]}
EXECUTABLE_CASE_IDS = [str(value) for value in SUITE["executable_case_ids"]]
assert len(EXECUTABLE_CASE_IDS) == int(SUITE["execution_case_count"])
assert len(EXECUTABLE_CASE_IDS) == len(set(EXECUTABLE_CASE_IDS))
assert set(EXECUTABLE_CASE_IDS).issubset(CASE_BY_ID)
CASES = [CASE_BY_ID[case_id] for case_id in EXECUTABLE_CASE_IDS]


def _user_turns(case: dict) -> list[str]:
    return [
        str(turn["text"])
        for turn in case.get("turns") or []
        if isinstance(turn, dict) and turn.get("role") == "user" and str(turn.get("text") or "")
    ]


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch, tmp_path):
    """Keep catalog runs deterministic and unable to reach a developer runtime."""
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


@pytest.mark.parametrize("case", CASES, ids=lambda row: row["id"])
def test_conversation_case_executes_real_lifecycle_contract(case: dict) -> None:
    executed = run_conversation_case(case)

    declared_turns = _user_turns(case)
    assert [turn.user_text for turn in executed.turns] == declared_turns
    assert len(executed.turns) == len(case["execution_contract"]["turn_contracts"])
    # The runner's detailed assertions establish the release contract.  Keep a
    # concise final guard here so a refactor cannot turn this back into a JSON
    # replay test that never invokes the lifecycle graph.
    assert all(
        turn.result.get("grounded_execution_plan") or turn.result.get("grounded_execution_plan")
        for turn in executed.turns
    )
