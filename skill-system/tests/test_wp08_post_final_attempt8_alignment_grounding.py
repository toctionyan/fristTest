from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services/agent-service"
AGENT_SRC = AGENT_ROOT / "src"
for path in (AGENT_ROOT, AGENT_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_smoke():
    path = AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("wp08_post_attempt8_alignment_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _goals() -> list[dict]:
    return [
        {
            "goal_id": "g1",
            "description": "查鼠标订单",
            "evidence_span": "查一下鼠标订单",
            "requested_effect": {"domain": "order", "operation": "list", "object_type": "order"},
            "depends_on": [],
        },
        {
            "goal_id": "g2",
            "description": "申请退款",
            "evidence_span": "帮我申请退款",
            "requested_effect": {"domain": "refund", "operation": "create", "object_type": "order"},
            "depends_on": [],
        },
    ]


def test_ungrounded_incomplete_claim_uses_existing_second_call_for_grounding_reaudit() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    user_text = "查一下鼠标订单，然后帮我申请退款"
    responses = [
        (
            SimpleNamespace(content=json.dumps({
                "verdict": "incomplete",
                "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
                "missing_spans": ["退款目标没有绑定"],
                "reason_code": "refund_target_missing",
            }, ensure_ascii=False)),
            {},
        ),
        (
            SimpleNamespace(content=json.dumps({
                "verdict": "exact",
                "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
                "missing_spans": [],
                "reason_code": "all_requested_outcomes_preserved",
            }, ensure_ascii=False)),
            {},
        ),
    ]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=responses
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=user_text,
            goals=_goals(),
            known_tools=set(),
        )
    assert verdict.exact
    assert invoke.call_count == 2
    assert verdict.details["verifier_repair_attempted"] is True
    assert verdict.details["verifier_repair_kind"] == "incomplete_claim_grounding_reaudit"
    repair = invoke.call_args_list[1].kwargs["payload"][-1].content
    assert "prior claim did not identify any machine-grounded omitted outcome" in repair
    assert "exact literal contiguous substring of USER_TEXT" in repair
    assert "withdraw the incomplete claim and return exact" in repair
    assert "tool/capability/oracle" in repair
    assert "退款目标没有绑定" not in repair


def test_persistent_ungrounded_incomplete_claim_remains_fail_closed_after_two_calls() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    user_text = "查一下鼠标订单，然后帮我申请退款"
    response = (
        SimpleNamespace(content=json.dumps({
            "verdict": "incomplete",
            "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
            "missing_spans": ["退款目标没有绑定"],
            "reason_code": "refund_target_missing",
        }, ensure_ascii=False)),
        {},
    )
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[response, response]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=user_text,
            goals=_goals(),
            known_tools=set(),
        )
    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_alignment_missing_span_not_grounded"
    assert verdict.details["original_verdict"] == "incomplete"
    assert verdict.details["grounding_failure"] == "missing_spans"
    assert verdict.details["verifier_repair_attempted"] is True
    assert verdict.details["verifier_repair_kind"] == "incomplete_claim_grounding_reaudit"
    assert invoke.call_count == 2


def test_ungrounded_exact_claim_can_restate_literal_evidence_on_existing_second_call() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    responses = [
        (
            SimpleNamespace(content=json.dumps({
                "verdict": "exact",
                "evidence_spans": ["订单查询和退款申请都覆盖了"],
                "missing_spans": [],
                "reason_code": "exact",
            }, ensure_ascii=False)),
            {},
        ),
        (
            SimpleNamespace(content=json.dumps({
                "verdict": "exact",
                "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
                "missing_spans": [],
                "reason_code": "literal_evidence_grounded",
            }, ensure_ascii=False)),
            {},
        ),
    ]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=responses
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text="查一下鼠标订单，然后帮我申请退款",
            goals=_goals(),
            known_tools=set(),
        )
    assert verdict.exact
    assert verdict.details["verifier_repair_kind"] == "exact_claim_grounding_reaudit"
    assert invoke.call_count == 2


def test_non_json_format_repair_contract_is_preserved() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    responses = [
        (SimpleNamespace(content="not-json"), {}),
        (
            SimpleNamespace(content=json.dumps({
                "verdict": "exact",
                "evidence_spans": ["查订单"],
                "missing_spans": [],
                "reason_code": "exact",
            }, ensure_ascii=False)),
            {},
        ),
    ]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=responses
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text="查订单",
            goals=[{"goal_id": "g1", "evidence_span": "查订单"}],
            known_tools=set(),
        )
    assert verdict.exact
    assert verdict.details["verifier_repair_kind"] == "machine_format_repair"
    assert invoke.call_count == 2
    assert "FORMAT_REPAIR" in invoke.call_args_list[1].kwargs["payload"][-1].content


def test_sanitized_alignment_diagnostic_exposes_grounding_state_without_raw_untrusted_claim() -> None:
    smoke = _load_smoke()
    result = {
        "code": "GOAL_ALIGNMENT_UNVERIFIED",
        "data": {
            "alignment_proof": {
                "verdict": "indeterminate",
                "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
                "missing_spans": [],
                "reason_code": "goal_alignment_missing_span_not_grounded",
                "source": "model",
                "independent": True,
                "details": {
                    "original_verdict": "incomplete",
                    "grounding_failure": "missing_spans",
                    "verifier_repair_attempted": True,
                    "verifier_repair_kind": "incomplete_claim_grounding_reaudit",
                },
            },
        },
    }
    diagnostic = smoke._sanitized_goal_rejection_diagnostic(result)
    alignment = diagnostic["alignment"]
    assert alignment["verdict"] == "indeterminate"
    assert alignment["original_verdict"] == "incomplete"
    assert alignment["grounding_failure"] == "missing_spans"
    assert alignment["verifier_repair_attempted"] is True
    assert alignment["verifier_repair_kind"] == "incomplete_claim_grounding_reaudit"
    assert alignment["missing_spans"] == []


def test_call_budget_and_outer_slas_do_not_increase() -> None:
    smoke = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
    config = (AGENT_SRC / "agent_core/config.py").read_text(encoding="utf-8")
    browser = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
    planning = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    assert "for attempt in range(2):" in planning
    assert 'model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes")' in smoke
    assert "1 declaration + 2 alignment + 2 granularity" in smoke
    assert '_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0' in config
    assert '_bounded_int_env("MODEL_MAX_RETRIES", 1' in config
    assert '{ timeout: 120_000 }' in browser
