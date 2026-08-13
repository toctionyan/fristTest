from __future__ import annotations

from copy import deepcopy
import inspect
import json

import pytest

from agent_core.goal_graph import (
    compile_frozen_semantic_contract,
    dataflow_closure,
    graph_structural_integrity,
    make_verified_artifact_ref,
    make_verified_dataflow_edge,
    seal_goal_graph,
    with_verified_dataflow_edge,
)
from agent_core.goal_graph.contracts import canonical_digest
from agent_core.kernel.semantic_contract import compute_semantic_digest


def _effect(output_id: str, *, subject_type: str = "order") -> dict:
    return {
        "domain": output_id.split(".", 1)[0],
        "operation": "semantic_output_set",
        "object_type": subject_type,
        "subject_type": subject_type,
        "requested_outputs": [
            {
                "output_id": output_id,
                "evidence_span": output_id,
            }
        ],
        "raw_description": output_id,
    }


def _goal(
    goal_id: str,
    output_id: str,
    *,
    cardinality: str = "single",
    depends_on: tuple[str, ...] = (),
    target_candidate: dict | None = None,
    reference: dict | None = None,
) -> dict:
    row = {
        "goal_id": goal_id,
        "description": output_id,
        "evidence_span": output_id,
        "requested_effect": _effect(output_id),
        "expected_result_cardinality": cardinality,
        "required": True,
        "depends_on": list(depends_on),
    }
    if target_candidate is not None:
        row["target_candidate"] = deepcopy(target_candidate)
    if reference is not None:
        row.update(deepcopy(reference))
    return row


def _contract(goals: list[dict]) -> dict:
    contract = {
        "version": "frozen-turn-semantic-contract@1",
        "authority": "sole_formal_turn_semantics",
        "immutable": True,
        "turn": 7,
        "user_text": "test",
        "summary": "test",
        "goals": deepcopy(goals),
        "goal_changes": [],
        "blocker_resolutions": [],
        "focus_change": None,
        "alignment_proof": {"verdict": "exact"},
        "granularity_proof": {"verdict": "exact"},
        "semantic_rewrite_allowed_after_freeze": False,
    }
    contract["semantic_digest"] = compute_semantic_digest(contract)
    contract["semantic_contract_id"] = f"semantic:7:{contract['semantic_digest'][:20]}"
    return contract


def _scope() -> dict:
    return {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-1"}


def _verified_reference(*, members: list[str] | None = None) -> dict:
    member_handles = members or ["artifact:order-10002"]
    proof_digest = "proof-digest-1"
    result_ref = "view:orders:1"
    return {
        "reference_expression": {
            "reference_type": "ordinal_visible_member" if len(member_handles) == 1 else "temporal_visible_result",
            "object_type": "order",
            "expected_cardinality": "single" if len(member_handles) == 1 else "collection",
            "evidence_span": "visible order",
        },
        "referent_resolution_proof": {
            "resolution_status": "UNIQUE",
            "resolved_result_ref": result_ref,
            "resolved_member_handles": member_handles,
            "proof_digest": proof_digest,
            "candidate_refs": [
                {
                    "result_ref": result_ref,
                    "resource_types": ["order"],
                    "checks": {
                        "object_type_match": True,
                        "object_type_proven": True,
                    },
                }
            ],
        },
        "resolved_reference": {
            "result_ref": result_ref,
            "member_handles": member_handles,
            "proof_digest": proof_digest,
        },
    }


def test_compile_is_deterministic_and_does_not_mutate_frozen_contract() -> None:
    contract = _contract([_goal("g1", "order.collection", cardinality="collection")])
    before = deepcopy(contract)
    first = compile_frozen_semantic_contract(contract, scope=_scope())
    second = compile_frozen_semantic_contract(contract, scope=_scope())

    assert first == second
    assert contract == before
    assert first["graph_digest"] == second["graph_digest"]
    assert first["runtime_behavior_change"] is False
    assert first["shadow_only"] is True


def test_graph_contains_no_tool_or_capability_availability_surface() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "order.collection", cardinality="collection")]),
        scope=_scope(),
    )
    serialized = json.dumps(graph, ensure_ascii=False).casefold()

    assert "tool_name" not in serialized
    assert "discovery_examples" not in serialized
    assert "allowed_capability" not in serialized
    assert "availability" not in serialized
    assert "capability" not in serialized
    assert graph["compiler_guarantees"]["input_authority"] == "frozen_semantic_contract_only"


