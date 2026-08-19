"""Issue #167 A2: preserve a visible signed-order collection through pronoun use.

The case intentionally uses two signed orders.  A one-member fixture cannot
prove that ``它们`` preserves collection cardinality instead of silently
selecting one arbitrary target.
"""
from __future__ import annotations

from copy import deepcopy
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
from tests.support import conversation_case_runner
from tests.support.conversation_case_fixtures import (
    FixtureBusinessPort,
    fixture_ledger,
    fixture_orders,
)


CASE_PATH = Path(__file__).parent / "issue167_cases" / "pronoun_them_after_signed_filter_a2.json"
ORACLE_PATH = Path(__file__).parent / "semantic_goal_oracle_evidence" / "pronoun_them_after_signed_filter_v20_4.json"
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

    variant = str((CASE.get("execution_contract") or {}).get("fixture", {}).get("variant") or "default")
    orders = fixture_orders(variant)

    def _variant_port() -> FixtureBusinessPort:
        return FixtureBusinessPort(orders=deepcopy(orders))

    def _variant_ledger(*, tenant_id: str, user_id: str, thread_id: str):
        return fixture_ledger(
            tenant_id=tenant_id,
            user_id=user_id,
            thread_id=thread_id,
            orders=orders,
        )

    monkeypatch.setattr(conversation_case_runner, "FixtureBusinessPort", _variant_port)
    monkeypatch.setattr(conversation_case_runner, "fixture_ledger", _variant_ledger)

    # Keep the authoritative runner assertions unchanged, but enrich a failure
    # with runtime state so the next repair is based on evidence instead of on
    # guessing from the scripted candidate.  This wrapper never catches a pass.
    original_assert_turn_contract = conversation_case_runner._assert_turn_contract

    def _diagnostic_assert_turn_contract(**kwargs):
        try:
            return original_assert_turn_contract(**kwargs)
        except AssertionError as exc:
            model = kwargs["model"]
            result = kwargs["result"]
            messages = [
                {
                    "type": message.__class__.__name__,
                    "content": str(getattr(message, "content", ""))[:1000],
                    "tool_calls": getattr(message, "tool_calls", None),
                }
                for message in list(result.get("messages") or [])[-8:]
            ]
            raise AssertionError(
                {
                    "original": str(exc),
                    "user_text": kwargs.get("user_text"),
                    "emitted_tool_calls": deepcopy(model.emitted_tool_calls),
                    "emitted_tool_batches": deepcopy(model.emitted_tool_batches),
                    "invoked_bound_tools": [sorted(names) for names in model.invoked_bound_tool_history],
                    "remaining_steps": model.remaining_steps,
                    "status": result.get("status"),
                    "last_error": result.get("last_error"),
                    "pending_reason": result.get("pending_reason"),
                    "resolution": result.get("resolution"),
                    "goal_alignment": result.get("goal_alignment"),
                    "goal_records": result.get("goal_records"),
                    "messages_tail": messages,
                }
            ) from exc

    monkeypatch.setattr(conversation_case_runner, "_assert_turn_contract", _diagnostic_assert_turn_contract)

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


def test_issue167_pronoun_preserves_signed_collection_for_refund_consultation() -> None:
    assert ORACLE["case_id"] == CASE["id"] == "pronoun_them_after_signed_filter"
    assert ORACLE["authority"]["derived_from_runtime_output"] is False

    executed = conversation_case_runner.run_conversation_case(CASE)
    assert [turn.user_text for turn in executed.turns] == [
        "我都买了什么？",
        "哪些签收了？",
        "它们都能退吗？",
    ]

    # Both members of the filtered signed subset must reach the authoritative
    # read-only eligibility preview boundary, exactly once each.  No wider
    # population and no arbitrary single-member collapse is accepted.
    eligibility_previews = [
        call
        for call in executed.port.calls
        if call.get("kind") == "preview_operation" and call.get("operation") == "APPLY_REFUND"
    ]
    assert [str(call.get("resource_id") or "") for call in eligibility_previews] == ["10001", "10002"]
    assert executed.port.count("execute_command") == 0

    final_trace_names = {
        str(row.get("name") or "")
        for row in list(executed.final.result.get("tool_trace") or [])
        if isinstance(row, dict)
    }
    assert "evaluate_refund_eligibility" in final_trace_names
    assert "prepare_refund" not in final_trace_names
    assert "commit_action" not in final_trace_names
