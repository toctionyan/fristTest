from __future__ import annotations

from copy import deepcopy

from agent_core.goal_graph.compiler import compile_frozen_semantic_contract
from agent_core.goal_graph.verification_contract import (
    VERIFICATION_AUTHORITY,
    VERIFICATION_CONTRACT_VERSION,
    VERIFICATION_DIGEST_ALGORITHM,
    build_verification_evidence,
    canonical_verification_digest,
    replay_verification_evidence,
)
from agent_core.goal_graph.verifier import verify_goal_graph
from agent_core.lifecycle.semantic_contract import freeze_semantic_contract


def _contract() -> dict:
    return freeze_semantic_contract(
        turn=1,
        user_text="查询订单",
        summary="typed verification contract",
        goals=[
            {
                "goal_id": "g1",
                "description": "查询订单",
                "evidence_span": "查询订单",
                "requested_effect": {
                    "domain": "order",
                    "operation": "details",
                    "object_type": "order",
                    "requested_outputs": [
                        {"output_id": "order.details", "evidence_span": "订单"}
                    ],
                },
                "expected_result_cardinality": "single",
                "required": True,
                "input_bindings": [],
            }
        ],
        alignment_proof={"verdict": "exact"},
    )


def _verification() -> dict:
    contract = _contract()
    graph = compile_frozen_semantic_contract(
        contract,
        scope={
            "tenant_id": "tenant-1",
            "user_id": "u001",
            "thread_id": "thread-1",
        },
    )
    return build_verification_evidence(
        verify_goal_graph(graph, frozen_contract=contract),
        graph=graph,
        frozen_contract=contract,
    )


def test_verification_contract_is_versioned_shadow_only_and_replayable() -> None:
    evidence = _verification()

    assert evidence["schema_version"] == VERIFICATION_CONTRACT_VERSION
    assert evidence["digest_algorithm"] == VERIFICATION_DIGEST_ALGORITHM
    assert evidence["authority"] == VERIFICATION_AUTHORITY
    assert evidence["execution_authority_granted"] is False
    assert evidence["tool_dispatch"] is False
    assert evidence["business_payload_included"] is False
    assert replay_verification_evidence(evidence)["ok"] is True


def test_verification_digest_is_stable_for_diagnostic_reordering() -> None:
    raw = {
        "ok": False,
        "structural": {
            "ok": False,
            "code": "GOAL_GRAPH_STRUCTURAL_INVALID",
            "errors": ["TYPE_MISMATCH", "SCOPE_MISMATCH"],
        },
        "dataflow": {
            "ok": False,
            "code": "GOAL_GRAPH_DATAFLOW_OPEN",
            "errors": ["DEPENDENCY_EDGE_CYCLE:g2->g1->g2"],
            "derived_dependencies": {"g2": ["g1", "g0"]},
        },
    }
    first = build_verification_evidence(raw)
    reordered = deepcopy(raw)
    reordered["structural"]["errors"].reverse()
    reordered["dataflow"]["derived_dependencies"]["g2"].reverse()
    second = build_verification_evidence(reordered)

    assert first["verification_digest"] == second["verification_digest"]
    assert first["failures"][0]["code"] == "scope_mismatch"
    assert first["failures"][1]["code"] == "type_mismatch"
    assert first["failures"][2]["code"] == "dependency_cycle"


def test_semantic_change_changes_digest_but_runtime_metadata_does_not() -> None:
    base = {
        "schema_version": VERIFICATION_CONTRACT_VERSION,
        "digest_algorithm": VERIFICATION_DIGEST_ALGORITHM,
        "ok": True,
        "structural": {"ok": True, "code": "VALID", "errors": []},
        "dataflow": {"ok": True, "code": "CLOSED", "errors": []},
        "timestamp": "2026-01-01T00:00:00Z",
    }
    changed_runtime = deepcopy(base)
    changed_runtime["timestamp"] = "2026-01-02T00:00:00Z"
    changed_semantics = deepcopy(base)
    changed_semantics["graph_digest"] = "graph:different"

    assert canonical_verification_digest(base) == canonical_verification_digest(changed_runtime)
    assert canonical_verification_digest(base) != canonical_verification_digest(changed_semantics)


def test_failure_taxonomy_is_structured_and_does_not_copy_dynamic_suffix() -> None:
    evidence = build_verification_evidence(
        {
            "ok": False,
            "structural": {
                "ok": False,
                "code": "GOAL_GRAPH_STRUCTURAL_INVALID",
                "errors": ["DATAFLOW_EDGE_CYCLE:user-secret:g2->g1->g2"],
            },
            "dataflow": {"ok": False, "code": "OPEN", "errors": []},
        }
    )

    failure = evidence["failures"][0]
    assert set(("code", "path", "category", "expected", "actual")) <= set(failure)
    assert failure["code"] == "dependency_cycle"
    assert "user-secret" not in str(failure)
    assert failure["actual"] == "DATAFLOW_EDGE_CYCLE"


def test_tampering_is_detected_by_replay_digest() -> None:
    evidence = _verification()
    tampered = deepcopy(evidence)
    tampered["dataflow"]["ok"] = not tampered["dataflow"]["ok"]

    assert replay_verification_evidence(tampered)["ok"] is False
