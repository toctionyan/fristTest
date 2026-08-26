from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import inspect

import pytest

from agent_core.goal_graph import (
    build_typed_goal_capability_coverage,
    compile_frozen_semantic_contract,
    make_verified_artifact_ref,
    make_verified_dataflow_edge,
    seal_goal_graph,
    with_verified_dataflow_edge,
)
from agent_core.goal_graph.contracts import make_goal_port
from agent_core.kernel.semantic_contract import compute_semantic_digest


def _effect(output_id: str, *, subject_type: str = "order") -> dict:
    return {
        "domain": output_id.split(".", 1)[0],
        "operation": "semantic_output_set",
        "object_type": subject_type,
        "subject_type": subject_type,
        "requested_outputs": [{"output_id": output_id, "evidence_span": output_id}],
        "raw_description": output_id,
    }


def _goal(
    goal_id: str,
    output_id: str,
    *,
    subject_type: str = "order",
    cardinality: str = "single",
    depends_on: tuple[str, ...] = (),
    target_candidate: dict | None = None,
    reference: dict | None = None,
) -> dict:
    row = {
        "goal_id": goal_id,
        "description": output_id,
        "evidence_span": output_id,
        "requested_effect": _effect(output_id, subject_type=subject_type),
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
        "turn": 8,
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
    contract["semantic_contract_id"] = f"semantic:8:{contract['semantic_digest'][:20]}"
    return contract


def _scope() -> dict:
    return {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-1"}


def _verified_reference(*, members: list[str] | None = None, resource_type: str = "order") -> dict:
    member_handles = members or [f"artifact:{resource_type}-10002"]
    proof_digest = "proof-digest-1"
    result_ref = f"view:{resource_type}:1"
    return {
        "reference_expression": {
            "reference_type": "ordinal_visible_member" if len(member_handles) == 1 else "temporal_visible_result",
            "object_type": resource_type,
            "expected_cardinality": "single" if len(member_handles) == 1 else "collection",
            "evidence_span": "visible resource",
        },
        "referent_resolution_proof": {
            "resolution_status": "UNIQUE",
            "resolved_result_ref": result_ref,
            "resolved_member_handles": member_handles,
            "proof_digest": proof_digest,
            "candidate_refs": [
                {
                    "result_ref": result_ref,
                    "resource_types": [resource_type],
                    "checks": {"object_type_match": True, "object_type_proven": True},
                }
            ],
        },
        "resolved_reference": {
            "result_ref": result_ref,
            "member_handles": member_handles,
            "proof_digest": proof_digest,
        },
    }


@dataclass(frozen=True)
class _Target:
    resource_types: tuple[str, ...]
    cardinality: str
    binding_sources: tuple[str, ...]


@dataclass(frozen=True)
class _Input:
    name: str
    type_name: str
    source_types: tuple[str, ...]
    authority: str = "authoritative"
    required: bool = True
    freshness_seconds: int | None = None


@dataclass(frozen=True)
class _DictContract:
    value: dict

    def as_dict(self) -> dict:
        return deepcopy(self.value)


@dataclass(frozen=True)
class _Planning:
    target: _Target
    requires: tuple[_Input, ...]
    authorization: _DictContract = _DictContract({"required": False, "mode": "none", "authority": "none"})
    completion: _DictContract = _DictContract({"mode": "tool_output", "proof_type": "Result", "proof_source": "verified_tool_output", "output_name": "result"})


@dataclass(frozen=True)
class _Capability:
    key: str
    tool_name: str
    semantic_effects: tuple[str, ...]
    planning_contract: _Planning
    contract_version: str = "2"
    execution_kind: str = "grounding_read"
    discovery_examples: tuple[str, ...] = ()
    exclusion_examples: tuple[str, ...] = ()


class _Registry:
    def __init__(self, *contracts: _Capability):
        self._contracts = {row.tool_name: row for row in contracts}
        self.version = "test-registry@1"

    def tool_names(self):
        return tuple(sorted(self._contracts))

    def contract_for_tool(self, tool_name: str):
        return self._contracts.get(tool_name)


def _patch_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_core.goal_graph.capability_closure as closure

    monkeypatch.setattr(
        closure,
        "completion_effects_for_contract",
        lambda contract: tuple(contract.semantic_effects),
    )


def _semantic_identity(output_id: str) -> str:
    return f"semantic-output:{output_id}"


def _refund_capability(
    *,
    target_type: str = "order",
    target_cardinality: str = "exactly_one",
    binding_sources: tuple[str, ...] = ("target_resolver", "visible_result_ref", "verified_context"),
    extra_inputs: tuple[_Input, ...] = (),
) -> _Capability:
    return _Capability(
        key="demo.refund.prepare",
        tool_name="prepare_demo_refund",
        semantic_effects=(_semantic_identity("refund.request"),),
        planning_contract=_Planning(
            target=_Target((target_type,), target_cardinality, binding_sources),
            requires=(
                _Input(
                    name="target_binding",
                    type_name="ResolvedResourceBinding",
                    source_types=binding_sources,
                ),
                *extra_inputs,
            ),
            authorization=_DictContract(
                {"required": True, "mode": "structured_interaction", "authority": "transaction_authority"}
            ),
            completion=_DictContract(
                {"mode": "transaction_receipt", "proof_type": "RefundReceipt", "proof_source": "transaction_authority", "output_name": None}
            ),
        ),
        discovery_examples=("totally irrelevant example",),
    )


def test_attempt8_shape_effect_match_is_not_enough_without_typed_target(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "order.collection", cardinality="collection"),
            _goal("g2", "refund.request", depends_on=("g1",), target_candidate={"opaque": "mouse order"}),
        ]),
        scope=_scope(),
    )
    coverage = build_typed_goal_capability_coverage(
        graph=graph,
        capability_registry=_Registry(_refund_capability()),
    )
    g2 = {row["goal_id"]: row for row in coverage["goals"]}["g2"]

    assert coverage["coverage_status"] == "DATAFLOW_OPEN"
    assert g2["status"] == "EFFECT_MATCH_BUT_TYPED_UNCLOSED"
    assert g2["closed_capability_tools"] == []
    assert "GOAL_TARGET_UNRESOLVED" in g2["candidate_proofs"][0]["reasons"]
    assert coverage["model_target_selection_authority"] is False