def test_legacy_depends_on_is_not_promoted_to_fake_dataflow_edge() -> None:
    contract = _contract([
        _goal("g1", "order.collection", cardinality="collection"),
        _goal("g2", "refund.request", depends_on=("g1",), target_candidate={"opaque_target": "candidate"}),
    ])
    graph = compile_frozen_semantic_contract(contract, scope=_scope())

    assert graph["edges"] == []
    g2 = {row["goal_id"]: row for row in graph["goals"]}["g2"]
    assert g2["compatibility"]["legacy_dependency_claims"] == ["g1"]
    assert g2["compatibility"]["dependency_claims_authoritative"] is False
    assert g2["target_binding"]["verified"] is False


def test_attempt8_root_shape_is_structurally_valid_but_dataflow_open() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "order.collection", cardinality="collection"),
            _goal(
                "g2",
                "refund.request",
                depends_on=("g1",),
                target_candidate={"opaque_target": "candidate"},
            ),
        ]),
        scope=_scope(),
    )

    structural = graph_structural_integrity(graph)
    closure = dataflow_closure(graph)

    assert structural["ok"] is True
    assert closure["ok"] is False
    assert "DEPENDENCY_CLAIM_UNVERIFIED:g2:g1" in closure["errors"]
    assert any(error.startswith("REQUIRED_INPUT_UNRESOLVED:g2:") for error in closure["errors"])


def test_verified_data_edge_derives_dependency_instead_of_model_claim() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "order.collection", cardinality="collection"),
            _goal(
                "g2",
                "refund.request",
                depends_on=("g1",),
                target_candidate={"opaque_target": "candidate"},
            ),
        ]),
        scope=_scope(),
    )
    goals = {row["goal_id"]: row for row in graph["goals"]}
    producer_port = goals["g1"]["output_ports"][0]
    consumer_port = goals["g2"]["input_ports"][0]
    artifact = make_verified_artifact_ref(
        artifact_ref="result:verified-order-set",
        type_name="OrderSet",
        resource_type="order",
        cardinality="collection",
        producer_goal_id="g1",
        scope=_scope(),
        semantic_contract_id=graph["source_semantic_contract"]["semantic_contract_id"],
        semantic_digest=graph["source_semantic_contract"]["semantic_digest"],
        source_ref_id="goal-output:verified-1",
        proof_digest="artifact-proof-1",
    )
    edge = make_verified_dataflow_edge(
        graph=graph,
        producer_goal_id="g1",
        producer_port_id=producer_port["port_id"],
        consumer_goal_id="g2",
        consumer_port_id=consumer_port["port_id"],
        artifact_ref=artifact,
        verification_proof_digest="edge-proof-1",
    )
    graph = with_verified_dataflow_edge(graph, edge)

    closure = dataflow_closure(graph)
    assert closure["ok"] is True
    assert closure["derived_dependencies"]["g2"] == ["g1"]
    assert closure["dependency_authority"] == "verified_dataflow_edges_only"


def test_unresolved_target_fails_closed_and_verified_frozen_reference_passes() -> None:
    unresolved = compile_frozen_semantic_contract(
        _contract([_goal("g1", "refund.eligibility", target_candidate={"opaque": "candidate"})]),
        scope=_scope(),
    )
    assert dataflow_closure(unresolved)["ok"] is False

    verified = compile_frozen_semantic_contract(
        _contract([_goal("g1", "refund.eligibility", reference=_verified_reference())]),
        scope=_scope(),
    )
    goal = verified["goals"][0]
    assert goal["target_binding"]["verified"] is True
    assert goal["target_binding"]["provenance"]["source"] == "frozen.resolved_reference"
    assert dataflow_closure(verified)["ok"] is True


def test_missing_reference_proof_is_not_treated_as_verified_target() -> None:
    reference = _verified_reference()
    reference["referent_resolution_proof"].pop("proof_digest")
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "refund.eligibility", reference=reference)]),
        scope=_scope(),
    )
    binding = graph["goals"][0]["target_binding"]
    assert binding["verified"] is False
    assert binding["reason_code"] == "FROZEN_TARGET_PROOF_MISMATCH"


