from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import inspect
import json

from agent_core.goal_graph.activation_preflight import (
    DEPENDENCY_ACTIVATION_REQUEST_AUTHORITY,
    DEPENDENCY_ACTIVATION_REQUEST_VERSION,
    evaluate_dependency_activation_preflight,
)
from agent_core.goal_graph.cutover_gate import (
    DEPENDENCY_CUTOVER_GRANT_AUTHORITY,
    DEPENDENCY_CUTOVER_GRANT_VERSION,
    LEGACY_DEPENDENCY_AUTHORITY,
    TYPED_DEPENDENCY_AUTHORITY,
    build_dependency_authority_rollback_contract,
    evaluate_dependency_cutover_gate,
)
from agent_core.goal_graph.dependency_authority import build_dependency_authority_attestation
from agent_core.goal_graph.handoff_simulation import build_dependency_authority_handoff_simulation
from agent_core.goal_graph.runtime_authority import (
    DEPENDENCY_RUNTIME_ACTIVATION_AUTHORITY,
    DEPENDENCY_RUNTIME_ACTIVATION_VERSION,
    dependency_runtime_activation_integrity,
    dependency_runtime_selection_integrity,
    select_runtime_dependency_authority,
    selected_dependency_goal_ids,
)


_IDENTITY_FIELDS = (
    "semantic_contract_id",
    "semantic_digest",
    "typed_graph_id",
    "typed_graph_digest",
    "typed_coverage_digest",
    "capability_registry_version",
    "completion_snapshot_digest",
)


def _digest(value: dict) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _identity(attestation: dict) -> dict:
    return {field: attestation[field] for field in _IDENTITY_FIELDS}


def _synthetic_attestation() -> dict:
    shadow = {
        "version": "typed-dependency-authority-shadow@1",
        "authority": "audit_only_current_dependency_enforcement_unchanged",
        "status": "MATCHED",
        "current_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "candidate_dependency_authority": TYPED_DEPENDENCY_AUTHORITY,
        "typed_coverage_status": "COMPLETE",
        "typed_dataflow_status": "GOAL_GRAPH_DATAFLOW_CLOSED",
        "typed_coverage_digest": "c" * 64,
        "typed_graph_id": "goal-graph:1:stage4e",
        "typed_graph_digest": "g" * 64,
        "evidence_errors": [],
        "comparisons": [],
        "divergence_codes": [],
        "cutover_eligible": True,
        "cutover_performed": False,
        "changes_current_dependency_blocking": False,
        "changes_allowed_capability_tools": False,
        "blocks_execution": False,
        "creates_permit": False,
        "mutates_semantics": False,
        "mutates_business_state": False,
    }
    shadow["shadow_digest"] = _digest(shadow)
    return build_dependency_authority_attestation(
        dependency_shadow=shadow,
        semantic_contract_id="semantic:1:stage4e",
        semantic_digest="s" * 64,
        capability_registry_version="registry@stage4e",
        completed_goal_ids=(),
    )


def _ready_preflight(attestation: dict) -> dict:
    identity = _identity(attestation)
    grant = {
        "version": DEPENDENCY_CUTOVER_GRANT_VERSION,
        "authority": DEPENDENCY_CUTOVER_GRANT_AUTHORITY,
        "immutable": True,
        "status": "GRANTED",
        "external_authority_verified": True,
        "grant_id": "grant:stage4e:test",
        "issued_by": "governance:test",
        "attestation_digest": attestation["attestation_digest"],
        **identity,
        "expires_at": 2000.0,
    }
    grant["grant_digest"] = _digest(grant)
    gate = evaluate_dependency_cutover_gate(
        attestation=attestation,
        grant=grant,
        current_identity=identity,
        evaluation_time=1000.0,
    )
    rollback = build_dependency_authority_rollback_contract(
        gate=gate,
        rollback_requested=True,
        reason_code="STAGE4E_RUNTIME_REVERSION_DRILL",
    )
    handoff = build_dependency_authority_handoff_simulation(
        gate=gate,
        rollback=rollback,
        exercise_rollback=True,
    )
    request = {
        "version": DEPENDENCY_ACTIVATION_REQUEST_VERSION,
        "authority": DEPENDENCY_ACTIVATION_REQUEST_AUTHORITY,
        "immutable": True,
        "status": "REQUESTED",
        "external_authority_verified": True,
        "request_id": "activation-request:stage4e:test",
        "issued_by": "governance:test",
        "desired_dependency_authority": TYPED_DEPENDENCY_AUTHORITY,
        "expected_current_runtime_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "attestation_digest": attestation["attestation_digest"],
        "gate_digest": gate["gate_digest"],
        "handoff_simulation_digest": handoff["simulation_digest"],
        "rollback_digest": rollback["rollback_digest"],
        **identity,
        "expires_at": 2000.0,
    }
    request["request_digest"] = _digest(request)
    preflight = evaluate_dependency_activation_preflight(
        gate=gate,
        handoff_simulation=handoff,
        rollback=rollback,
        activation_request=request,
        current_identity=identity,
        evaluation_time=1000.0,
    )
    assert preflight["status"] == "ACTIVATION_PREFLIGHT_READY"
    return preflight


