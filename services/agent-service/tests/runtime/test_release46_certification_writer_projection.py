from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import SystemMessage

from agent_core.lifecycle.dialogue_runtime import _semantic_writer_declaration_result_projection


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_preprod_conversation_smoke.py"
SPEC = importlib.util.spec_from_file_location("release46_preprod_semantic_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


def _release46_scope_rejection() -> dict:
    return {
        "ok": False,
        "code": "GOAL_DECLARATION_INCOMPLETE",
        "message": "redeclare",
        "data": {
            "errors": ["GOAL_DECLARATION_INCOMPLETE"],
            "current_user_input": "鼠标订单的退款什么时候到账",
            "repair_contract": {
                "authority": "current_user_input_only",
                "required_action": "redeclaration",
            },
            "alignment_proof": {
                "verdict": "incomplete",
                "reason_code": "target-scope-constraint fidelity",
                "source": "model",
                "independent": True,
                "evidence_spans": ["鼠标订单的退款什么时候到账"],
                "missing_spans": ["鼠标订单"],
                "details": {
                    "dependency_authority": "independent_goal_alignment",
                    "dependency_proof_complete": True,
                    "dependency_graph_match": True,
                    "dependency_edges": [],
                    "verifier_repair_attempted": True,
                    "verifier_repair_kind": "candidate_blind_dependency_scope_constraint_adjudication",
                    "candidate_semantic_replacement": "audit_secret_must_not_reach_writer",
                },
            },
            "independent_verifier_feedback": {
                "authority": "independent_goal_alignment",
                "required_action": "redeclaration_removing_rejected_scope_constraints",
                "violation_field": "target_candidate.scope_constraints",
                "invalid_scope_constraint_spans": ["鼠标订单"],
                "replacement_target": "audit_secret_must_not_reach_writer",
                "constraints": ["audit_only_detail"],
            },
        },
    }


def test_release46_certification_adapter_is_exact_live_runtime_projection():
    raw = _release46_scope_rejection()
    message = HARNESS._semantic_writer_rejection_tool_message(
        tool_call_id="release46:declare:1",
        result=raw,
    )
    projected = json.loads(message.content)

    assert projected == _semantic_writer_declaration_result_projection(raw)
    assert "alignment_proof" not in projected["data"]
    feedback = projected["data"]["independent_verifier_feedback"]
    assert feedback["authority"] == "read_only_violation_evidence"
    assert feedback["required_action"] == "redeclaration_from_current_user_input"
    assert feedback["violation"] == {
        "field": "target_candidate.scope_constraints",
        "reason_code": "target-scope-constraint fidelity",
        "evidence_spans": ["鼠标订单"],
    }
    assert (
        "remove_only_listed_invalid_scope_constraint_entries_and_preserve_other_literal_population_narrowing_constraints"
        in feedback["constraints"]
    )
    serialized = json.dumps(projected, ensure_ascii=False, sort_keys=True)
    assert "candidate_semantic_replacement" not in serialized
    assert "replacement_target" not in serialized
    assert "audit_secret_must_not_reach_writer" not in serialized


def test_release46_normal_bounded_repair_feeds_projected_tool_message(monkeypatch):
    raw = _release46_scope_rejection()
    responses = [SimpleNamespace(name="first"), SimpleNamespace(name="second")]
    calls = [
        {"id": "call-1", "name": "declare_turn_goals", "args": {"goals": [{"goal_id": "g1"}]}},
        {"id": "call-2", "name": "declare_turn_goals", "args": {"goals": [{"goal_id": "g1"}]}},
    ]
    provider_payloads: list[list] = []

    def fake_invoke_model(*, purpose, model, payload):
        provider_payloads.append(list(payload))
        return responses[len(provider_payloads) - 1], {"purpose": purpose}

    monkeypatch.setattr(HARNESS, "invoke_model", fake_invoke_model)
    monkeypatch.setattr(HARNESS, "attest_real_model_metadata", lambda **_: {"ok": True})
    monkeypatch.setattr(HARNESS, "tool_calls", lambda _: [calls.pop(0)])

    validation_attempts = 0

    def fake_validate(**kwargs):
        nonlocal validation_attempts
        validation_attempts += 1
        if validation_attempts == 1:
            raise HARNESS._ProductionGoalDeclarationRejected(
                case_id=kwargs["case_id"],
                result=raw,
            )
        return {"goals": kwargs["goals"]}

    monkeypatch.setattr(HARNESS, "_validate_with_production_goal_contract", fake_validate)

    _, declared, _, attempts = HARNESS._declare_with_bounded_production_repair(
        case_id="semantic_refund_arrival_query",
        user_text="鼠标订单的退款什么时候到账",
        bound=object(),
        system=SystemMessage(content="system"),
        identity={},
    )

    assert attempts == 2
    assert declared == {"goals": [{"goal_id": "g1"}]}
    assert len(provider_payloads) == 2
    second_tool_message = provider_payloads[1][-1]
    assert second_tool_message.name == "declare_turn_goals"
    assert json.loads(second_tool_message.content) == _semantic_writer_declaration_result_projection(raw)
    assert "alignment_proof" not in json.loads(second_tool_message.content)["data"]