def test_cross_scope_target_binding_is_rejected_even_when_graph_is_resealed() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "refund.eligibility", reference=_verified_reference())]),
        scope=_scope(),
    )
    graph["goals"][0]["target_binding"]["scope"]["thread_id"] = "other-thread"
    graph = seal_goal_graph(graph)

    structural = graph_structural_integrity(graph)
    assert structural["ok"] is False
    assert "TARGET_BINDING_SCOPE_MISMATCH" in structural["errors"]


def test_collection_cannot_silently_feed_exactly_one_input() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "order.collection", cardinality="collection"),
            _goal("g2", "refund.request", depends_on=("g1",), target_candidate={"opaque": "candidate"}),
        ]),
        scope=_scope(),
    )
    goals = {row["goal_id"]: row for row in graph["goals"]}
    goals["g2"]["input_ports"][0]["cardinality"] = "exactly_one"
    graph = seal_goal_graph(graph)
    artifact = make_verified_artifact_ref(
        artifact_ref="result:order-set",
        type_name="OrderSet",
        resource_type="order",
        cardinality="collection",
        producer_goal_id="g1",
        scope=_scope(),
        semantic_contract_id=graph["source_semantic_contract"]["semantic_contract_id"],
        semantic_digest=graph["source_semantic_contract"]["semantic_digest"],
        source_ref_id="goal-output:collection",
        proof_digest="artifact-proof",
    )
    edge = make_verified_dataflow_edge(
        graph=graph,
        producer_goal_id="g1",
        producer_port_id=goals["g1"]["output_ports"][0]["port_id"],
        consumer_goal_id="g2",
        consumer_port_id=goals["g2"]["input_ports"][0]["port_id"],
        artifact_ref=artifact,
        verification_proof_digest="edge-proof",
    )
    graph = with_verified_dataflow_edge(graph, edge)

    structural = graph_structural_integrity(graph)
    assert structural["ok"] is False
    assert "DATAFLOW_EDGE_CONSUMER_CARDINALITY_MISMATCH" in structural["errors"]


def test_verified_member_selection_must_be_explicit_for_collection_to_single_projection() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "order.collection", cardinality="collection"),
            _goal("g2", "refund.request", depends_on=("g1",), target_candidate={"opaque": "candidate"}),
        ]),
        scope=_scope(),
    )
    goals = {row["goal_id"]: row for row in graph["goals"]}
    goals["g2"]["input_ports"][0]["cardinality"] = "exactly_one"
    graph = seal_goal_graph(graph)
    artifact = make_verified_artifact_ref(
        artifact_ref="artifact:order-10002",
        type_name="OrderSet",
        resource_type="order",
        cardinality="exactly_one",
        producer_goal_id="g1",
        scope=_scope(),
        semantic_contract_id=graph["source_semantic_contract"]["semantic_contract_id"],
        semantic_digest=graph["source_semantic_contract"]["semantic_digest"],
        source_ref_id="goal-output:selected-member",
        proof_digest="artifact-proof",
    )
    edge = make_verified_dataflow_edge(
        graph=graph,
        producer_goal_id="g1",
        producer_port_id=goals["g1"]["output_ports"][0]["port_id"],
        consumer_goal_id="g2",
        consumer_port_id=goals["g2"]["input_ports"][0]["port_id"],
        artifact_ref=artifact,
        verification_proof_digest="edge-proof",
        projection={
            "kind": "verified_member_selection",
            "source_result_ref": "result:order-set",
            "member_handle": "artifact:order-10002",
            "proof_digest": "selection-proof",
        },
    )
    graph = with_verified_dataflow_edge(graph, edge)

    assert graph_structural_integrity(graph)["ok"] is True
    assert dataflow_closure(graph)["ok"] is True


def test_verified_artifact_ref_is_pointer_only_and_never_copies_business_facts() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "order.collection", cardinality="collection")]),
        scope=_scope(),
    )
    ref = make_verified_artifact_ref(
        artifact_ref="view:orders",
        type_name="OrderSet",
        resource_type="order",
        cardinality="collection",
        producer_goal_id="g1",
        scope=_scope(),
        semantic_contract_id=graph["source_semantic_contract"]["semantic_contract_id"],
        semantic_digest=graph["source_semantic_contract"]["semantic_digest"],
        source_ref_id="goal-output:1",
        proof_digest="proof:1",
    )
    serialized = json.dumps(ref, ensure_ascii=False)

    assert "facts" not in ref
    assert "data" not in ref
    assert "payload" not in ref
    assert "business_facts_copied" in serialized
    assert ref["provenance"]["business_facts_copied"] is False


