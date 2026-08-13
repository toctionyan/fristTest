from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "verify_preprod_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("verify_preprod_conversation_smoke_attempt4_literal", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _goals(second_span: str) -> list[dict]:
    return [
        {
            "goal_id": "g1",
            "description": "查订单",
            "evidence_span": "查订单",
            "requested_effect": {
                "domain": "order",
                "operation": "query",
                "object_type": "order",
            },
            "expected_result_cardinality": "collection",
            "depends_on": [],
        },
        {
            "goal_id": "g2",
            "description": "再查物流",
            "evidence_span": second_span,
            "requested_effect": {
                "domain": "logistics",
                "operation": "query",
                "object_type": "shipment",
            },
            "expected_result_cardinality": "collection",
            "depends_on": ["g1"],
        },
    ]


def _response(label: str, second_span: str):
    return SimpleNamespace(label=label, goals=_goals(second_span))


def _pure_literal_result(user_text: str) -> dict:
    return {
        "ok": False,
        "code": "GOAL_DECLARATION_INVALID",
        "message": "literal evidence rejected",
        "data": {
            "errors": ["evidence_not_in_current_turn:g2"],
            "current_user_input": user_text,
            "repair_contract": {
                "authority": "current_user_input_only",
                "required_action": "redeclaration",
            },
        },
    }


def _semantic_result() -> dict:
    return {
        "ok": False,
        "code": "GOAL_DECLARATION_INCOMPLETE",
        "message": "semantic coverage rejected",
        "data": {
            "alignment_proof": {
                "verdict": "incomplete",
                "reason_code": "goal_alignment_dependency_graph_mismatch",
                "source": "model",
                "independent": True,
                "evidence_spans": ["查订单", "再查物流"],
                "missing_spans": [],
                "details": {
                    "dependency_authority": "independent_goal_alignment",
                    "dependency_proof_complete": True,
                    "dependency_graph_match": False,
                },
            },
        },
    }


def _mixed_literal_and_verifier_result(user_text: str) -> dict:
    result = _pure_literal_result(user_text)
    result["data"]["alignment_proof"] = {
        "verdict": "indeterminate",
        "reason_code": "adversarial_mixed_failure",
        "source": "model",
        "independent": True,
        "evidence_spans": [],
        "missing_spans": [],
        "details": {},
    }
    return result


def _run_declaration(monkeypatch, script, *, responses, outcomes, user_text: str):
    pending_responses = list(responses)
    pending_outcomes = list(outcomes)
    payloads = []

    def invoke_model(**kwargs):
        payloads.append(kwargs["payload"])
        response = pending_responses.pop(0)
        return response, {"purpose": kwargs["purpose"]}

    def tool_calls(response):
        return [{
            "id": f"call-{response.label}",
            "name": "declare_turn_goals",
            "args": {"goals": response.goals},
        }]

    def validate(**kwargs):
        outcome = pending_outcomes.pop(0)
        if outcome is None:
            return {"goals": kwargs["goals"]}
        raise script._ProductionGoalDeclarationRejected(
            case_id=str(kwargs["case_id"]),
            result=outcome,
        )

    monkeypatch.setattr(script, "invoke_model", invoke_model)
    monkeypatch.setattr(script, "tool_calls", tool_calls)
    monkeypatch.setattr(
        script,
        "attest_real_model_metadata",
        lambda **_kwargs: {"contract": "test-attestation"},
    )
    monkeypatch.setattr(script, "_validate_with_production_goal_contract", validate)

    result = script._declare_with_bounded_production_repair(
        case_id="semantic_multi_orders_logistics",
        user_text=user_text,
        bound=object(),
        system=script.SystemMessage(content="declare goals"),
        identity={"provider": "test"},
    )
    return result, payloads


