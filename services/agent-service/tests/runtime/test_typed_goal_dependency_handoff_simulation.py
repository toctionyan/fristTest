from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import inspect
import json

from agent_core.goal_graph.cutover_gate import (
    DEPENDENCY_CUTOVER_GRANT_AUTHORITY,
    DEPENDENCY_CUTOVER_GRANT_VERSION,
    LEGACY_DEPENDENCY_AUTHORITY,
    TYPED_DEPENDENCY_AUTHORITY,
    build_dependency_authority_rollback_contract,
    evaluate_dependency_cutover_gate,
)
from agent_core.goal_graph.dependency_authority import build_dependency_authority_attestation
from agent_core.goal_graph.handoff_simulation import (
    build_dependency_authority_handoff_simulation,
    dependency_authority_handoff_simulation_integrity,
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
        "typed_graph_id": "goal-graph:1:abc",
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
        semantic_contract_id="semantic:1:abc",
        semantic_digest="s" * 64,
        capability_registry_version="registry@1",
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
        "grant_id": "grant:stage4c:test",
        "issued_by": "governance:test",
        "attestation_digest": attestation["attestation_digest"],
        **_identity(attestation),
        "expires_at": 2000.0,
    }
    grant["grant_digest"] = _digest(grant)
    return grant


def _ready_gate() -> dict:
    attestation = _attestation()
    return evaluate_dependency_cutover_gate(
        attestation=attestation,
        grant=_grant(attestation),
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )


def _blocked_gate() -> dict:
    attestation = _attestation()
    return evaluate_dependency_cutover_gate(
        attestation=attestation,
        grant=None,
        current_identity=_identity(attestation),
        evaluation_time=1000.0,
    )


def _rollback(gate: dict) -> dict:
    return build_dependency_authority_rollback_contract(
        gate=gate,
        rollback_requested=True,
        reason_code="STAGE4C_DRY_RUN_REVERT",
    )


