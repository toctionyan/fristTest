from __future__ import annotations

import inspect

from agent_core.goal_graph.cutover_gate import (
    LEGACY_DEPENDENCY_AUTHORITY,
    TYPED_DEPENDENCY_AUTHORITY,
)
from agent_core.lifecycle.pretool_execution_policy import (
    TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY,
    build_pretool_execution_policy,
)
from agent_core.runtime.deps import lifecycle_runtime_deps
from app.services.agent_service import AgentService
from tests.runtime.test_pretool_execution_policy import _contract, _goal, _registry
from tests.runtime.test_typed_goal_dependency_runtime_authority import (
    _ready_preflight,
    _runtime_activation,
)


def _policy_fixture():
    contract = _contract([_goal("details", domain="order", operation="query_details")])
    state = {
        "frozen_semantic_contract": contract,
        "current_tenant_id": "tenant-1",
        "current_user_id": "u001",
        "current_thread_id": "web-u001-stage4f",
        "goal_records": [],
    }
    registry = _registry()
    baseline = build_pretool_execution_policy(
        state=state,
        capability_registry=registry,
    )
    return state, registry, baseline


def test_default_runtime_composition_has_no_activation_control_resolver() -> None:
    parameter = inspect.signature(lifecycle_runtime_deps).parameters[
        "dependency_authority_control_resolver"
    ]
    assert parameter.default is None
    assert "dependency_authority_control_resolver=" not in inspect.getsource(
        AgentService.__init__
    )


def test_untrusted_checkpoint_value_cannot_activate_dependency_authority() -> None:
    state, registry, _baseline = _policy_fixture()
    state[TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY] = {
        "runtime_activation": {"status": "ACTIVATED"},
        "evaluation_time": 1000.0,
    }

    policy = build_pretool_execution_policy(
        state=state,
        capability_registry=registry,
    )

    assert policy["selected_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert policy["dependency_runtime_authority_selection"]["status"] == "LEGACY_DEFAULT"
    assert policy["dependency_authority_ingress"]["status"] == "UNTRUSTED_STATE_VALUE_IGNORED"
    assert policy["dependency_authority_ingress"]["raw_control_exposed"] is False


def test_trusted_runtime_resolver_can_feed_exact_stage4e_selector_without_widening_tools() -> None:
    state, registry, baseline = _policy_fixture()
    attestation = baseline["typed_dependency_authority_attestation"]
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(attestation=attestation, preflight=preflight)

    def resolve_control():
        return {
            "activation_preflight": preflight,
            "runtime_activation": activation,
            "evaluation_time": 1000.0,
            "rollback_requested": False,
        }

    activated = build_pretool_execution_policy(
        state={
            **state,
            TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY: resolve_control,
        },
        capability_registry=registry,
    )

    assert activated["selected_dependency_authority"] == TYPED_DEPENDENCY_AUTHORITY
    assert activated["dependency_runtime_authority_selection"]["status"] == "TYPED_AUTHORITY_ACTIVE"
    assert activated["allowed_capability_tools"] == baseline["allowed_capability_tools"]
    assert activated["creates_permit"] is False
    assert activated["dispatches_tools"] is False
    assert activated["dependency_authority_ingress"] == {
        "status": "RESOLVED",
        "source": "application_runtime_deps",
        "has_activation_preflight": True,
        "has_runtime_activation": True,
        "has_evaluation_time": True,
        "rollback_requested": False,
        "raw_control_exposed": False,
    }


def test_trusted_resolver_error_fails_closed_without_exposing_exception_text() -> None:
    state, registry, _baseline = _policy_fixture()

    def broken_control():
        raise RuntimeError("do-not-project-this-secret")

    policy = build_pretool_execution_policy(
        state={
            **state,
            TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY: broken_control,
        },
        capability_registry=registry,
    )

    assert policy["selected_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    ingress = policy["dependency_authority_ingress"]
    assert ingress["status"] == "RESOLVER_ERROR_FAIL_CLOSED"
    assert ingress["error_type"] == "RuntimeError"
    assert "do-not-project-this-secret" not in str(ingress)


def test_trusted_resolver_rollback_reselects_legacy_single_authority() -> None:
    state, registry, baseline = _policy_fixture()
    attestation = baseline["typed_dependency_authority_attestation"]
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(attestation=attestation, preflight=preflight)

    policy = build_pretool_execution_policy(
        state={
            **state,
            TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY: lambda: {
                "activation_preflight": preflight,
                "runtime_activation": activation,
                "evaluation_time": 1000.0,
                "rollback_requested": True,
            },
        },
        capability_registry=registry,
    )

    selection = policy["dependency_runtime_authority_selection"]
    assert policy["selected_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert selection["status"] == "ROLLED_BACK_TO_LEGACY"
    assert selection["selected_authority_count"] == 1
    assert selection["runtime_reversion_performed"] is True


def test_agent_loop_wrapper_strips_state_injected_resolver_and_only_accepts_runtime_dep(monkeypatch) -> None:
    import agent_core.lifecycle.nodes as nodes

    captured = []

    def fake_dialogue(state, **_kwargs):
        captured.append(state)
        return {"status": "captured"}

    monkeypatch.setattr(nodes, "_dialogue_agent_loop_node", fake_dialogue)
    attacker = lambda: {"runtime_activation": {"status": "ACTIVATED"}}

    nodes.agent_loop_node(
        {TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY: attacker},
        context_bundle_builder=object(),
        capability_registry=object(),
        model_resolver=lambda: object(),
    )
    assert TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY not in captured[-1]

    trusted = lambda: None
    nodes.agent_loop_node(
        {TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY: attacker},
        context_bundle_builder=object(),
        capability_registry=object(),
        model_resolver=lambda: object(),
        dependency_authority_control_resolver=trusted,
    )
    assert captured[-1][TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY] is trusted