def test_verified_historical_target_closes_target_and_missing_reason_is_collectable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "refund.request", reference=_verified_reference())]),
        scope=_scope(),
    )
    capability = _refund_capability(
        extra_inputs=(
            _Input("refund_reason", "RefundReason", ("user_input", "structured_interaction"), "candidate_then_structured"),
        )
    )
    coverage = build_typed_goal_capability_coverage(graph=graph, capability_registry=_Registry(capability))
    goal = coverage["goals"][0]
    proof = goal["candidate_proofs"][0]

    assert coverage["coverage_status"] == "INCOMPLETE"
    assert goal["status"] == "TYPED_COVERED_NEEDS_INTERACTION"
    assert proof["target_proof"]["ok"] is True
    assert proof["input_proof"]["readiness"] == "NEEDS_INTERACTION"
    assert proof["input_proof"]["collectable_input_names"] == ["refund_reason"]
    assert proof["execution_authority_granted"] is False
    assert proof["permit_created"] is False


def test_capability_target_resource_type_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "refund.request", reference=_verified_reference())]),
        scope=_scope(),
    )
    coverage = build_typed_goal_capability_coverage(
        graph=graph,
        capability_registry=_Registry(_refund_capability(target_type="ticket")),
    )
    proof = coverage["goals"][0]["candidate_proofs"][0]
    assert proof["status"] == "REJECTED"
    assert "CAPABILITY_TARGET_RESOURCE_TYPE_MISMATCH" in proof["reasons"]


def test_collection_target_cannot_match_exactly_one_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal(
                "g1",
                "refund.request",
                reference=_verified_reference(members=["artifact:o1", "artifact:o2"]),
            )
        ]),
        scope=_scope(),
    )
    coverage = build_typed_goal_capability_coverage(
        graph=graph,
        capability_registry=_Registry(_refund_capability(target_cardinality="exactly_one")),
    )
    proof = coverage["goals"][0]["candidate_proofs"][0]
    assert proof["status"] == "REJECTED"
    assert "CAPABILITY_TARGET_CARDINALITY_MISMATCH" in proof["reasons"]