def test_goal_graph_does_not_redefine_runtime_goal_output_ref_contract() -> None:
    import agent_core.goal_graph.contracts as contracts

    source = inspect.getsource(contracts)
    assert "GOAL_OUTPUT_REF_VERSION" not in source
    assert "goal-output-ref@1" not in source


def test_goal_graph_package_is_domain_neutral() -> None:
    import agent_core.goal_graph.compiler as compiler
    import agent_core.goal_graph.contracts as contracts
    import agent_core.goal_graph.verifier as verifier

    source = "\n".join(inspect.getsource(module) for module in (compiler, contracts, verifier))
    assert "agent_modules.ecommerce" not in source
    assert "ecommerce" not in source.casefold()


def test_graph_digest_tampering_is_detected() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "order.collection", cardinality="collection")]),
        scope=_scope(),
    )
    graph["goals"][0]["evidence_span"] = "tampered"

    structural = graph_structural_integrity(graph)
    assert structural["ok"] is False
    assert "GOAL_GRAPH_DIGEST_INVALID" in structural["errors"]


def test_verifier_can_bind_graph_to_the_exact_frozen_semantic_identity() -> None:
    contract = _contract([_goal("g1", "order.collection", cardinality="collection")])
    graph = compile_frozen_semantic_contract(contract, scope=_scope())
    other = _contract([_goal("g1", "order.details", cardinality="single")])

    assert graph_structural_integrity(graph, frozen_contract=contract)["ok"] is True
    mismatch = graph_structural_integrity(graph, frozen_contract=other)
    assert mismatch["ok"] is False
    assert "GOAL_GRAPH_SOURCE_SEMANTIC_MISMATCH" in mismatch["errors"]


def test_edge_identity_tampering_is_detected_even_after_graph_is_resealed() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "order.collection", cardinality="collection"),
            _goal("g2", "refund.request", depends_on=("g1",), target_candidate={"opaque": "candidate"}),
        ]),
        scope=_scope(),
    )
    goals = {row["goal_id"]: row for row in graph["goals"]}
    artifact = make_verified_artifact_ref(
        artifact_ref="result:orders",
        type_name="OrderSet",
        resource_type="order",
        cardinality="collection",
        producer_goal_id="g1",
        scope=_scope(),
        semantic_contract_id=graph["source_semantic_contract"]["semantic_contract_id"],
        semantic_digest=graph["source_semantic_contract"]["semantic_digest"],
        source_ref_id="goal-output:orders",
        proof_digest="artifact-proof",
    )
    edge = make_verified_dataflow_edge(
        graph=graph,
        producer_goal_id="g1",
        producer_port_id=goals["g1"]["output_ports"][0]["port_id"],
        consumer_goal_id="g2",
        consumer_port_id=goals["g2"]["input_ports"][0]["port_id"],
        artifact_ref=artifact,
        verification_proof_digest="edge-proof",
    )
    graph = with_verified_dataflow_edge(graph, edge)
    graph["edges"][0]["verification_proof_digest"] = "tampered-proof"
    graph = seal_goal_graph(graph)

    structural = graph_structural_integrity(graph)
    assert structural["ok"] is False
    assert "DATAFLOW_EDGE_ID_INVALID" in structural["errors"]