def _runtime_activation(
    *,
    attestation: dict,
    preflight: dict,
    expires_at: float = 2000.0,
) -> dict:
    activation = {
        "version": DEPENDENCY_RUNTIME_ACTIVATION_VERSION,
        "authority": DEPENDENCY_RUNTIME_ACTIVATION_AUTHORITY,
        "immutable": True,
        "status": "ACTIVATED",
        "external_authority_verified": True,
        "activation_id": "runtime-activation:stage4e:test",
        "issued_by": "governance:test",
        "preflight_digest": preflight["preflight_digest"],
        "desired_dependency_authority": TYPED_DEPENDENCY_AUTHORITY,
        "expected_current_runtime_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        **_identity(attestation),
        "expires_at": expires_at,
    }
    activation["activation_digest"] = _digest(activation)
    return activation


def _active_selection() -> tuple[dict, dict, dict]:
    attestation = _synthetic_attestation()
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(attestation=attestation, preflight=preflight)
    selection = select_runtime_dependency_authority(
        preflight=preflight,
        activation=activation,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )
    return attestation, activation, selection


def test_default_selector_keeps_legacy_authority_without_activation() -> None:
    selection = select_runtime_dependency_authority(
        preflight=None,
        activation=None,
        current_identity=None,
        evaluation_time=None,
    )

    assert selection["status"] == "LEGACY_DEFAULT"
    assert selection["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert selection["selected_authority_count"] == 1
    assert selection["single_authority_invariant"] is True
    assert selection["activation_performed"] is False
    assert selection["creates_permit"] is False
    assert selection["dispatches_tools"] is False
    assert dependency_runtime_selection_integrity(selection)["ok"] is True


def test_exact_preflight_and_runtime_activation_select_typed_authority() -> None:
    _attestation, _activation, selection = _active_selection()

    assert selection["status"] == "TYPED_AUTHORITY_ACTIVE"
    assert selection["selected_runtime_dependency_authority"] == TYPED_DEPENDENCY_AUTHORITY
    assert selection["selected_authority_count"] == 1
    assert selection["runtime_activation_authority_granted"] is True
    assert selection["activation_performed"] is True
    assert selection["cutover_performed"] is True
    assert selection["creates_permit"] is False
    assert selection["dispatches_tools"] is False
    assert dependency_runtime_selection_integrity(selection)["ok"] is True


def test_selected_dependency_set_never_unions_legacy_and_typed() -> None:
    _attestation, _activation, selection = _active_selection()

    selected = selected_dependency_goal_ids(
        selection=selection,
        legacy_dependency_goal_ids=["legacy-only"],
        typed_dependency_goal_ids=["typed-only"],
    )

    assert selected == ["typed-only"]
    assert "legacy-only" not in selected


def test_typed_selection_can_block_when_legacy_set_is_empty() -> None:
    _attestation, _activation, selection = _active_selection()
    dependencies = set(selected_dependency_goal_ids(
        selection=selection,
        legacy_dependency_goal_ids=[],
        typed_dependency_goal_ids=["details"],
    ))

    assert dependencies - set() == {"details"}


def test_typed_selection_can_release_when_legacy_only_dependency_exists() -> None:
    _attestation, _activation, selection = _active_selection()
    dependencies = set(selected_dependency_goal_ids(
        selection=selection,
        legacy_dependency_goal_ids=["refund"],
        typed_dependency_goal_ids=[],
    ))

    assert dependencies == set()


def test_tampered_runtime_activation_fails_closed_to_legacy() -> None:
    attestation = _synthetic_attestation()
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(attestation=attestation, preflight=preflight)
    activation["activation_digest"] = "0" * 64

    selection = select_runtime_dependency_authority(
        preflight=preflight,
        activation=activation,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )

    assert selection["status"] == "LEGACY_FAIL_CLOSED"
    assert selection["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert any(code.startswith("ACTIVATION:") for code in selection["errors"])


def test_expired_runtime_activation_fails_closed_to_legacy() -> None:
    attestation = _synthetic_attestation()
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(
        attestation=attestation,
        preflight=preflight,
        expires_at=900.0,
    )

    selection = select_runtime_dependency_authority(
        preflight=preflight,
        activation=activation,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )

    assert selection["status"] == "LEGACY_FAIL_CLOSED"
    assert "RUNTIME_ACTIVATION_EXPIRED" in selection["errors"]


def test_missing_evaluation_time_fails_closed() -> None:
    attestation = _synthetic_attestation()
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(attestation=attestation, preflight=preflight)

    selection = select_runtime_dependency_authority(
        preflight=preflight,
        activation=activation,
        current_identity=_identity(attestation),
        evaluation_time=None,
    )

    assert selection["status"] == "LEGACY_FAIL_CLOSED"
    assert "RUNTIME_AUTHORITY_EVALUATION_TIME_REQUIRED" in selection["errors"]


def test_preflight_digest_mismatch_fails_closed() -> None:
    attestation = _synthetic_attestation()
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(attestation=attestation, preflight=preflight)
    activation["preflight_digest"] = "0" * 64
    activation["activation_digest"] = _digest(
        {key: value for key, value in activation.items() if key != "activation_digest"}
    )

    selection = select_runtime_dependency_authority(
        preflight=preflight,
        activation=activation,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )

    assert selection["status"] == "LEGACY_FAIL_CLOSED"
    assert "RUNTIME_ACTIVATION_PREFLIGHT_DIGEST_MISMATCH" in selection["errors"]


def test_identity_drift_fails_closed() -> None:
    attestation = _synthetic_attestation()
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(attestation=attestation, preflight=preflight)
    current = _identity(attestation)
    current["typed_graph_digest"] = "0" * 64

    selection = select_runtime_dependency_authority(
        preflight=preflight,
        activation=activation,
        current_identity=current,
        evaluation_time=1000.0,
    )

    assert selection["status"] == "LEGACY_FAIL_CLOSED"
    assert "CURRENT_TYPED_GRAPH_DIGEST_MISMATCH" in selection["errors"]


def test_explicit_rollback_reselects_legacy_without_dual_authority() -> None:
    attestation = _synthetic_attestation()
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(attestation=attestation, preflight=preflight)

    selection = select_runtime_dependency_authority(
        preflight=preflight,
        activation=activation,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
        rollback_requested=True,
    )

    assert selection["status"] == "ROLLED_BACK_TO_LEGACY"
    assert selection["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert selection["selected_authority_count"] == 1
    assert selection["runtime_reversion_performed"] is True
    assert selection["activation_performed"] is False
    assert dependency_runtime_selection_integrity(selection)["ok"] is True


def test_selection_integrity_rejects_recomputed_dual_or_permit_state() -> None:
    _attestation, _activation, selection = _active_selection()
    tampered = deepcopy(selection)
    tampered["selected_authority_count"] = 2
    tampered["creates_permit"] = True
    tampered["selection_digest"] = _digest(
        {key: value for key, value in tampered.items() if key != "selection_digest"}
    )

    integrity = dependency_runtime_selection_integrity(tampered)

    assert integrity["ok"] is False
    assert "RUNTIME_SELECTION_EXACTLY_ONE_REQUIRED" in integrity["errors"]
    assert "RUNTIME_SELECTION_CREATES_PERMIT_MUST_BE_FALSE" in integrity["errors"]


def test_runtime_activation_record_integrity_rejects_legacy_desired_authority() -> None:
    attestation = _synthetic_attestation()
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(attestation=attestation, preflight=preflight)
    activation["desired_dependency_authority"] = LEGACY_DEPENDENCY_AUTHORITY
    activation["activation_digest"] = _digest(
        {key: value for key, value in activation.items() if key != "activation_digest"}
    )

    integrity = dependency_runtime_activation_integrity(activation)

    assert integrity["ok"] is False
    assert "RUNTIME_ACTIVATION_DESIRED_AUTHORITY_INVALID" in integrity["errors"]


def test_pretool_policy_default_remains_legacy_and_explicit_activation_is_observable() -> None:
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy
    from tests.runtime.test_pretool_execution_policy import _contract, _goal, _registry

    contract = _contract([_goal("details", domain="order", operation="query_details")])
    state = {
        "frozen_semantic_contract": contract,
        "current_tenant_id": "tenant-1",
        "current_user_id": "u001",
        "current_thread_id": "web-u001-stage4e",
        "goal_records": [],
    }
    registry = _registry()
    baseline = build_pretool_execution_policy(state=state, capability_registry=registry)
    assert baseline["selected_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert baseline["dependency_runtime_authority_selection"]["status"] == "LEGACY_DEFAULT"

    attestation = baseline["typed_dependency_authority_attestation"]
    preflight = _ready_preflight(attestation)
    activation = _runtime_activation(attestation=attestation, preflight=preflight)
    activated = build_pretool_execution_policy(
        state=state,
        capability_registry=registry,
        dependency_activation_preflight=preflight,
        dependency_runtime_activation=activation,
        dependency_authority_evaluation_time=1000.0,
    )

    assert activated["selected_dependency_authority"] == TYPED_DEPENDENCY_AUTHORITY
    assert activated["dependency_runtime_authority_selection"]["status"] == "TYPED_AUTHORITY_ACTIVE"
    assert activated["allowed_capability_tools"] == baseline["allowed_capability_tools"]
    assert activated["creates_permit"] is False
    assert activated["dispatches_tools"] is False


def test_stage4e_selector_module_has_no_dispatch_or_business_authority() -> None:
    import agent_core.goal_graph.runtime_authority as module

    source = inspect.getsource(module)
    assert "agent_core.lifecycle" not in source
    assert "agent_modules" not in source
    assert "BusinessService" not in source
    assert "ExecutionPermit" in source  # documentation boundary only
    assert "dispatch(" not in source
