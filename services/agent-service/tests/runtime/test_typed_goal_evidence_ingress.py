from __future__ import annotations


from agent_core.runtime.typed_goal_evidence_ingress import (
    TRUSTED_TYPED_GOAL_EVIDENCE_RESOLVER_KEY,
    resolve_trusted_typed_goal_evidence,
    disabled_typed_goal_evidence_resolver,
)


def _trusted_payload():
    return {
        "available_input_evidence": [],
        "evaluation_time": 1000.0,
        "input_issuer_validator": lambda _row: True,
        "target_issuer_validator": lambda _row: True,
    }


def test_missing_or_state_supplied_resolver_is_fail_closed() -> None:
    inputs, metadata = resolve_trusted_typed_goal_evidence({})
    assert inputs == {}
    assert metadata["status"] == "DISABLED"
    assert metadata["raw_evidence_exposed"] is False

    inputs, metadata = resolve_trusted_typed_goal_evidence(
        {TRUSTED_TYPED_GOAL_EVIDENCE_RESOLVER_KEY: {"evaluation_time": 1000.0}}
    )
    assert inputs == {}
    assert metadata["status"] == "UNTRUSTED_STATE_VALUE_IGNORED"


def test_application_resolver_supplies_only_validated_trust_roots() -> None:
    input_validator = lambda _row: True
    target_validator = lambda _row: True
    evidence = {"proof_ref": "proof:input-1"}
    inputs, metadata = resolve_trusted_typed_goal_evidence(
        {
            TRUSTED_TYPED_GOAL_EVIDENCE_RESOLVER_KEY: lambda: {
                "available_input_evidence": [evidence],
                "evaluation_time": 1000,
                "input_issuer_validator": input_validator,
                "target_issuer_validator": target_validator,
            }
        }
    )
    assert inputs["available_input_evidence"] == (evidence,)
    assert inputs["evaluation_time"] == 1000.0
    assert inputs["input_issuer_validator"] is input_validator
    assert inputs["target_issuer_validator"] is target_validator
    assert metadata["status"] == "RESOLVED"
    assert metadata["evidence_count"] == 1
    assert metadata["raw_evidence_exposed"] is False


def test_incomplete_application_trust_root_cannot_make_shadow_evidence_ready() -> None:
    inputs, metadata = resolve_trusted_typed_goal_evidence(
        {
            TRUSTED_TYPED_GOAL_EVIDENCE_RESOLVER_KEY: lambda: {
                **_trusted_payload(),
                "evaluation_time": "not-a-time",
            }
        }
    )
    assert inputs == {}
    assert metadata["status"] == "INCOMPLETE_TRUST_ROOT_FAIL_CLOSED"


def test_default_composition_is_explicit_empty_evidence_and_rejects_issuers() -> None:
    raw = disabled_typed_goal_evidence_resolver()
    assert raw["available_input_evidence"] == []
    assert raw["evaluation_time"] > 0
    assert raw["input_issuer_validator"]({}) is False
    assert raw["target_issuer_validator"]({}) is False


def test_resolver_exception_is_observable_but_never_exposes_raw_inputs() -> None:
    def failing_resolver():
        raise RuntimeError("provider unavailable")

    inputs, metadata = resolve_trusted_typed_goal_evidence(
        {TRUSTED_TYPED_GOAL_EVIDENCE_RESOLVER_KEY: failing_resolver}
    )
    assert inputs == {}
    assert metadata["status"] == "RESOLVER_ERROR_FAIL_CLOSED"
    assert metadata["error_type"] == "RuntimeError"
    assert metadata["raw_evidence_exposed"] is False


def test_agent_service_composes_the_explicit_fail_closed_provider() -> None:
    from app.services.agent_service import AgentService

    source = AgentService._compose_runtime_deps.__doc__ or ""
    assert "Compose dependency authority" in source
    import inspect

    assert "typed_goal_evidence_resolver=" in inspect.getsource(
        AgentService._compose_runtime_deps
    )


def test_lifecycle_node_admits_resolver_only_from_runtime_dependencies(monkeypatch) -> None:
    from agent_core.lifecycle import nodes

    captured = []

    def fake_dialogue(state, **_kwargs):
        captured.append(state)
        return {"status": "captured"}

    monkeypatch.setattr(nodes, "_dialogue_agent_loop_node", fake_dialogue)
    attacker = lambda: _trusted_payload()
    trusted = lambda: _trusted_payload()

    nodes.agent_loop_node(
        {TRUSTED_TYPED_GOAL_EVIDENCE_RESOLVER_KEY: attacker},
        context_bundle_builder=object(),
        capability_registry=object(),
        model_resolver=lambda: object(),
        typed_goal_evidence_resolver=trusted,
    )
    assert captured[-1][TRUSTED_TYPED_GOAL_EVIDENCE_RESOLVER_KEY] is trusted


def test_shadow_planner_forwards_ingress_to_typed_coverage(monkeypatch) -> None:
    from tests.runtime.test_pretool_shadow_planner import _contract, _goal, _registry
    import agent_core.lifecycle.pretool_planner as planner

    observed = {}

    def fake_coverage(**kwargs):
        observed.update(kwargs)
        return {"coverage_status": "COMPLETE", "coverage_digest": "test"}

    monkeypatch.setattr(planner, "build_goal_capability_coverage", fake_coverage)
    input_validator = lambda _row: True
    target_validator = lambda _row: True
    planner.build_pretool_shadow_plan(
        state={
            "frozen_semantic_contract": _contract(
                [_goal("logistics", domain="order", operation="query_logistics")]
            )
        },
        capability_registry=_registry(),
        available_input_evidence=({"proof_ref": "proof:input-1"},),
        evaluation_time=1000.0,
        input_issuer_validator=input_validator,
        target_issuer_validator=target_validator,
    )
    assert observed["available_input_evidence"] == ({"proof_ref": "proof:input-1"},)
    assert observed["evaluation_time"] == 1000.0
    assert observed["input_issuer_validator"] is input_validator
    assert observed["target_issuer_validator"] is target_validator