def test_nested_business_fact_payload_in_verified_artifact_ref_is_rejected() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "order.collection", cardinality="collection"),
            _goal("g2", "refund.request", depends_on=("g1",), target_candidate={"opaque": "candidate"}),
        ]),
        scope=_scope(),
    )
    goals = {row["goal_id"]: row for row in graph["goals"]}
    artifact = make_verified_artifact_ref(
        artifact_ref="result:orders",
        type_name="OrderSet",
        resource_type="order",
        cardinality="collection",
        producer_goal_id="g1",
        scope=_scope(),
        semantic_contract_id=graph["source_semantic_contract"]["semantic_contract_id"],
        semantic_digest=graph["source_semantic_contract"]["semantic_digest"],
        source_ref_id="goal-output:orders",
        proof_digest="artifact-proof",
    )
    artifact["provenance"]["nested"] = {"facts": {"status": "shipped"}}
    unsigned = deepcopy(artifact)
    unsigned.pop("ref_digest", None)
    artifact["ref_digest"] = canonical_digest(unsigned)
    edge = make_verified_dataflow_edge(
        graph=graph,
        producer_goal_id="g1",
        producer_port_id=goals["g1"]["output_ports"][0]["port_id"],
        consumer_goal_id="g2",
        consumer_port_id=goals["g2"]["input_ports"][0]["port_id"],
        artifact_ref=artifact,
        verification_proof_digest="edge-proof",
    )
    graph = with_verified_dataflow_edge(graph, edge)

    structural = graph_structural_integrity(graph)
    assert structural["ok"] is False
    assert "VERIFIED_ARTIFACT_REF_COPIES_BUSINESS_FACTS" in structural["errors"]


def test_verified_artifact_ref_requires_proven_type_and_cardinality() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "order.collection", cardinality="collection")]),
        scope=_scope(),
    )
    source = graph["source_semantic_contract"]
    with pytest.raises(ValueError, match="VERIFIED_ARTIFACT_RESOURCE_TYPE_REQUIRED"):
        make_verified_artifact_ref(
            artifact_ref="result:x",
            type_name="OrderSet",
            resource_type="unspecified",
            cardinality="collection",
            producer_goal_id="g1",
            scope=_scope(),
            semantic_contract_id=source["semantic_contract_id"],
            semantic_digest=source["semantic_digest"],
            source_ref_id="goal-output:x",
            proof_digest="proof",
        )
    with pytest.raises(ValueError, match="VERIFIED_ARTIFACT_CARDINALITY_REQUIRED"):
        make_verified_artifact_ref(
            artifact_ref="result:x",
            type_name="OrderSet",
            resource_type="order",
            cardinality="unknown",
            producer_goal_id="g1",
            scope=_scope(),
            semantic_contract_id=source["semantic_contract_id"],
            semantic_digest=source["semantic_digest"],
            source_ref_id="goal-output:x",
            proof_digest="proof",
        )


def test_verified_target_binding_cannot_be_reinterpreted_as_different_cardinality() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal(
                "g1",
                "refund.eligibility",
                reference=_verified_reference(members=["artifact:o1", "artifact:o2"]),
            )
        ]),
        scope=_scope(),
    )
    graph["goals"][0]["input_ports"][0]["cardinality"] = "exactly_one"
    graph = seal_goal_graph(graph)

    structural = graph_structural_integrity(graph)
    assert structural["ok"] is False
    assert "TARGET_BINDING_CARDINALITY_MISMATCH" in structural["errors"]


def test_one_input_cannot_have_verified_binding_and_verified_edge_as_parallel_authorities() -> None:
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "order.details", cardinality="single"),
            _goal(
                "g2",
                "refund.eligibility",
                depends_on=("g1",),
                reference=_verified_reference(),
            ),
        ]),
        scope=_scope(),
    )
    goals = {row["goal_id"]: row for row in graph["goals"]}
    artifact = make_verified_artifact_ref(
        artifact_ref="artifact:order-10002",
        type_name="OrderSet",
        resource_type="order",
        cardinality="exactly_one",
        producer_goal_id="g1",
        scope=_scope(),
        semantic_contract_id=graph["source_semantic_contract"]["semantic_contract_id"],
        semantic_digest=graph["source_semantic_contract"]["semantic_digest"],
        source_ref_id="goal-output:order-10002",
        proof_digest="artifact-proof",
    )
    edge = make_verified_dataflow_edge(
        graph=graph,
        producer_goal_id="g1",
        producer_port_id=goals["g1"]["output_ports"][0]["port_id"],
        consumer_goal_id="g2",
        consumer_port_id=goals["g2"]["input_ports"][0]["port_id"],
        artifact_ref=artifact,
        verification_proof_digest="edge-proof",
    )
    graph = with_verified_dataflow_edge(graph, edge)

    closure = dataflow_closure(graph)
    assert closure["ok"] is False
    assert any(error.startswith("REQUIRED_INPUT_MULTIPLE_AUTHORITIES:g2:") for error in closure["errors"])
