"""Issue #167 A2: pause one open task, finish an interrupting read, then resume safely.

The A1 oracle fixes the conversational meaning independently of Runtime output.
A2 uses the real lifecycle and transaction authority: the mouse refund Draft is
paused as a semantic Goal while earphone logistics is current, then the same
Draft is re-surfaced on resume without treating resume text as authorization.
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
from tests.support import conversation_case_runner


CASE_PATH = Path(__file__).parent / "issue167_cases" / "task_refund_pause_logistics_resume_a2.json"
ORACLE_PATH = (
    Path(__file__).parent
    / "semantic_goal_oracle_evidence"
    / "task_refund_pause_logistics_resume_v20_4.json"
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


def _trace_names(turn_result: dict) -> set[str]:
    return {
        str(row.get("name") or "")
        for row in list(turn_result.get("tool_trace") or [])
        if isinstance(row, dict)
    }


def _tool_results(turn_result: dict, tool_name: str) -> list[dict]:
    return [
        row.get("result") if isinstance(row.get("result"), dict) else {}
        for row in list(turn_result.get("tool_trace") or [])
        if isinstance(row, dict) and str(row.get("name") or "") == tool_name
    ]


def _offers(turn_result: dict) -> list[dict]:
    return [
        row
        for row in list(turn_result.get("artifact_ledger") or [])
        if isinstance(row, dict) and str(row.get("kind") or "") == "offer"
    ]


def _goal_record(turn_result: dict, goal_id: str) -> dict:
    rows = [
        row
        for row in list(turn_result.get("goal_records") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "") == goal_id
    ]
    assert len(rows) == 1, rows
    return rows[0]


def _install_first_red_diagnostics(monkeypatch) -> None:
    """Preserve every shared assertion, but enrich the first failing turn.

    This is audit-only instrumentation for Pack A. It does not skip, weaken or
    replace any conversation contract assertion and can be removed once the
    first meaningful owner is localized.
    """
    original = conversation_case_runner._assert_turn_contract

    def _diagnostic_assert_turn_contract(**kwargs):
        try:
            return original(**kwargs)
        except AssertionError as exc:
            model = kwargs.get("model")
            result = kwargs.get("result") if isinstance(kwargs.get("result"), dict) else {}
            trace = []
            for row in list(result.get("tool_trace") or []):
                if not isinstance(row, dict):
                    continue
                tool_result = row.get("result") if isinstance(row.get("result"), dict) else {}
                trace.append(
                    {
                        "name": str(row.get("name") or ""),
                        "classification": str(row.get("classification") or ""),
                        "ok": tool_result.get("ok"),
                        "code": tool_result.get("code"),
                        "message": tool_result.get("message"),
                    }
                )
            details = {
                "user_text": kwargs.get("user_text"),
                "status": result.get("status"),
                "phase": result.get("phase"),
                "workflow_state": result.get("workflow_state"),
                "emitted_tool_calls": [
                    {
                        "name": str(call.get("name") or ""),
                        "args": call.get("args"),
                    }
                    for call in list(getattr(model, "emitted_tool_calls", []) or [])
                    if isinstance(call, dict)
                ],
                "emitted_tool_batches": list(getattr(model, "emitted_tool_batches", []) or []),
                "invoked_bound_tool_history": [
                    sorted(str(name) for name in names)
                    for names in list(getattr(model, "invoked_bound_tool_history", []) or [])
                ],
                "remaining_steps": getattr(model, "remaining_steps", None),
                "goal_records": result.get("goal_records"),
                "current_turn_plan": result.get("current_turn_plan"),
                "frozen_semantic_contract": result.get("frozen_semantic_contract"),
                "capability_surface": result.get("capability_surface"),
                "latest_execution_disposition": result.get("latest_execution_disposition"),
                "action_gateway_result": result.get("action_gateway_result"),
                "tool_trace": trace,
            }
            raise AssertionError(
                f"{exc}\nISSUE167_TASK_RESUME_FIRST_RED="
                + json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)
            ) from exc

    monkeypatch.setattr(
        conversation_case_runner,
        "_assert_turn_contract",
        _diagnostic_assert_turn_contract,
    )


def test_issue167_refund_task_pauses_for_logistics_and_resumes_same_draft(monkeypatch) -> None:
    assert ORACLE["case_id"] == CASE["id"] == "task_refund_pause_logistics_resume"
    assert ORACLE["authority"]["derived_from_runtime_output"] is False
    _install_first_red_diagnostics(monkeypatch)

    executed = conversation_case_runner.run_conversation_case(CASE)
    assert len(executed.turns) == 3
    refund_turn, logistics_turn, resume_turn = executed.turns

    assert refund_turn.user_text == "帮我申请鼠标退款"
    assert logistics_turn.user_text == "先查一下耳机物流"
    assert resume_turn.user_text == "继续刚才那个"

    first_offers = _offers(refund_turn.result)
    assert len(first_offers) == 1
    first_offer = first_offers[0]
    first_handle = str(first_offer.get("handle") or "")
    assert first_handle
    assert str(first_offer.get("target_handle") or "").endswith(":10003")
    assert str(first_offer.get("draft_state") or "") == "AWAITING_AUTHORIZATION"
    assert {"prepare_refund", "action_gateway"}.issubset(_trace_names(refund_turn.result))
    assert "commit_action" not in _trace_names(refund_turn.result)

    paused_refund = _goal_record(logistics_turn.result, "refund_goal")
    assert str(paused_refund.get("lifecycle") or "") == "PAUSED"
    assert str(paused_refund.get("last_change_evidence_span") or "") == "先"
    assert _trace_names(logistics_turn.result).isdisjoint({"prepare_refund", "commit_action"})
    assert "get_order_logistics" in _trace_names(logistics_turn.result)

    logistics_reads = [
        str(call.get("resource_id") or "")
        for call in executed.port.calls
        if call.get("kind") == "read_resource" and call.get("resource_type") == "logistics"
    ]
    assert logistics_reads == ["10001"]

    resume_results = _tool_results(resume_turn.result, "prepare_refund")
    assert len(resume_results) == 1 and resume_results[0].get("ok") is True
    resume_data = resume_results[0].get("data") or {}
    assert resume_data.get("offer_reused") is True
    assert str(resume_data.get("offer_handle") or "") == first_handle
    assert {"prepare_refund", "action_gateway"}.issubset(_trace_names(resume_turn.result))
    assert _trace_names(resume_turn.result).isdisjoint({"get_order_logistics", "commit_action"})

    resumed_refund = _goal_record(resume_turn.result, "refund_goal")
    assert str(resumed_refund.get("lifecycle") or "") != "PAUSED"
    assert str(resumed_refund.get("last_change_evidence_span") or "") == "继续"

    final_offers = _offers(resume_turn.result)
    assert len(final_offers) == 1
    assert str(final_offers[0].get("handle") or "") == first_handle
    assert str(final_offers[0].get("target_handle") or "").endswith(":10003")
    assert str(final_offers[0].get("draft_state") or "") == "AWAITING_AUTHORIZATION"

    preview_calls = [
        call for call in executed.port.calls
        if call.get("kind") == "preview_operation" and call.get("operation") == "APPLY_REFUND"
    ]
    assert len(preview_calls) == 1
    assert str(preview_calls[0].get("resource_id") or "") == "10003"
    assert executed.port.count("execute_command") == 0