def test_target_binding_source_is_contract_checked_without_input_name_heuristics(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "refund.request", reference=_verified_reference())]),
        scope=_scope(),
    )
    good = _refund_capability(binding_sources=("visible_result_ref",))
    bad = _refund_capability(binding_sources=("capability_output",))

    good_proof = build_typed_goal_capability_coverage(
        graph=graph, capability_registry=_Registry(good)
    )["goals"][0]["candidate_proofs"][0]
    bad_proof = build_typed_goal_capability_coverage(
        graph=graph, capability_registry=_Registry(bad)
    )["goals"][0]["candidate_proofs"][0]

    assert good_proof["input_proof"]["inputs"][0]["name"] == "target_binding"
    assert good_proof["input_proof"]["inputs"][0]["status"] == "SATISFIED_BY_TARGET"
    assert "CAPABILITY_TARGET_BINDING_SOURCE_MISMATCH" in bad_proof["reasons"]


def test_capability_output_input_requires_exact_typed_upstream_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "refund.eligibility", reference=_verified_reference()),
            _goal("g2", "refund.request", depends_on=("g1",), reference=_verified_reference()),
        ]),
        scope=_scope(),
    )
    g2 = next(row for row in graph["goals"] if row["goal_id"] == "g2")
    g2["input_ports"].append(
        make_goal_port(
            goal_id="g2",
            name="eligibility_assessment",
            direction="input",
            type_name="order",
            cardinality="exactly_one",
            required=True,
        )
    )
    graph = seal_goal_graph(graph)

    capability = _refund_capability(
        extra_inputs=(
            _Input("eligibility_assessment", "RefundEligibilityAssessment", ("capability_output",), "verified_ledger"),
        )
    )
    before = build_typed_goal_capability_coverage(graph=graph, capability_registry=_Registry(capability))
    before_g2 = {row["goal_id"]: row for row in before["goals"]}["g2"]
    assert before_g2["candidate_proofs"][0]["status"] == "BLOCKED_INPUT"

    goals = {row["goal_id"]: row for row in graph["goals"]}
    artifact = make_verified_artifact_ref(
        artifact_ref="artifact:eligibility:1",
        type_name="RefundEligibilityAssessment",
        resource_type="order",
        cardinality="exactly_one",
        producer_goal_id="g1",
        scope=_scope(),
        semantic_contract_id=graph["source_semantic_contract"]["semantic_contract_id"],
        semantic_digest=graph["source_semantic_contract"]["semantic_digest"],
        source_ref_id="goal-output:eligibility:1",
        proof_digest="artifact-proof",
        authority="verified_ledger",
    )
    edge = make_verified_dataflow_edge(
        graph=graph,
        producer_goal_id="g1",
        producer_port_id=goals["g1"]["output_ports"][0]["port_id"],
        consumer_goal_id="g2",
        consumer_port_id=next(
            row["port_id"] for row in goals["g2"]["input_ports"] if row["name"] == "eligibility_assessment"
        ),
        artifact_ref=artifact,
        verification_proof_digest="edge-proof",
    )
    graph = with_verified_dataflow_edge(graph, edge)
    after = build_typed_goal_capability_coverage(graph=graph, capability_registry=_Registry(capability))
    after_g2 = {row["goal_id"]: row for row in after["goals"]}["g2"]
    proof = after_g2["candidate_proofs"][0]

    assert after["dataflow_status"] == "GOAL_GRAPH_DATAFLOW_CLOSED"
    assert proof["status"] == "READY"
    typed_input = next(row for row in proof["input_proof"]["inputs"] if row["name"] == "eligibility_assessment")
    assert typed_input["status"] == "SATISFIED_BY_UPSTREAM_OUTPUT"
    assert typed_input["proof_refs"] == [edge["edge_id"]]


