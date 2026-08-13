"""Release semantic cases with an independent goal oracle.

The scripted model remains deterministic, but the meaning of each user turn is
specified separately in ``goal_oracle``.  The test therefore fails when a
candidate script omits a branch, uses a nearby capability, or declares a goal
that does not match the user evidence span.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_core.business import configure_business_port
from agent_core.config import clear_checkpointer_cache
from agent_core.persistence.store_provider import get_store_provider, reset_store_provider_cache
from agent_modules.ecommerce.business_port import get_ecommerce_business_port, reset_ecommerce_business_port_cache
from tests.support.canonical_semantic_fixture import canonicalize_scripted_live_goal_fixture
from tests.support.conversation_case_runner import run_conversation_case

CATALOG = Path(__file__).parent / "strong_context_cases" / "semantic_goal_coverage_suite_v20_4.json"
CASES = [
    canonicalize_scripted_live_goal_fixture(
        row,
        suite_id="semantic_goal_coverage_suite_v20_4",
    )
    for row in json.loads(CATALOG.read_text(encoding="utf-8"))["cases"]
]


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


@pytest.mark.parametrize("case", CASES, ids=lambda row: row["id"])
def test_semantic_goal_oracle_and_runtime_are_both_satisfied(case: dict) -> None:
    executed = run_conversation_case(case)
    workflow = (
        executed.final.result.get("grounded_execution_plan")
        or executed.final.result.get("grounded_execution_plan")
        or {}
    )
    assert workflow.get("goal_coverage_complete") is True
