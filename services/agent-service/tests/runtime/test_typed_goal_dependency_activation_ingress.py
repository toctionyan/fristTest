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


def _verified_control(*, preflight, activation, control_epoch=7):
    return {
        "activation_preflight": preflight,
        "runtime_activation": activation,
        "evaluation_time": 1000.0,
        "rollback_requested": False,
        "control_head_identity": {
            "control_epoch": control_epoch,
            "revision": f"control-rev-{control_epoch:04d}",
            "snapshot_digest": ("a" if control_epoch == 7 else "b") * 64,
        },
        "rollback_head_identity": None,
    }


def test_stage4i_runtime_factory_default_stays_none_but_agent_service_wires_disabled_resolver() -> None:
    parameter = inspect.signature(lifecycle_runtime_deps).parameters[
        "dependency_authority_control_resolver"
    ]
    assert parameter.default is None
    assert "self._compose_runtime_deps()" in inspect.getsource(AgentService.__init__)
    assert "dependency_authority_control_resolver=" in inspect.getsource(
        AgentService._compose_runtime_deps
    )


def test_stage4i_wired_disabled_resolver_keeps_legacy_authority_and_tool_surface() -> None:
    state, registry, baseline = _policy_fixture()
    policy = build_pretool_execution_policy(
        state={
            **state,
            TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY: lambda: None,
        },
        capability_registry=registry,
    )
    assert policy["selected_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert policy["dependency_runtime_authority_selection"]["status"] == "LEGACY_DEFAULT"
    assert policy["dependency_runtime_authority_selection"]["selected_authority_count"] == 1
    assert policy["dependency_authority_ingress"]["status"] == "NO_CONTROL_FAIL_CLOSED"
    assert policy["allowed_capability_tools"] == baseline["allowed_capability_tools"]
    assert policy["creates_permit"] is False
    assert policy["dispatches_tools"] is False


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
        return _verified_control(preflight=preflight, activation=activation)

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
    ingress = activated["dependency_authority_ingress"]
    assert ingress["status"] == "RESOLVED"
    assert ingress["source"] == "application_runtime_deps"
    assert ingress["has_activation_preflight"] is True
    assert ingress["has_runtime_activation"] is True
    assert ingress["has_evaluation_time"] is True
    assert ingress["rollback_requested"] is False
    assert ingress["control_head_identity"] == {
        "control_epoch": 7,
        "revision": "control-rev-0007",
        "snapshot_digest": "a" * 64,
    }
    assert ingress["control_head_identity_status"] == "RESOLVED_TRUSTED"
    assert len(ingress["control_head_identity_digest"]) == 64
    assert ingress["rollback_head_identity"] is None
    assert ingress["rollback_head_identity_status"] == "UNAVAILABLE"
    assert ingress["rollback_head_identity_digest"] is None
    assert len(ingress["cross_worker_control_plane_head_digest"]) == 64
    assert ingress["raw_control_exposed"] is False


def test_runtime_head_digest_is_stable_across_workers_and_changes_with_epoch_only_as_observability() -> None:
    state, registry, baseline = _policy_fixture()
    attestation = baseline["typed_dependency_authority_attestation"]
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(attestation=attestation, preflight=preflight)

    def policy_for_epoch(epoch):
        return build_pretool_execution_policy(
            state={
                **state,
                TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY: lambda: _verified_control(
                    preflight=preflight,
                    activation=activation,
                    control_epoch=epoch,
                ),
            },
            capability_registry=registry,
        )

    worker_a = policy_for_epoch(7)
    worker_b = policy_for_epoch(7)
    advanced = policy_for_epoch(8)

    digest_a = worker_a["dependency_authority_ingress"][
        "cross_worker_control_plane_head_digest"
    ]
    digest_b = worker_b["dependency_authority_ingress"][
        "cross_worker_control_plane_head_digest"
    ]
    digest_advanced = advanced["dependency_authority_ingress"][
        "cross_worker_control_plane_head_digest"
    ]
    assert digest_a == digest_b
    assert digest_advanced != digest_a
    assert worker_a["selected_dependency_authority"] == TYPED_DEPENDENCY_AUTHORITY
    assert advanced["selected_dependency_authority"] == TYPED_DEPENDENCY_AUTHORITY
    assert worker_a["allowed_capability_tools"] == advanced["allowed_capability_tools"]


def test_malformed_head_observability_is_ignored_without_changing_authority_selection() -> None:
    state, registry, baseline = _policy_fixture()
    attestation = baseline["typed_dependency_authority_attestation"]
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(attestation=attestation, preflight=preflight)

    control = _verified_control(preflight=preflight, activation=activation)
    control["control_head_identity"] = {
        "control_epoch": 7,
        "revision": "control-rev-0007",
        "snapshot_digest": "a" * 64,
        "signature": "must-not-escape",
    }
    policy = build_pretool_execution_policy(
        state={
            **state,
            TRUSTED_DEPENDENCY_AUTHORITY_CONTROL_RESOLVER_KEY: lambda: control,
        },
        capability_registry=registry,
    )

    assert policy["selected_dependency_authority"] == TYPED_DEPENDENCY_AUTHORITY
    assert policy["allowed_capability_tools"] == baseline["allowed_capability_tools"]
    ingress = policy["dependency_authority_ingress"]
    assert ingress["control_head_identity"] is None
    assert ingress["control_head_identity_status"] == "INVALID_IGNORED"
    assert ingress["control_head_identity_digest"] is None
    assert ingress["cross_worker_control_plane_head_digest"] is None
    assert "must-not-escape" not in str(ingress)


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
                "control_head_identity": {
                    "control_epoch": 7,
                    "revision": "control-rev-0007",
                    "snapshot_digest": "a" * 64,
                },
                "rollback_head_identity": {
                    "rollback_epoch": 2,
                    "revision": "rollback-rev-0002",
                    "snapshot_digest": "c" * 64,
                },
            },
        },
        capability_registry=registry,
    )

    selection = policy["dependency_runtime_authority_selection"]
    assert policy["selected_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert selection["status"] == "ROLLED_BACK_TO_LEGACY"
    assert selection["selected_authority_count"] == 1
    assert selection["runtime_reversion_performed"] is True
    ingress = policy["dependency_authority_ingress"]
    assert ingress["rollback_head_identity"]["rollback_epoch"] == 2
    assert ingress["rollback_head_identity_status"] == "RESOLVED_TRUSTED"
    assert len(ingress["cross_worker_control_plane_head_digest"]) == 64


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