def test_wrong_upstream_logical_type_does_not_satisfy_capability_input(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "refund.eligibility", reference=_verified_reference()),
            _goal("g2", "refund.request", depends_on=("g1",), reference=_verified_reference()),
        ]),
        scope=_scope(),
    )
    g2 = next(row for row in graph["goals"] if row["goal_id"] == "g2")
    g2["input_ports"].append(
        make_goal_port(
            goal_id="g2", name="eligibility_assessment", direction="input",
            type_name="order", cardinality="exactly_one", required=True,
        )
    )
    graph = seal_goal_graph(graph)
    goals = {row["goal_id"]: row for row in graph["goals"]}
    artifact = make_verified_artifact_ref(
        artifact_ref="artifact:wrong:1",
        type_name="DifferentAssessment",
        resource_type="order",
        cardinality="exactly_one",
        producer_goal_id="g1",
        scope=_scope(),
        semantic_contract_id=graph["source_semantic_contract"]["semantic_contract_id"],
        semantic_digest=graph["source_semantic_contract"]["semantic_digest"],
        source_ref_id="goal-output:wrong:1",
        proof_digest="artifact-proof",
        authority="verified_ledger",
    )
    graph = with_verified_dataflow_edge(
        graph,
        make_verified_dataflow_edge(
            graph=graph,
            producer_goal_id="g1",
            producer_port_id=goals["g1"]["output_ports"][0]["port_id"],
            consumer_goal_id="g2",
            consumer_port_id=next(row["port_id"] for row in goals["g2"]["input_ports"] if row["name"] == "eligibility_assessment"),
            artifact_ref=artifact,
            verification_proof_digest="edge-proof",
        ),
    )
    capability = _refund_capability(
        extra_inputs=(_Input("eligibility_assessment", "RefundEligibilityAssessment", ("capability_output",), "verified_ledger"),)
    )
    proof = {
        row["goal_id"]: row for row in build_typed_goal_capability_coverage(
            graph=graph, capability_registry=_Registry(capability)
        )["goals"]
    }["g2"]["candidate_proofs"][0]
    assert proof["status"] == "BLOCKED_INPUT"
    assert "eligibility_assessment" in proof["input_proof"]["blocking_input_names"]


def test_no_target_goal_requires_capability_with_no_target(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "order.collection", cardinality="collection")]),
        scope=_scope(),
    )
    capability = _Capability(
        key="demo.order.list",
        tool_name="list_demo_resources",
        semantic_effects=(_semantic_identity("order.collection"),),
        planning_contract=_Planning(
            target=_Target((), "none", ()),
            requires=(
                _Input("query", "OrderQuery", ("user_input", "structured_interaction"), "candidate_then_structured"),
            ),
        ),
    )
    coverage = build_typed_goal_capability_coverage(graph=graph, capability_registry=_Registry(capability))
    assert coverage["coverage_status"] == "INCOMPLETE"
    assert coverage["goals"][0]["status"] == "TYPED_COVERED_NEEDS_INTERACTION"


def test_effect_exactness_is_still_required_even_when_target_shape_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "refund.request", reference=_verified_reference())]),
        scope=_scope(),
    )
    wrong_effect = _Capability(
        key="demo.order.details",
        tool_name="get_demo_order",
        semantic_effects=(_semantic_identity("order.details"),),
        planning_contract=_refund_capability().planning_contract,
    )
    coverage = build_typed_goal_capability_coverage(graph=graph, capability_registry=_Registry(wrong_effect))
    assert coverage["goals"][0]["status"] == "UNCOVERED"
    assert coverage["goals"][0]["candidate_proofs"] == []


def test_authorization_and_completion_are_proof_metadata_not_execution_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "refund.request", reference=_verified_reference())]),
        scope=_scope(),
    )
    proof = build_typed_goal_capability_coverage(
        graph=graph, capability_registry=_Registry(_refund_capability())
    )["goals"][0]["candidate_proofs"][0]

    assert proof["authorization"] == {
        "required": True,
        "mode": "structured_interaction",
        "authority": "transaction_authority",
    }
    assert proof["completion"]["mode"] == "transaction_receipt"
    assert proof["execution_authority_granted"] is False
    assert proof["permit_created"] is False


