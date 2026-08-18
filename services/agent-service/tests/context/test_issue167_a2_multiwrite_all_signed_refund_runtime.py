"""Issue #167 A2: preserve an exact signed subset before a multi-target refund write.

The fixture has two signed orders so this contract can distinguish the exact
filtered collection from both the full visible order list and an arbitrary
single-member collapse.  The first A2 enrollment is deliberately fail-closed:
plain chat text must not mint transaction authority or silently batch a
single-target refund draft capability.
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


CASE_PATH = Path(__file__).parent / "issue167_cases" / "multiwrite_all_signed_refund_a2.json"
ORACLE_PATH = Path(__file__).parent / "semantic_goal_oracle_evidence" / "multiwrite_all_signed_refund_v20_4.json"
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

    # Keep the authoritative runner untouched on success.  On the first real
    # RED, enrich the assertion with exactly the Runtime evidence needed to
    # identify the owning boundary instead of guessing from the model script.
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
                    "content": str(getattr(message, "content", ""))[:1200],
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
                    "resolution": deepcopy(result.get("resolution")),
                    "frozen_semantic_contract": deepcopy(result.get("frozen_semantic_contract")),
                    "capability_surface": deepcopy(result.get("capability_surface")),
                    "goal_alignment": deepcopy(result.get("goal_alignment")),
                    "goal_records": deepcopy(result.get("goal_records")),
                    "grounded_execution_plan": deepcopy(result.get("grounded_execution_plan")),
                    "artifact_ledger": deepcopy(result.get("artifact_ledger")),
                    "tool_trace": deepcopy(result.get("tool_trace")),
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


def test_issue167_multiwrite_refund_filters_signed_subset_before_write_boundary() -> None:
    assert ORACLE["case_id"] == CASE["id"] == "multiwrite_all_signed_refund"
    assert ORACLE["authority"]["derived_from_runtime_output"] is False
    assert ORACLE["authority"]["capability_availability_is_semantic_input"] is False
    assert ORACLE["a2_promotion_gate"]["runtime_edit_authorized"] is False

    turn2_contract = CASE["execution_contract"]["turn_contracts"][1]
    prepare_call = turn2_contract["model_steps"][1]["tool_calls"][0]
    candidate_target = prepare_call["args"]["target"]
    assert candidate_target == {
        "mode": "set_operation",
        "operator": "filter",
        "left_handle": "$previous_turn.data.result_handle",
        "status": "已签收",
        "status_span": "已签收",
    }

    executed = conversation_case_runner.run_conversation_case(CASE)
    assert [turn.user_text for turn in executed.turns] == [
        "我买了什么？",
        "已签收的都申请退款",
    ]

    # The frozen semantics own the requested write effect; capability support
    # or failure is downstream evidence and must not rewrite this Goal.
    frozen = executed.turns[1].result.get("frozen_semantic_contract") or {}
    goals = [row for row in list(frozen.get("goals") or []) if isinstance(row, dict)]
    assert len(goals) == 1
    goal = goals[0]
    assert goal.get("evidence_span") == "已签收的都申请退款"
    assert goal.get("expected_result_cardinality") == "collection"
    requested_effect = goal.get("requested_effect") or {}
    assert requested_effect.get("domain") == "refund"
    assert requested_effect.get("operation") == "create"
    assert requested_effect.get("object_type") == "order"

    prepare_rows = [
        row
        for row in list(executed.turns[1].result.get("tool_trace") or [])
        if isinstance(row, dict) and str(row.get("name") or "") == "prepare_refund"
    ]
    assert prepare_rows
    prepare_row = prepare_rows[-1]
    runtime_target = (prepare_row.get("args") or {}).get("target") or {}
    assert runtime_target.get("mode") == "set_operation"
    assert runtime_target.get("operator") == "filter"
    assert runtime_target.get("status") == "已签收"
    assert str(runtime_target.get("left_handle") or "")
    assert (prepare_row.get("result") or {}).get("code") == "UNSUPPORTED_TARGET_CARDINALITY"

    # This A2 exercises the safe clarification branch of the canonical
    # clarify-or-split boundary.  It may prove a multi-member target, but it
    # must not turn that plain-language collection into transaction authority.
    offers = [
        row
        for row in list(executed.final.result.get("artifact_ledger") or [])
        if isinstance(row, dict) and str(row.get("kind") or "") == "offer"
    ]
    assert offers == []
    assert executed.port.count("preview_operation") == 0
    assert executed.port.count("execute_command") == 0

    trace_names = {
        str(row.get("name") or "")
        for row in list(executed.final.result.get("tool_trace") or [])
        if isinstance(row, dict)
    }
    assert "prepare_refund" in trace_names
    assert "commit_action" not in trace_names