def test_blocked_gate_cannot_enter_typed_simulation() -> None:
    simulation = build_dependency_authority_handoff_simulation(gate=_blocked_gate())

    assert simulation["status"] == "BLOCKED"
    assert simulation["typed_candidate_entered_in_simulation"] is False
    assert simulation["simulated_final_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert simulation["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert "TYPED_HANDOFF_CANDIDATE_NOT_READY" in simulation["errors"]
    assert dependency_authority_handoff_simulation_integrity(simulation)["ok"] is True


def test_candidate_ready_gate_can_simulate_typed_without_runtime_cutover() -> None:
    simulation = build_dependency_authority_handoff_simulation(gate=_ready_gate())

    assert simulation["status"] == "TYPED_HANDOFF_SIMULATED"
    assert simulation["simulation_only"] is True
    assert simulation["typed_candidate_entered_in_simulation"] is True
    assert simulation["simulated_final_dependency_authority"] == TYPED_DEPENDENCY_AUTHORITY
    assert simulation["observed_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert simulation["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert simulation["runtime_activation_authority_granted"] is False
    assert simulation["cutover_performed"] is False
    assert simulation["creates_permit"] is False
    assert dependency_authority_handoff_simulation_integrity(simulation)["ok"] is True


def test_every_handoff_timeline_step_has_exactly_one_authority() -> None:
    gate = _ready_gate()
    simulation = build_dependency_authority_handoff_simulation(
        gate=gate,
        rollback=_rollback(gate),
        exercise_rollback=True,
    )

    assert simulation["status"] == "ROLLBACK_DRILL_COMPLETE"
    assert [step["selected_dependency_authority"] for step in simulation["timeline"]] == [
        LEGACY_DEPENDENCY_AUTHORITY,
        TYPED_DEPENDENCY_AUTHORITY,
        LEGACY_DEPENDENCY_AUTHORITY,
    ]
    assert all(len(step["active_authorities"]) == 1 for step in simulation["timeline"])
    assert simulation["single_authority_invariant"] is True
    assert simulation["dual_authority_observed"] is False


def test_rollback_drill_returns_simulation_to_legacy_without_runtime_mutation() -> None:
    gate = _ready_gate()
    simulation = build_dependency_authority_handoff_simulation(
        gate=gate,
        rollback=_rollback(gate),
        exercise_rollback=True,
    )

    assert simulation["rollback_exercised_in_simulation"] is True
    assert simulation["simulated_final_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert simulation["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert simulation["runtime_reversion_performed"] is False
    assert simulation["changes_current_dependency_blocking"] is False
    assert simulation["changes_allowed_capability_tools"] is False
    assert simulation["mutates_semantics"] is False
    assert simulation["mutates_business_state"] is False


def test_invalid_rollback_is_validated_before_any_typed_simulated_entry() -> None:
    gate = _ready_gate()
    rollback = _rollback(gate)
    rollback["reversion_target"] = TYPED_DEPENDENCY_AUTHORITY

    simulation = build_dependency_authority_handoff_simulation(
        gate=gate,
        rollback=rollback,
        exercise_rollback=True,
    )

    assert simulation["status"] == "BLOCKED"
    assert simulation["typed_candidate_entered_in_simulation"] is False
    assert simulation["timeline"][-1]["selected_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert any(code.startswith("ROLLBACK:") for code in simulation["errors"])
    assert "ROLLBACK_REVERSION_TARGET_INVALID" in simulation["errors"]
    assert dependency_authority_handoff_simulation_integrity(simulation)["ok"] is True


def test_rollback_bound_to_another_gate_fails_closed_before_typed_entry() -> None:
    gate = _ready_gate()
    rollback = _rollback(gate)
    rollback["source_gate_digest"] = "0" * 64
    rollback["rollback_digest"] = _digest({k: v for k, v in rollback.items() if k != "rollback_digest"})

    simulation = build_dependency_authority_handoff_simulation(
        gate=gate,
        rollback=rollback,
        exercise_rollback=True,
    )

    assert simulation["status"] == "BLOCKED"
    assert simulation["typed_candidate_entered_in_simulation"] is False
    assert "ROLLBACK_SOURCE_GATE_DIGEST_MISMATCH" in simulation["errors"]


def test_tampered_gate_fails_closed_before_typed_entry() -> None:
    gate = _ready_gate()
    gate["candidate_dependency_authority"] = LEGACY_DEPENDENCY_AUTHORITY

    simulation = build_dependency_authority_handoff_simulation(gate=gate)

    assert simulation["status"] == "BLOCKED"
    assert simulation["typed_candidate_entered_in_simulation"] is False
    assert simulation["selected_runtime_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert any(code.startswith("CUTOVER_GATE:") for code in simulation["errors"])


def test_unknown_simulated_authority_request_fails_closed() -> None:
    simulation = build_dependency_authority_handoff_simulation(
        gate=_ready_gate(),
        requested_simulated_authority="unknown_authority",
    )

    assert simulation["status"] == "BLOCKED"
    assert simulation["simulated_final_dependency_authority"] == LEGACY_DEPENDENCY_AUTHORITY
    assert "SIMULATED_AUTHORITY_REQUEST_INVALID" in simulation["errors"]


def test_explicit_legacy_simulation_never_enters_typed_candidate() -> None:
    simulation = build_dependency_authority_handoff_simulation(
        gate=_ready_gate(),
        requested_simulated_authority=LEGACY_DEPENDENCY_AUTHORITY,
    )

    assert simulation["status"] == "LEGACY_ONLY_SIMULATED"
    assert simulation["typed_candidate_entered_in_simulation"] is False
    assert simulation["timeline"] == [
        {
            "index": 0,
            "phase": "observed_runtime_baseline",
            "selected_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
            "active_authorities": [LEGACY_DEPENDENCY_AUTHORITY],
            "legacy_active": True,
            "typed_active": False,
        }
    ]


def test_integrity_rejects_a_post_build_dual_authority_timeline() -> None:
    simulation = build_dependency_authority_handoff_simulation(gate=_ready_gate())
    tampered = deepcopy(simulation)
    tampered["timeline"][1]["active_authorities"] = [
        LEGACY_DEPENDENCY_AUTHORITY,
        TYPED_DEPENDENCY_AUTHORITY,
    ]
    tampered["simulation_digest"] = _digest(
        {k: v for k, v in tampered.items() if k != "simulation_digest"}
    )

    integrity = dependency_authority_handoff_simulation_integrity(tampered)

    assert integrity["ok"] is False
    assert "HANDOFF_TIMELINE_STEP_1_NOT_SINGULAR" in integrity["errors"]


def test_integrity_rejects_post_build_runtime_authority_switch() -> None:
    simulation = build_dependency_authority_handoff_simulation(gate=_ready_gate())
    tampered = deepcopy(simulation)
    tampered["selected_runtime_dependency_authority"] = TYPED_DEPENDENCY_AUTHORITY
    tampered["simulation_digest"] = _digest(
        {k: v for k, v in tampered.items() if k != "simulation_digest"}
    )

    integrity = dependency_authority_handoff_simulation_integrity(tampered)

    assert integrity["ok"] is False
    assert "HANDOFF_SELECTED_RUNTIME_AUTHORITY_MUST_BE_LEGACY" in integrity["errors"]


def test_integrity_rejects_unsealed_post_build_tampering() -> None:
    simulation = build_dependency_authority_handoff_simulation(gate=_ready_gate())
    simulation["status"] = "BLOCKED"

    integrity = dependency_authority_handoff_simulation_integrity(simulation)

    assert integrity["ok"] is False
    assert "HANDOFF_SIMULATION_DIGEST_INVALID" in integrity["errors"]


def test_integrity_rejects_recomputed_repeated_typed_handoff_sequence() -> None:
    gate = _ready_gate()
    simulation = build_dependency_authority_handoff_simulation(
        gate=gate,
        rollback=_rollback(gate),
        exercise_rollback=True,
    )
    tampered = deepcopy(simulation)
    tampered["timeline"].extend(
        [
            {
                "index": 3,
                "phase": "simulated_typed_handoff",
                "selected_dependency_authority": TYPED_DEPENDENCY_AUTHORITY,
                "active_authorities": [TYPED_DEPENDENCY_AUTHORITY],
                "legacy_active": False,
                "typed_active": True,
            },
            {
                "index": 4,
                "phase": "simulated_rollback_to_legacy",
                "selected_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,
                "active_authorities": [LEGACY_DEPENDENCY_AUTHORITY],
                "legacy_active": True,
                "typed_active": False,
            },
        ]
    )
    tampered["simulation_digest"] = _digest(
        {k: v for k, v in tampered.items() if k != "simulation_digest"}
    )

    integrity = dependency_authority_handoff_simulation_integrity(tampered)

    assert integrity["ok"] is False
    assert "HANDOFF_TIMELINE_SHAPE_INVALID" in integrity["errors"]
    assert "HANDOFF_TYPED_STEP_COUNT_INVALID" in integrity["errors"]


def test_integrity_rejects_recomputed_phase_and_index_drift() -> None:
    simulation = build_dependency_authority_handoff_simulation(gate=_ready_gate())
    tampered = deepcopy(simulation)
    tampered["timeline"][1]["index"] = 7
    tampered["timeline"][1]["phase"] = "unexpected_phase"
    tampered["simulation_digest"] = _digest(
        {k: v for k, v in tampered.items() if k != "simulation_digest"}
    )

    integrity = dependency_authority_handoff_simulation_integrity(tampered)

    assert integrity["ok"] is False
    assert "HANDOFF_TIMELINE_STEP_1_INDEX_INVALID" in integrity["errors"]
    assert "HANDOFF_TIMELINE_SHAPE_INVALID" in integrity["errors"]


def test_integrity_rejects_recomputed_request_status_mismatch() -> None:
    simulation = build_dependency_authority_handoff_simulation(gate=_ready_gate())
    tampered = deepcopy(simulation)
    tampered["requested_simulated_authority"] = LEGACY_DEPENDENCY_AUTHORITY
    tampered["simulation_digest"] = _digest(
        {k: v for k, v in tampered.items() if k != "simulation_digest"}
    )

    integrity = dependency_authority_handoff_simulation_integrity(tampered)

    assert integrity["ok"] is False
    assert "HANDOFF_REQUEST_STATUS_MISMATCH" in integrity["errors"]


def test_stage4c_simulation_module_has_no_runtime_or_domain_import_authority() -> None:
    import agent_core.goal_graph.handoff_simulation as module

    source = inspect.getsource(module)
    assert "agent_core.lifecycle" not in source
    assert "agent_core.runtime" not in source
    assert "agent_modules" not in source
    assert "permit_created" not in source