def test_discovery_examples_do_not_affect_typed_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "refund.request", reference=_verified_reference())]),
        scope=_scope(),
    )
    first = _refund_capability()
    second = _Capability(
        **{**first.__dict__, "discovery_examples": ("completely different natural language",)}
    )
    a = build_typed_goal_capability_coverage(graph=graph, capability_registry=_Registry(first))
    b = build_typed_goal_capability_coverage(graph=graph, capability_registry=_Registry(second))
    # Registry/tool identity is equal; natural-language examples are irrelevant to proof.
    assert a["goals"] == b["goals"]


def test_capability_closure_module_is_domain_neutral_and_has_no_business_keywords() -> None:
    import agent_core.goal_graph.capability_closure as module

    source = inspect.getsource(module)
    assert "agent_modules" not in source
    assert "ecommerce" not in source.casefold()
    assert "refund" not in source.casefold()
    assert "order_id" not in source.casefold()


def test_verified_artifact_ref_requires_logical_type_name() -> None:
    from agent_core.goal_graph.contracts import make_verified_artifact_ref

    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "order.collection", cardinality="collection")]),
        scope=_scope(),
    )
    source = graph["source_semantic_contract"]
    with pytest.raises(ValueError, match="VERIFIED_ARTIFACT_TYPE_NAME_REQUIRED"):
        make_verified_artifact_ref(
            artifact_ref="artifact:x",
            type_name="",
            resource_type="order",
            cardinality="exactly_one",
            producer_goal_id="g1",
            scope=_scope(),
            semantic_contract_id=source["semantic_contract_id"],
            semantic_digest=source["semantic_digest"],
            source_ref_id="goal-output:x",
            proof_digest="proof",
        )


def test_verified_context_input_can_be_satisfied_by_exact_typed_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "runtime.transaction_status", subject_type="runtime")]),
        scope=_scope(),
    )
    capability = _Capability(
        key="demo.runtime.status",
        tool_name="query_demo_runtime_status",
        semantic_effects=(_semantic_identity("runtime.transaction_status"),),
        planning_contract=_Planning(
            target=_Target((), "none", ("verified_context",)),
            requires=(
                _Input(
                    "conversation_scope",
                    "VerifiedConversationScope",
                    ("verified_context",),
                    "authoritative",
                ),
            ),
        ),
    )
    evidence = ({
        "verified": True,
        "source_type": "verified_context",
        "type_name": "VerifiedConversationScope",
        "proof_ref": "context-snapshot:83",
        "authority": "authoritative",
        "scope": _scope(),
    },)
    coverage = build_typed_goal_capability_coverage(
        graph=graph,
        capability_registry=_Registry(capability),
        available_input_evidence=evidence,
    )
    proof = coverage["goals"][0]["candidate_proofs"][0]

    assert coverage["coverage_status"] == "INCOMPLETE"
    assert proof["status"] == "BLOCKED_INPUT"
    assert proof["input_proof"]["inputs"][0]["reason"] == "NO_TYPED_INPUT_SOURCE_PROOF"


def test_typed_input_evidence_must_be_verified_scoped_and_exact_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "runtime.transaction_status", subject_type="runtime")]),
        scope=_scope(),
    )
    capability = _Capability(
        key="demo.runtime.status",
        tool_name="query_demo_runtime_status",
        semantic_effects=(_semantic_identity("runtime.transaction_status"),),
        planning_contract=_Planning(
            target=_Target((), "none", ("verified_context",)),
            requires=(
                _Input("conversation_scope", "VerifiedConversationScope", ("verified_context",)),
            ),
        ),
    )
    invalid = (
        {
            "verified": False,
            "source_type": "verified_context",
            "type_name": "VerifiedConversationScope",
            "proof_ref": "context:unverified",
            "authority": "authoritative",
            "scope": _scope(),
        },
        {
            "verified": True,
            "source_type": "verified_context",
            "type_name": "WrongType",
            "proof_ref": "context:wrong-type",
            "authority": "authoritative",
            "scope": _scope(),
        },
        {
            "verified": True,
            "source_type": "verified_context",
            "type_name": "VerifiedConversationScope",
            "proof_ref": "context:wrong-scope",
            "authority": "authoritative",
            "scope": {**_scope(), "thread_id": "other-thread"},
        },
    )
    proof = build_typed_goal_capability_coverage(
        graph=graph,
        capability_registry=_Registry(capability),
        available_input_evidence=invalid,
    )["goals"][0]["candidate_proofs"][0]

    assert proof["status"] == "BLOCKED_INPUT"
    assert proof["input_proof"]["blocking_input_names"] == ["conversation_scope"]


