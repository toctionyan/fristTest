from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import inspect
import json

from agent_core.goal_graph.activation_preflight import (
    DEPENDENCY_ACTIVATION_REQUEST_AUTHORITY,
    DEPENDENCY_ACTIVATION_REQUEST_VERSION,
    dependency_activation_preflight_integrity,
    dependency_activation_request_integrity,
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
from agent_core.goal_graph.dependency_authority import (
    build_dependency_authority_attestation,
)
from agent_core.goal_graph.handoff_simulation import (
    build_dependency_authority_handoff_simulation,
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


def _attestation() -> dict:
    shadow = {
        "version": "typed-dependency-authority-shadow@1",
        "authority": "audit_only_current_dependency_enforcement_unchanged",
        "status": "MATCHED",
        "current_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "candidate_dependency_authority": TYPED_DEPENDENCY_AUTHORITY,
        "typed_coverage_status": "COMPLETE",
        "typed_dataflow_status": "GOAL_GRAPH_DATAFLOW_CLOSED",
        "typed_coverage_digest": "c" * 64,
        "typed_graph_id": "goal-graph:1:stage4d",
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
        semantic_contract_id="semantic:1:stage4d",
        semantic_digest="s" * 64,
        capability_registry_version="registry@stage4d",
        completed_goal_ids=(),
    )


def _identity(attestation: dict) -> dict:
    return {field: attestation[field] for field in _IDENTITY_FIELDS}


def _grant(attestation: dict) -> dict:
    grant = {
        "version": DEPENDENCY_CUTOVER_GRANT_VERSION,
        "authority": DEPENDENCY_CUTOVER_GRANT_AUTHORITY,
        "immutable": True,
        "status": "GRANTED",
        "external_authority_verified": True,
        "grant_id": "grant:stage4d:test",
        "issued_by": "governance:test",
        "attestation_digest": attestation["attestation_digest"],
        **_identity(attestation),
        "expires_at": 2000.0,
    }
    grant["grant_digest"] = _digest(grant)
    return grant


def _ready_gate() -> tuple[dict, dict]:
    attestation = _attestation()
    gate = evaluate_dependency_cutover_gate(
        attestation=attestation,
        grant=_grant(attestation),
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )
    return attestation, gate


def _rollback(gate: dict) -> dict:
    return build_dependency_authority_rollback_contract(
        gate=gate,
        rollback_requested=True,
        reason_code="STAGE4D_PREFLIGHT_REVERSION_DRILL",
    )


def _rollback_handoff(gate: dict, rollback: dict) -> dict:
    return build_dependency_authority_handoff_simulation(
        gate=gate,
        rollback=rollback,
        exercise_rollback=True,
    )


def _activation_request(
    *,
    attestation: dict,
    gate: dict,
    handoff: dict,
    rollback: dict,
    expires_at: float = 2000.0,
) -> dict:
    request = {
        "version": DEPENDENCY_ACTIVATION_REQUEST_VERSION,
        "authority": DEPENDENCY_ACTIVATION_REQUEST_AUTHORITY,
        "immutable": True,
        "status": "REQUESTED",
        "external_authority_verified": True,
        "request_id": "activation-request:stage4d:test",
        "issued_by": "governance:test",
        "desired_dependency_authority": TYPED_DEPENDENCY_AUTHORITY,
        "expected_current_runtime_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
        "attestation_digest": attestation["attestation_digest"],
        "gate_digest": gate["gate_digest"],
        "handoff_simulation_digest": handoff["simulation_digest"],
        "rollback_digest": rollback["rollback_digest"],
        **_identity(attestation),
        "expires_at": expires_at,
    }
    request["request_digest"] = _digest(request)
    return request


def _ready_inputs() -> tuple[dict, dict, dict, dict, dict]:
    attestation, gate = _ready_gate()
    rollback = _rollback(gate)
    handoff = _rollback_handoff(gate, rollback)
    request = _activation_request(
        attestation=attestation,
        gate=gate,
        handoff=handoff,
        rollback=rollback,
    )
    return attestation, gate, rollback, handoff, request


def test_exact_stage4d_preflight_can_be_ready_without_runtime_activation() -> None:
    attestation, gate, rollback, handoff, request = _ready_inputs()

    preflight = evaluate_dependency_activation_preflight(
        gate=gate,
        handoff_simulation=handoff,
        rollback=rollback,
        activation_request=request,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )

    assert preflight["status"] == "ACTIVATION_PREFLIGHT_READY"
    assert preflight["activation_candidate_ready"] is True
    assert preflight["rollback_drill_verified"] is True
    assert (
        preflight["selected_runtime_dependency_authority"]
        == LEGACY_DEPENDENCY_AUTHORITY
    )
    assert (
        preflight["would_select_if_separately_activated"]
        == TYPED_DEPENDENCY_AUTHORITY
    )
    assert preflight["runtime_activation_authority_granted"] is False
    assert preflight["activation_performed"] is False
    assert preflight["cutover_performed"] is False
    assert preflight["creates_permit"] is False
    assert preflight["changes_allowed_capability_tools"] is False
    assert preflight["mutates_business_state"] is False
    assert dependency_activation_preflight_integrity(preflight)["ok"] is True


def test_missing_activation_request_fails_closed_to_legacy() -> None:
    attestation, gate, rollback, handoff, _ = _ready_inputs()

    preflight = evaluate_dependency_activation_preflight(
        gate=gate,
        handoff_simulation=handoff,
        rollback=rollback,
        activation_request=None,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )

    assert preflight["status"] == "BLOCKED"
    assert preflight["activation_candidate_ready"] is False
    assert (
        preflight["selected_runtime_dependency_authority"]
        == LEGACY_DEPENDENCY_AUTHORITY
    )
    assert any(code.startswith("REQUEST:") for code in preflight["errors"])


def test_handoff_without_verified_rollback_drill_is_not_activation_ready() -> None:
    attestation, gate = _ready_gate()
    rollback = _rollback(gate)
    handoff = build_dependency_authority_handoff_simulation(gate=gate)
    request = _activation_request(
        attestation=attestation,
        gate=gate,
        handoff=handoff,
        rollback=rollback,
    )

    preflight = evaluate_dependency_activation_preflight(
        gate=gate,
        handoff_simulation=handoff,
        rollback=rollback,
        activation_request=request,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )

    assert preflight["status"] == "BLOCKED"
    assert "HANDOFF_ROLLBACK_DRILL_REQUIRED" in preflight["errors"]
    assert "HANDOFF_ROLLBACK_EXERCISE_REQUIRED" in preflight["errors"]


def test_expired_activation_request_fails_closed() -> None:
    attestation, gate, rollback, handoff, _ = _ready_inputs()
    request = _activation_request(
        attestation=attestation,
        gate=gate,
        handoff=handoff,
        rollback=rollback,
        expires_at=900.0,
    )

    preflight = evaluate_dependency_activation_preflight(
        gate=gate,
        handoff_simulation=handoff,
        rollback=rollback,
        activation_request=request,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )

    assert preflight["status"] == "BLOCKED"
    assert "ACTIVATION_REQUEST_EXPIRED" in preflight["errors"]


def test_activation_request_bound_to_another_gate_fails_closed() -> None:
    attestation, gate, rollback, handoff, request = _ready_inputs()
    request["gate_digest"] = "0" * 64
    request["request_digest"] = _digest(
        {k: v for k, v in request.items() if k != "request_digest"}
    )

    preflight = evaluate_dependency_activation_preflight(
        gate=gate,
        handoff_simulation=handoff,
        rollback=rollback,
        activation_request=request,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )

    assert preflight["status"] == "BLOCKED"
    assert "ACTIVATION_REQUEST_GATE_DIGEST_MISMATCH" in preflight["errors"]


def test_activation_request_bound_to_another_handoff_fails_closed() -> None:
    attestation, gate, rollback, handoff, request = _ready_inputs()
    request["handoff_simulation_digest"] = "0" * 64
    request["request_digest"] = _digest(
        {k: v for k, v in request.items() if k != "request_digest"}
    )

    preflight = evaluate_dependency_activation_preflight(
        gate=gate,
        handoff_simulation=handoff,
        rollback=rollback,
        activation_request=request,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )

    assert preflight["status"] == "BLOCKED"
    assert (
        "ACTIVATION_REQUEST_HANDOFF_SIMULATION_DIGEST_MISMATCH"
        in preflight["errors"]
    )


def test_current_identity_drift_fails_closed() -> None:
    attestation, gate, rollback, handoff, request = _ready_inputs()
    current = _identity(attestation)
    current["completion_snapshot_digest"] = "0" * 64

    preflight = evaluate_dependency_activation_preflight(
        gate=gate,
        handoff_simulation=handoff,
        rollback=rollback,
        activation_request=request,
        current_identity=current,
        evaluation_time=1000.0,
    )

    assert preflight["status"] == "BLOCKED"
    assert "CURRENT_COMPLETION_SNAPSHOT_DIGEST_MISMATCH" in preflight["errors"]


def test_request_integrity_rejects_legacy_as_desired_activation_authority() -> None:
    _, _, _, _, request = _ready_inputs()
    request["desired_dependency_authority"] = LEGACY_DEPENDENCY_AUTHORITY
    request["request_digest"] = _digest(
        {k: v for k, v in request.items() if k != "request_digest"}
    )

    integrity = dependency_activation_request_integrity(request)

    assert integrity["ok"] is False
    assert "ACTIVATION_REQUEST_DESIRED_AUTHORITY_INVALID" in integrity["errors"]


def test_preflight_integrity_rejects_recomputed_runtime_authority_switch() -> None:
    attestation, gate, rollback, handoff, request = _ready_inputs()
    preflight = evaluate_dependency_activation_preflight(
        gate=gate,
        handoff_simulation=handoff,
        rollback=rollback,
        activation_request=request,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )
    tampered = deepcopy(preflight)
    tampered["selected_runtime_dependency_authority"] = TYPED_DEPENDENCY_AUTHORITY
    tampered["activation_performed"] = True
    tampered["preflight_digest"] = _digest(
        {k: v for k, v in tampered.items() if k != "preflight_digest"}
    )

    integrity = dependency_activation_preflight_integrity(tampered)

    assert integrity["ok"] is False
    assert (
        "ACTIVATION_PREFLIGHT_SELECTED_AUTHORITY_MUST_BE_LEGACY"
        in integrity["errors"]
    )
    assert "ACTIVATION_PERFORMED_MUST_BE_FALSE" in integrity["errors"]


def test_stage4d_preflight_module_has_no_runtime_or_domain_import_authority() -> None:
    import agent_core.goal_graph.activation_preflight as module

    source = inspect.getsource(module)
    assert "agent_core.lifecycle" not in source
    assert "agent_core.runtime" not in source
    assert "agent_modules" not in source
    assert "ExecutionPermit" not in source
    assert "dispatch" not in source