def test_third_retry_is_available_only_after_two_pure_literal_grounding_failures(monkeypatch) -> None:
    script = _load_script()
    user_text = "查订单，再查物流"
    pure = _pure_literal_result(user_text)

    (goals, declared, evidence, attempts), payloads = _run_declaration(
        monkeypatch,
        script,
        responses=[
            _response("one", "查看物流"),
            _response("two", "查询物流状态"),
            _response("three", "再查物流"),
        ],
        outcomes=[pure, pure, None],
        user_text=user_text,
    )

    assert attempts == 3
    assert len(payloads) == 3
    assert goals[1]["evidence_span"] == "再查物流"
    assert declared["goals"][1]["evidence_span"] == "再查物流"
    assert evidence["trace"]["purpose"].endswith(":attempt3")

    normal_retry = json.loads(payloads[1][-1].content)
    assert normal_retry["code"] == "GOAL_DECLARATION_INVALID"

    literal_retry = json.loads(payloads[2][-1].content)
    assert literal_retry["code"] == "GOAL_DECLARATION_LITERAL_GROUNDING_RETRY"
    assert set(literal_retry["data"]) == {
        "errors",
        "current_user_input",
        "repair_contract",
    }
    assert literal_retry["data"]["current_user_input"] == user_text
    contract = literal_retry["data"]["repair_contract"]
    assert contract["authority"] == "current_user_input_only"
    assert contract["retry_kind"] == "literal_grounding_only"
    assert any("exact contiguous characters" in rule for rule in contract["rules"])
    assert any("Preserve every semantic branch" in rule for rule in contract["rules"])
    assert any("Do not paraphrase" in rule for rule in contract["rules"])
    serialized = json.dumps(literal_retry, ensure_ascii=False)
    assert "requested_effect" not in serialized
    assert "alignment_proof" not in serialized
    assert "granularity_proof" not in serialized
    assert "independent_verifier_feedback" not in serialized


def test_mixed_literal_and_semantic_verifier_failure_does_not_gain_third_retry(monkeypatch) -> None:
    script = _load_script()
    user_text = "查订单，再查物流"
    pure = _pure_literal_result(user_text)
    mixed = _mixed_literal_and_verifier_result(user_text)

    assert script._is_pure_literal_grounding_rejection(pure) is True
    assert script._is_pure_literal_grounding_rejection(mixed) is False

    with pytest.raises(RuntimeError, match="bounded production declaration repair exhausted"):
        _run_declaration(
            monkeypatch,
            script,
            responses=[
                _response("one", "查看物流"),
                _response("two", "查询物流状态"),
            ],
            outcomes=[pure, mixed],
            user_text=user_text,
        )


def test_prior_semantic_verifier_failure_blocks_third_retry_even_if_second_is_literal(monkeypatch) -> None:
    script = _load_script()
    user_text = "查订单，再查物流"
    calls = {"count": 0}
    responses = [
        _response("one", "再查物流"),
        _response("two", "查询物流状态"),
    ]
    outcomes = [_semantic_result(), _pure_literal_result(user_text)]

    def invoke_model(**kwargs):
        calls["count"] += 1
        return responses.pop(0), {"purpose": kwargs["purpose"]}

    monkeypatch.setattr(script, "invoke_model", invoke_model)
    monkeypatch.setattr(
        script,
        "tool_calls",
        lambda response: [{
            "id": f"call-{response.label}",
            "name": "declare_turn_goals",
            "args": {"goals": response.goals},
        }],
    )
    monkeypatch.setattr(
        script,
        "attest_real_model_metadata",
        lambda **_kwargs: {"contract": "test-attestation"},
    )

    def validate(**kwargs):
        outcome = outcomes.pop(0)
        raise script._ProductionGoalDeclarationRejected(
            case_id=str(kwargs["case_id"]),
            result=outcome,
        )

    monkeypatch.setattr(script, "_validate_with_production_goal_contract", validate)

    with pytest.raises(RuntimeError, match="bounded production declaration repair exhausted"):
        script._declare_with_bounded_production_repair(
            case_id="semantic_multi_orders_logistics",
            user_text=user_text,
            bound=object(),
            system=script.SystemMessage(content="declare goals"),
            identity={"provider": "test"},
        )

    assert calls["count"] == 2


def test_third_literal_retry_still_fails_closed_when_evidence_remains_non_literal(monkeypatch) -> None:
    script = _load_script()
    user_text = "查订单，再查物流"
    pure = _pure_literal_result(user_text)
    calls = {"count": 0}
    responses = [
        _response("one", "查看物流"),
        _response("two", "查询物流状态"),
        _response("three", "帮我看看物流进度"),
    ]
    outcomes = [pure, pure, pure]

    def invoke_model(**kwargs):
        calls["count"] += 1
        return responses.pop(0), {"purpose": kwargs["purpose"]}

    monkeypatch.setattr(script, "invoke_model", invoke_model)
    monkeypatch.setattr(
        script,
        "tool_calls",
        lambda response: [{
            "id": f"call-{response.label}",
            "name": "declare_turn_goals",
            "args": {"goals": response.goals},
        }],
    )
    monkeypatch.setattr(
        script,
        "attest_real_model_metadata",
        lambda **_kwargs: {"contract": "test-attestation"},
    )

    def validate(**kwargs):
        outcome = outcomes.pop(0)
        raise script._ProductionGoalDeclarationRejected(
            case_id=str(kwargs["case_id"]),
            result=outcome,
        )

    monkeypatch.setattr(script, "_validate_with_production_goal_contract", validate)

    with pytest.raises(RuntimeError, match="evidence_not_in_current_turn:g2"):
        script._declare_with_bounded_production_repair(
            case_id="semantic_multi_orders_logistics",
            user_text=user_text,
            bound=object(),
            system=script.SystemMessage(content="declare goals"),
            identity={"provider": "test"},
        )

    assert calls["count"] == 3