def test_same_turn_data_edge_target_is_treated_as_resolver_proof_not_model_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
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
        type_name="VerifiedOrder",
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
        projection={
            "kind": "verified_member_selection",
            "source_result_ref": "result:orders",
            "member_handle": "artifact:order-10002",
            "proof_digest": "selection-proof",
        },
    )
    graph = with_verified_dataflow_edge(graph, edge)
    capability = _refund_capability(binding_sources=("target_resolver",))
    proof = {
        row["goal_id"]: row for row in build_typed_goal_capability_coverage(
            graph=graph, capability_registry=_Registry(capability)
        )["goals"]
    }["g2"]["candidate_proofs"][0]

    assert proof["target_proof"]["ok"] is True
    assert proof["target_proof"]["evidence"]["selected_source_type"] == "target_resolver"
    assert proof["target_proof"]["evidence"]["provenance"] == "verified_dataflow_edge"
    assert proof["execution_authority_granted"] is False


def test_capability_output_can_supply_both_target_and_exact_typed_required_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors the generic shape used by eligibility -> action promotion."""
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "refund.eligibility", reference=_verified_reference()),
            _goal("g2", "refund.request", depends_on=("g1",), target_candidate={"opaque": "candidate"}),
        ]),
        scope=_scope(),
    )
    goals = {row["goal_id"]: row for row in graph["goals"]}
    goals["g2"]["input_ports"][0]["cardinality"] = "exactly_one"
    graph = seal_goal_graph(graph)
    artifact = make_verified_artifact_ref(
        artifact_ref="artifact:eligibility:1",
        type_name="RefundEligibilityAssessment",
        resource_type="order",
        cardinality="exactly_one",
        producer_goal_id="g1",
        scope=_scope(),
        semantic_contract_id=graph["source_semantic_contract"]["semantic_contract_id"],
        semantic_digest=graph["source_semantic_contract"]["semantic_digest"],
        source_ref_id="goal-output:eligibility:1",
        proof_digest="artifact-proof",
        authority="verified_ledger",
    )
    graph = with_verified_dataflow_edge(
        graph,
        make_verified_dataflow_edge(
            graph=graph,
            producer_goal_id="g1",
            producer_port_id=goals["g1"]["output_ports"][0]["port_id"],
            consumer_goal_id="g2",
            consumer_port_id=goals["g2"]["input_ports"][0]["port_id"],
            artifact_ref=artifact,
            verification_proof_digest="edge-proof",
            projection={
                "kind": "verified_member_selection",
                "source_result_ref": "result:eligible-orders",
                "member_handle": "artifact:eligibility:1",
                "proof_digest": "selection-proof",
            },
        ),
    )
    capability = _Capability(
        key="demo.promote.assessment",
        tool_name="promote_demo_assessment",
        semantic_effects=(_semantic_identity("refund.request"),),
        planning_contract=_Planning(
            target=_Target(("order",), "exactly_one", ("capability_output",)),
            requires=(
                _Input(
                    "eligibility_assessment",
                    "RefundEligibilityAssessment",
                    ("capability_output",),
                    "verified_ledger",
                ),
                _Input(
                    "action_evidence",
                    "CurrentTurnActionEvidence",
                    ("user_input",),
                    "literal_evidence",
                ),
            ),
            authorization=_DictContract(
                {"required": True, "mode": "structured_interaction", "authority": "transaction_authority"}
            ),
            completion=_DictContract(
                {"mode": "transaction_receipt", "proof_type": "RefundReceipt", "proof_source": "transaction_authority", "output_name": None}
            ),
        ),
    )
    proof = {
        row["goal_id"]: row for row in build_typed_goal_capability_coverage(
            graph=graph,
            capability_registry=_Registry(capability),
        )["goals"]
    }["g2"]["candidate_proofs"][0]

    assert proof["target_proof"]["ok"] is True
    assert proof["target_proof"]["evidence"]["selected_source_type"] == "capability_output"
    eligibility = next(row for row in proof["input_proof"]["inputs"] if row["name"] == "eligibility_assessment")
    assert eligibility["status"] == "SATISFIED_BY_TARGET"
    assert proof["status"] == "NEEDS_INTERACTION"


def test_typed_input_authority_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([_goal("g1", "runtime.transaction_status", subject_type="runtime")]),
        scope=_scope(),
    )
    capability = _Capability(
        key="demo.runtime.status.authority",
        tool_name="query_demo_runtime_status_authority",
        semantic_effects=(_semantic_identity("runtime.transaction_status"),),
        planning_contract=_Planning(
            target=_Target((), "none", ("verified_context",)),
            requires=(
                _Input("conversation_scope", "VerifiedConversationScope", ("verified_context",), "authoritative"),
            ),
        ),
    )
    evidence = ({
        "verified": True,
        "source_type": "verified_context",
        "type_name": "VerifiedConversationScope",
        "authority": "candidate",
        "proof_ref": "context:wrong-authority",
        "scope": _scope(),
    },)
    proof = build_typed_goal_capability_coverage(
        graph=graph,
        capability_registry=_Registry(capability),
        available_input_evidence=evidence,
    )["goals"][0]["candidate_proofs"][0]

    assert proof["status"] == "BLOCKED_INPUT"
    row = proof["input_proof"]["inputs"][0]
    assert row["reason"] == "NO_TYPED_INPUT_SOURCE_PROOF"
    assert row["evidence_authority"] is None


def test_upstream_output_authority_must_match_required_input_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "refund.eligibility", reference=_verified_reference()),
            _goal("g2", "refund.request", depends_on=("g1",), reference=_verified_reference()),
        ]),
        scope=_scope(),
    )
    g2 = next(row for row in graph["goals"] if row["goal_id"] == "g2")
    g2["input_ports"].append(make_goal_port(
        goal_id="g2", name="eligibility_assessment", direction="input",
        type_name="order", cardinality="exactly_one", required=True,
    ))
    graph = seal_goal_graph(graph)
    goals = {row["goal_id"]: row for row in graph["goals"]}
    artifact = make_verified_artifact_ref(
        artifact_ref="artifact:eligibility:authority",
        type_name="RefundEligibilityAssessment",
        resource_type="order",
        cardinality="exactly_one",
        producer_goal_id="g1",
        scope=_scope(),
        semantic_contract_id=graph["source_semantic_contract"]["semantic_contract_id"],
        semantic_digest=graph["source_semantic_contract"]["semantic_digest"],
        source_ref_id="goal-output:eligibility:authority",
        proof_digest="artifact-proof",
        authority="verified_tool_output",
    )
    graph = with_verified_dataflow_edge(graph, make_verified_dataflow_edge(
        graph=graph,
        producer_goal_id="g1",
        producer_port_id=goals["g1"]["output_ports"][0]["port_id"],
        consumer_goal_id="g2",
        consumer_port_id=next(row["port_id"] for row in goals["g2"]["input_ports"] if row["name"] == "eligibility_assessment"),
        artifact_ref=artifact,
        verification_proof_digest="edge-proof",
    ))
    capability = _refund_capability(extra_inputs=(
        _Input("eligibility_assessment", "RefundEligibilityAssessment", ("capability_output",), "verified_ledger"),
    ))
    proof = {row["goal_id"]: row for row in build_typed_goal_capability_coverage(
        graph=graph, capability_registry=_Registry(capability)
    )["goals"]}["g2"]["candidate_proofs"][0]

    assert proof["status"] == "BLOCKED_INPUT"
    typed_input = next(row for row in proof["input_proof"]["inputs"] if row["name"] == "eligibility_assessment")
    assert typed_input["reason"] == "UPSTREAM_INPUT_AUTHORITY_MISMATCH"


def _fresh_eligibility_graph(*, expires_at: float | None) -> dict:
    graph = compile_frozen_semantic_contract(
        _contract([
            _goal("g1", "refund.eligibility", reference=_verified_reference()),
            _goal("g2", "refund.request", depends_on=("g1",), reference=_verified_reference()),
        ]),
        scope=_scope(),
    )
    g2 = next(row for row in graph["goals"] if row["goal_id"] == "g2")
    g2["input_ports"].append(make_goal_port(
        goal_id="g2", name="eligibility_assessment", direction="input",
        type_name="order", cardinality="exactly_one", required=True,
    ))
    graph = seal_goal_graph(graph)
    goals = {row["goal_id"]: row for row in graph["goals"]}
    kwargs = {} if expires_at is None else {"expires_at": expires_at}
    artifact = make_verified_artifact_ref(
        artifact_ref="artifact:eligibility:freshness",
        type_name="RefundEligibilityAssessment",
        resource_type="order",
        cardinality="exactly_one",
        producer_goal_id="g1",
        scope=_scope(),
        semantic_contract_id=graph["source_semantic_contract"]["semantic_contract_id"],
        semantic_digest=graph["source_semantic_contract"]["semantic_digest"],
        source_ref_id="goal-output:eligibility:freshness",
        proof_digest="artifact-proof",
        authority="verified_ledger",
        **kwargs,
    )
    return with_verified_dataflow_edge(graph, make_verified_dataflow_edge(
        graph=graph,
        producer_goal_id="g1",
        producer_port_id=goals["g1"]["output_ports"][0]["port_id"],
        consumer_goal_id="g2",
        consumer_port_id=next(row["port_id"] for row in goals["g2"]["input_ports"] if row["name"] == "eligibility_assessment"),
        artifact_ref=artifact,
        verification_proof_digest="edge-proof",
    ))


def _freshness_capability() -> _Capability:
    return _refund_capability(extra_inputs=(
        _Input(
            "eligibility_assessment",
            "RefundEligibilityAssessment",
            ("capability_output",),
            "verified_ledger",
            True,
            300,
        ),
    ))


def test_freshness_required_input_requires_explicit_evaluation_time(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = _fresh_eligibility_graph(expires_at=2000.0)
    proof = {row["goal_id"]: row for row in build_typed_goal_capability_coverage(
        graph=graph, capability_registry=_Registry(_freshness_capability())
    )["goals"]}["g2"]["candidate_proofs"][0]
    typed_input = next(row for row in proof["input_proof"]["inputs"] if row["name"] == "eligibility_assessment")
    assert proof["status"] == "BLOCKED_INPUT"
    assert typed_input["reason"] == "FRESHNESS_EVALUATION_TIME_REQUIRED"


def test_freshness_required_input_rejects_expired_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = _fresh_eligibility_graph(expires_at=1000.0)
    proof = {row["goal_id"]: row for row in build_typed_goal_capability_coverage(
        graph=graph, capability_registry=_Registry(_freshness_capability()), evaluation_time=1000.0
    )["goals"]}["g2"]["candidate_proofs"][0]
    typed_input = next(row for row in proof["input_proof"]["inputs"] if row["name"] == "eligibility_assessment")
    assert proof["status"] == "BLOCKED_INPUT"
    assert typed_input["reason"] == "INPUT_EVIDENCE_EXPIRED"


def test_freshness_required_input_accepts_unexpired_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_effects(monkeypatch)
    graph = _fresh_eligibility_graph(expires_at=1301.0)
    proof = {row["goal_id"]: row for row in build_typed_goal_capability_coverage(
        graph=graph, capability_registry=_Registry(_freshness_capability()), evaluation_time=1000.0
    )["goals"]}["g2"]["candidate_proofs"][0]
    typed_input = next(row for row in proof["input_proof"]["inputs"] if row["name"] == "eligibility_assessment")
    assert typed_input["status"] == "SATISFIED_BY_UPSTREAM_OUTPUT"
    assert typed_input["evidence_expires_at"] == 1301.0
