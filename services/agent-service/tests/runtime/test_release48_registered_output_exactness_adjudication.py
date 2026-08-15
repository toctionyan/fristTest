from __future__ import annotations

import json
from types import SimpleNamespace

import agent_core.config as config_module
import agent_core.model_calls as model_calls_module
from agent_core.lifecycle import goal_planning
from agent_core.lifecycle.goal_planning import (
    ModelGoalAlignmentVerifier,
    _declared_registered_output_exactness_risk,
)


def _vocabulary() -> dict:
    return {
        "version": "semantic-output-vocabulary@1",
        "authority": "domain_semantics_only_capability_independent",
        "availability_exposed": False,
        "tool_names_exposed": False,
        "outputs": [
            {
                "output_id": "refund.status",
                "subject_type": "refund",
                "effect_kinds": ["read"],
                "description": "读取已经存在的退款申请记录以及当前处理状态。",
            }
        ],
    }


def _goal(*, output_id: str, evidence_span: str, open_description: str = "") -> dict:
    output = {"output_id": output_id, "evidence_span": evidence_span}
    if open_description:
        output["open_description"] = open_description
    return {
        "goal_id": "g1",
        "description": evidence_span,
        "evidence_span": evidence_span,
        "goal_type": "query",
        "required": True,
        "depends_on": [],
        "requested_effect": {
            "domain": "refund",
            "operation": "query",
            "object_type": "refund",
            "raw_description": evidence_span,
            "requested_outputs": [output],
        },
        "expected_result_cardinality": "single",
    }


def _run_verifier(monkeypatch, *, user_text: str, goals: list[dict], responses: list[dict]):
    calls: list[dict] = []

    def fake_structured_verifier_messages(*, role, instruction, decision_rules, payload, format_repair=None):
        row = {
            "role": role,
            "instruction": instruction,
            "decision_rules": list(decision_rules),
            "payload": payload,
            "format_repair": format_repair,
        }
        calls.append(row)
        return row

    def fake_invoke_model(*, purpose, model, payload):
        index = len(calls) - 1
        return SimpleNamespace(content=json.dumps(responses[index], ensure_ascii=False)), {"purpose": purpose}

    monkeypatch.setattr(config_module, "get_model", lambda: object())
    monkeypatch.setattr(model_calls_module, "structured_verifier_messages", fake_structured_verifier_messages)
    monkeypatch.setattr(model_calls_module, "invoke_model", fake_invoke_model)
    monkeypatch.setattr(goal_planning, "_semantic_vocabulary_for_alignment", _vocabulary)

    verdict = ModelGoalAlignmentVerifier().verify(
        user_text=user_text,
        goals=goals,
        known_tools=set(),
    )
    return verdict, calls


def test_release48_structural_risk_marks_non_open_identity_but_not_open():
    registered = _declared_registered_output_exactness_risk([
        _goal(output_id="refund.status", evidence_span="退款什么时候到账")
    ])
    assert registered == {
        "risk": True,
        "claims": [{
            "goal_id": "g1",
            "output_id": "refund.status",
            "evidence_span": "退款什么时候到账",
        }],
        "capability_registry_consulted": False,
        "language_interpretation_used": False,
        "runtime_rejection_authority": False,
    }

    open_risk = _declared_registered_output_exactness_risk([
        _goal(
            output_id="open",
            evidence_span="退款什么时候到账",
            open_description="退款到账时间",
        )
    ])
    assert open_risk["risk"] is False
    assert open_risk["claims"] == []


def test_release48_third_slot_adversarially_rejects_nearby_registered_output(monkeypatch):
    user_text = "鼠标订单的退款什么时候到账"
    evidence = "退款什么时候到账"
    goals = [_goal(output_id="refund.status", evidence_span=evidence)]
    responses = [
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "goal_alignment_exact",
        },
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "dependency_decisions": [],
            "reason_code": "goal_alignment_exact",
        },
        {
            "verdict": "incomplete",
            "evidence_spans": [evidence],
            "missing_spans": [evidence],
            "reason_code": "semantic_substitution",
        },
    ]

    verdict, calls = _run_verifier(
        monkeypatch,
        user_text=user_text,
        goals=goals,
        responses=responses,
    )

    assert len(calls) == 3
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "semantic_substitution"
    assert verdict.missing_spans == (evidence,)
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_requested_output_exactness_adjudication"

    third = calls[2]
    assert "Start each listed identity from the hypothesis of semantic substitution" in third["format_repair"]
    assert third["payload"]["REGISTERED_OUTPUT_EXACTNESS_RISK"]["claims"] == [{
        "goal_id": "g1",
        "output_id": "refund.status",
        "evidence_span": evidence,
    }]
    assert third["payload"]["CANONICAL_SEMANTIC_OUTPUT_VOCABULARY"] == _vocabulary()
    risk_payload = third["payload"]["REGISTERED_OUTPUT_EXACTNESS_RISK"]
    assert risk_payload["capability_registry_consulted"] is False
    assert "tool_name" not in json.dumps(risk_payload, ensure_ascii=False).casefold()
    assert "available" not in json.dumps(risk_payload, ensure_ascii=False).casefold()


def test_release48_exact_registered_output_survives_adversarial_third_slot(monkeypatch):
    user_text = "鼠标订单退款状态怎么样"
    evidence = "退款状态怎么样"
    goals = [_goal(output_id="refund.status", evidence_span=evidence)]
    responses = [
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "goal_alignment_exact",
        },
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "dependency_decisions": [],
            "reason_code": "goal_alignment_exact",
        },
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "reason_code": "requested_output_exactness_confirmed",
        },
    ]

    verdict, calls = _run_verifier(
        monkeypatch,
        user_text=user_text,
        goals=goals,
        responses=responses,
    )

    assert len(calls) == 3
    assert verdict.exact is True
    assert verdict.reason_code == "goal_alignment_candidate_blind_dependency_reaudit_exact"
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True


def test_release48_open_identity_does_not_spend_registered_output_third_slot(monkeypatch):
    user_text = "鼠标订单的退款什么时候到账"
    evidence = "退款什么时候到账"
    goals = [_goal(output_id="open", evidence_span=evidence, open_description="退款到账时间")]
    responses = [
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "goal_alignment_exact",
        },
        {
            "verdict": "exact",
            "evidence_spans": [evidence],
            "missing_spans": [],
            "dependency_decisions": [],
            "reason_code": "goal_alignment_exact",
        },
    ]

    verdict, calls = _run_verifier(
        monkeypatch,
        user_text=user_text,
        goals=goals,
        responses=responses,
    )

    assert len(calls) == 2
    assert verdict.exact is True