def _canonical_goal(*, output_id: str, legacy_domain: str = "order", legacy_operation: str = "query_logistics") -> dict:
    return {
        "goal_id": "g1",
        "evidence_span": "查下物流到哪了",
        "required": True,
        "depends_on": [],
        "requested_effect": {
            "domain": legacy_domain,
            "operation": legacy_operation,
            "object_type": "order",
            "requested_outputs": [{
                "output_id": output_id,
                "evidence_span": "查下物流到哪了",
            }],
        },
    }


def _canonical_oracle() -> list[dict]:
    return [{
        "oracle_id": "g1",
        "evidence_span": "查下物流到哪了",
        "required": True,
        "depends_on": [],
        "accepted_output_sets": [["shipment.current_status"]],
    }]


def test_wp08_production_gate_requires_canonical_output_identity(monkeypatch) -> None:
    script = _load_script()
    captured = {}

    monkeypatch.setattr(
        script,
        "get_runtime_registry",
        lambda: SimpleNamespace(capabilities=()),
    )

    def validate_goal_declaration(**kwargs):
        captured.update(kwargs)
        return {"ok": True}, {"goals": kwargs["args"]["goals"]}

    monkeypatch.setattr(script, "validate_goal_declaration", validate_goal_declaration)
    result, declared = script._production_goal_declaration_evaluation(
        user_text="查下物流到哪了",
        goals=[_canonical_goal(output_id="shipment.current_status")],
    )

    assert result["ok"] is True
    assert declared is not None
    assert captured["require_canonical_output_identity"] is True


def test_canonical_oracle_ignores_non_authoritative_legacy_triplet(monkeypatch) -> None:
    script = _load_script()
    monkeypatch.setattr(script, "_semantic_output_ids", lambda: ("shipment.current_status",))
    goal = _canonical_goal(
        output_id="shipment.current_status",
        legacy_domain="deliberately-wrong-legacy-domain",
        legacy_operation="deliberately-wrong-legacy-operation",
    )

    script._match_oracle(
        case_id="adversarial-canonical-pass",
        oracle=_canonical_oracle(),
        goals=[goal],
    )


def test_matching_legacy_triplet_cannot_rescue_wrong_canonical_output(monkeypatch) -> None:
    script = _load_script()
    monkeypatch.setattr(
        script,
        "_semantic_output_ids",
        lambda: ("shipment.current_status", "order.collection"),
    )
    goal = _canonical_goal(
        output_id="order.collection",
        legacy_domain="order",
        legacy_operation="query_logistics",
    )

    with pytest.raises(RuntimeError, match="no unique model goal matches oracle"):
        script._match_oracle(
            case_id="adversarial-wrong-canonical",
            oracle=_canonical_oracle(),
            goals=[goal],
        )


def test_open_output_cannot_satisfy_registered_canonical_oracle(monkeypatch) -> None:
    script = _load_script()
    monkeypatch.setattr(script, "_semantic_output_ids", lambda: ("shipment.current_status",))
    goal = _canonical_goal(output_id="open")
    goal["requested_effect"]["requested_outputs"][0]["open_description"] = "查询未知物流语义"

    with pytest.raises(RuntimeError, match="no unique model goal matches oracle"):
        script._match_oracle(
            case_id="adversarial-open-nearest-match",
            oracle=_canonical_oracle(),
            goals=[goal],
        )


def test_canonical_planning_vocabulary_exposes_no_availability_or_tool_names(monkeypatch) -> None:
    script = _load_script()
    snapshot = {
        "version": "semantic-output-vocabulary@1",
        "authority": "domain_semantics_only_capability_independent",
        "availability_exposed": False,
        "tool_names_exposed": False,
        "outputs": [{"output_id": "shipment.current_status"}],
    }
    monkeypatch.setattr(
        script,
        "get_module_registry",
        lambda: SimpleNamespace(semantic_vocabulary_snapshot=lambda: snapshot),
    )

    projected = script._semantic_vocabulary_snapshot()
    assert projected == snapshot
    assert projected["availability_exposed"] is False
    assert projected["tool_names_exposed"] is False
    serialized_outputs = json.dumps(projected["outputs"], ensure_ascii=False)
    assert "tool_name" not in serialized_outputs
    assert "availability" not in serialized_outputs
