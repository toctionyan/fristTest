from __future__ import annotations

"""Domain-neutral contracts for the shadow typed Goal Graph foundation.

The contracts in this package are deliberately non-executable. They describe
verified semantic/dataflow structure without replacing the FrozenSemanticContract,
Artifact Ledger, existing runtime GoalOutputRef contract, CapabilityGate,
transaction authority, or Business Service.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Iterable

CANONICAL_GOAL_GRAPH_VERSION = "canonical-goal-graph@1"
GOAL_PORT_VERSION = "goal-port@1"
TYPED_TARGET_BINDING_VERSION = "typed-target-binding@1"
TYPED_DATAFLOW_EDGE_VERSION = "typed-dataflow-edge@1"
SEMANTIC_DEPENDENCY_EDGE_VERSION = "semantic-dependency-edge@1"
VERIFIED_ARTIFACT_REF_VERSION = "verified-artifact-ref@1"

_CARDINALITY_ALIASES = {
    "": "unknown",
    "unknown": "unknown",
    "unspecified": "unknown",
    "none": "none",
    "zero": "none",
    "single": "exactly_one",
    "one": "exactly_one",
    "exactly_one": "exactly_one",
    "one_or_collection": "one_or_collection",
    "collection": "collection",
    "many": "collection",
    "set": "collection",
}


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def normalize_scope(scope: dict[str, Any] | None) -> dict[str, str]:
    source = scope if isinstance(scope, dict) else {}
    return {
        "tenant_id": _text(source.get("tenant_id"), limit=300),
        "user_id": _text(source.get("user_id"), limit=300),
        "thread_id": _text(source.get("thread_id"), limit=500),
    }


def normalize_cardinality(value: Any) -> str:
    return _CARDINALITY_ALIASES.get(_text(value, limit=80).casefold(), "unknown")


def requested_effect_subject_type(requested_effect: dict[str, Any] | None) -> str:
    source = requested_effect if isinstance(requested_effect, dict) else {}
    return _text(
        source.get("subject_type") or source.get("object_type") or "unspecified",
        limit=200,
    ).casefold() or "unspecified"


def requested_output_ids(requested_effect: dict[str, Any] | None) -> list[str]:
    source = requested_effect if isinstance(requested_effect, dict) else {}
    outputs = source.get("requested_outputs")
    if not isinstance(outputs, list):
        return []
    result: list[str] = []
    for row in outputs:
        if not isinstance(row, dict):
            continue
        output_id = _text(row.get("output_id"), limit=240).casefold()
        if output_id and output_id not in result:
            result.append(output_id)
    return result


def make_goal_port(
    *,
    goal_id: str,
    name: str,
    direction: str,
    type_name: str,
    cardinality: str,
    required: bool,
    semantic_output_id: str | None = None,
) -> dict[str, Any]:
    normalized_direction = _text(direction, limit=20).casefold()
    if normalized_direction not in {"input", "output"}:
        raise ValueError("GOAL_PORT_DIRECTION_INVALID")
    normalized_goal_id = _text(goal_id, limit=200)
    normalized_name = _text(name, limit=240)
    if not normalized_goal_id or not normalized_name:
        raise ValueError("GOAL_PORT_IDENTITY_REQUIRED")
    port = {
        "version": GOAL_PORT_VERSION,
        "port_id": f"{normalized_goal_id}:{normalized_direction}:{normalized_name}",
        "goal_id": normalized_goal_id,
        "name": normalized_name,
        "direction": normalized_direction,
        "type_name": _text(type_name, limit=200).casefold() or "unspecified",
        "cardinality": normalize_cardinality(cardinality),
        "required": bool(required),
    }
    semantic_id = _text(semantic_output_id, limit=240).casefold()
    if semantic_id:
        port["semantic_output_id"] = semantic_id
    return port


def make_unresolved_target_binding(
    *,
    goal_id: str,
    resource_type: str,
    cardinality: str,
    scope: dict[str, Any] | None,
    semantic_contract_id: str,
    semantic_digest: str,
    reason_code: str,
) -> dict[str, Any]:
    return {
        "version": TYPED_TARGET_BINDING_VERSION,
        "status": "UNRESOLVED",
        "verified": False,
        "reason_code": _text(reason_code, limit=200) or "TARGET_UNRESOLVED",
        "goal_id": _text(goal_id, limit=200),
        "resource_type": _text(resource_type, limit=200).casefold() or "unspecified",
        "cardinality": normalize_cardinality(cardinality),
        "scope": normalize_scope(scope),
        "semantic_contract_id": _text(semantic_contract_id, limit=500),
        "semantic_digest": _text(semantic_digest, limit=128),
        "provenance": {
            "source": "frozen_semantic_contract",
            "authority": "semantic_claim_only",
        },
    }


def make_verified_historical_target_binding(
    *,
    goal_id: str,
    resource_type: str,
    result_ref: str,
    member_handles: Iterable[str],
    proof_digest: str,
    scope: dict[str, Any] | None,
    semantic_contract_id: str,
    semantic_digest: str,
    position: int | None = None,
) -> dict[str, Any]:
    members = [str(value) for value in member_handles if str(value)]
    if not members:
        raise ValueError("TARGET_BINDING_MEMBER_REQUIRED")
    if not _text(result_ref, limit=500) or not _text(proof_digest, limit=256):
        raise ValueError("TARGET_BINDING_PROOF_REQUIRED")
    cardinality = "exactly_one" if len(members) == 1 else "collection"
    binding: dict[str, Any] = {
        "version": TYPED_TARGET_BINDING_VERSION,
        "status": "VERIFIED",
        "verified": True,
        "reason_code": "FROZEN_REFERENCE_PROOF_VERIFIED",
        "goal_id": _text(goal_id, limit=200),
        "resource_type": _text(resource_type, limit=200).casefold() or "unspecified",
        "cardinality": cardinality,
        "binding_source": "visible_result_ref",
        "result_ref": _text(result_ref, limit=500),
        "member_handles": members,
        "proof_digest": _text(proof_digest, limit=256),
        "scope": normalize_scope(scope),
        "semantic_contract_id": _text(semantic_contract_id, limit=500),
        "semantic_digest": _text(semantic_digest, limit=128),
        "provenance": {
            "source": "frozen.resolved_reference",
            "authority": "validated_historical_reference",
        },
    }
    if isinstance(position, int) and not isinstance(position, bool):
        binding["position"] = int(position)
    unsigned = deepcopy(binding)
    binding["binding_digest"] = canonical_digest(unsigned)
    return binding


_ARTIFACT_REF_FORBIDDEN_PAYLOAD_KEYS = {
    "facts",
    "business_facts",
    "data",
    "payload",
    "preview",
    "raw_result",
    "customer_safe_summary",
}


def artifact_ref_contains_business_payload(ref: dict[str, Any] | None) -> bool:
    if not isinstance(ref, dict):
        return True

    def contains(value: Any) -> bool:
        if isinstance(value, dict):
            if any(str(key) in _ARTIFACT_REF_FORBIDDEN_PAYLOAD_KEYS for key in value):
                return True
            return any(contains(child) for child in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains(child) for child in value)
        return False

    return contains(ref)


def make_verified_artifact_ref(
    *,
    artifact_ref: str,
    type_name: str,
    resource_type: str,
    cardinality: str,
    producer_goal_id: str,
    scope: dict[str, Any] | None,
    semantic_contract_id: str,
    semantic_digest: str,
    source_ref_id: str,
    proof_digest: str,
    authority: str = "verified_tool_output",
    expires_at: float | None = None,
) -> dict[str, Any]:
    if not _text(artifact_ref, limit=500):
        raise ValueError("VERIFIED_ARTIFACT_REF_REQUIRED")
    normalized_type_name = _text(type_name, limit=240)
    normalized_resource_type = _text(resource_type, limit=200).casefold() or "unspecified"
    normalized_cardinality = normalize_cardinality(cardinality)
    normalized_authority = _text(authority, limit=200)
    if not normalized_type_name:
        raise ValueError("VERIFIED_ARTIFACT_TYPE_NAME_REQUIRED")
    if normalized_resource_type == "unspecified":
        raise ValueError("VERIFIED_ARTIFACT_RESOURCE_TYPE_REQUIRED")
    if normalized_cardinality not in {"exactly_one", "collection"}:
        raise ValueError("VERIFIED_ARTIFACT_CARDINALITY_REQUIRED")
    if not normalized_authority:
        raise ValueError("VERIFIED_ARTIFACT_AUTHORITY_REQUIRED")
    if not _text(source_ref_id, limit=500) or not _text(proof_digest, limit=256):
        raise ValueError("VERIFIED_ARTIFACT_PROVENANCE_REQUIRED")
    ref = {
        "version": VERIFIED_ARTIFACT_REF_VERSION,
        "status": "VERIFIED",
        "verified": True,
        "artifact_ref": _text(artifact_ref, limit=500),
        "type_name": normalized_type_name,
        "resource_type": normalized_resource_type,
        "cardinality": normalized_cardinality,
        "authority": normalized_authority,
        "producer_goal_id": _text(producer_goal_id, limit=200),
        "scope": normalize_scope(scope),
        "semantic_contract_id": _text(semantic_contract_id, limit=500),
        "semantic_digest": _text(semantic_digest, limit=128),
        "source_ref_id": _text(source_ref_id, limit=500),
        "proof_digest": _text(proof_digest, limit=256),
        "provenance": {
            "source": "verified_runtime_artifact_pointer",
            "business_facts_copied": False,
        },
    }
    if expires_at is not None:
        try:
            normalized_expires_at = float(expires_at)
        except (TypeError, ValueError):
            raise ValueError("VERIFIED_ARTIFACT_EXPIRY_INVALID")
        if normalized_expires_at <= 0:
            raise ValueError("VERIFIED_ARTIFACT_EXPIRY_INVALID")
        ref["expires_at"] = normalized_expires_at
    unsigned = deepcopy(ref)
    ref["ref_digest"] = canonical_digest(unsigned)
    return ref


def make_verified_dataflow_edge(
    *,
    graph: dict[str, Any],
    producer_goal_id: str,
    producer_port_id: str,
    consumer_goal_id: str,
    consumer_port_id: str,
    artifact_ref: dict[str, Any],
    verification_proof_digest: str,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _text(verification_proof_digest, limit=256):
        raise ValueError("DATAFLOW_EDGE_PROOF_REQUIRED")
    source = graph.get("source_semantic_contract") if isinstance(graph.get("source_semantic_contract"), dict) else {}
    edge_base = {
        "version": TYPED_DATAFLOW_EDGE_VERSION,
        "verified": True,
        "producer_goal_id": _text(producer_goal_id, limit=200),
        "producer_port_id": _text(producer_port_id, limit=500),
        "consumer_goal_id": _text(consumer_goal_id, limit=200),
        "consumer_port_id": _text(consumer_port_id, limit=500),
        "artifact_ref": deepcopy(artifact_ref),
        "scope": normalize_scope(graph.get("scope") if isinstance(graph.get("scope"), dict) else {}),
        "semantic_contract_id": _text(source.get("semantic_contract_id"), limit=500),
        "semantic_digest": _text(source.get("semantic_digest"), limit=128),
        "verification_proof_digest": _text(verification_proof_digest, limit=256),
        "projection": deepcopy(projection or {"kind": "identity"}),
    }
    identity = canonical_digest(edge_base)
    edge = deepcopy(edge_base)
    edge["edge_id"] = f"dataflow:{identity[:24]}"
    return edge


def make_semantic_dependency_edge(
    *,
    graph: dict[str, Any],
    producer_goal_id: str,
    producer_port_id: str,
    consumer_goal_id: str,
    consumer_port_id: str,
    source_kind: str,
    relation_kind: str,
    evidence_span: str,
    source_proof_digest: str,
) -> dict[str, Any]:
    """Create a sealed symbolic edge without claiming a runtime artifact exists."""

    normalized_source = _text(source_kind, limit=80)
    normalized_relation = _text(relation_kind, limit=80)
    normalized_proof = _text(source_proof_digest, limit=256)
    if normalized_source not in {"current_goal_output", "condition_goal_output"}:
        raise ValueError("SEMANTIC_DEPENDENCY_SOURCE_INVALID")
    if normalized_relation not in {"result_reference", "result_value_input", "result_condition"}:
        raise ValueError("SEMANTIC_DEPENDENCY_RELATION_INVALID")
    if not normalized_proof:
        raise ValueError("SEMANTIC_DEPENDENCY_PROOF_REQUIRED")
    source = graph.get("source_semantic_contract") if isinstance(graph.get("source_semantic_contract"), dict) else {}
    edge_base = {
        "version": SEMANTIC_DEPENDENCY_EDGE_VERSION,
        "verified": True,
        "symbolic_only": True,
        "producer_goal_id": _text(producer_goal_id, limit=200),
        "producer_port_id": _text(producer_port_id, limit=500),
        "consumer_goal_id": _text(consumer_goal_id, limit=200),
        "consumer_port_id": _text(consumer_port_id, limit=500),
        "source_kind": normalized_source,
        "relation_kind": normalized_relation,
        "evidence_span": _text(evidence_span, limit=500),
        "source_proof_digest": normalized_proof,
        "scope": normalize_scope(graph.get("scope") if isinstance(graph.get("scope"), dict) else {}),
        "semantic_contract_id": _text(source.get("semantic_contract_id"), limit=500),
        "semantic_digest": _text(source.get("semantic_digest"), limit=128),
        "runtime_artifact_present": False,
        "execution_authority_granted": False,
    }
    identity = canonical_digest(edge_base)
    edge = deepcopy(edge_base)
    edge["edge_id"] = f"semantic-dependency:{identity[:24]}"
    return edge


def _graph_digest_payload(graph: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(graph)
    payload.pop("graph_digest", None)
    payload.pop("graph_id", None)
    return payload


def seal_goal_graph(graph: dict[str, Any]) -> dict[str, Any]:
    sealed = deepcopy(graph)
    sealed["version"] = CANONICAL_GOAL_GRAPH_VERSION
    sealed["immutable"] = True
    digest = canonical_digest(_graph_digest_payload(sealed))
    sealed["graph_digest"] = digest
    turn = int((sealed.get("source_semantic_contract") or {}).get("turn") or 0)
    sealed["graph_id"] = f"goal-graph:{turn}:{digest[:20]}"
    return sealed


def with_verified_dataflow_edge(graph: dict[str, Any], edge: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(graph)
    updated["edges"] = [
        deepcopy(row) for row in list(updated.get("edges") or []) if isinstance(row, dict)
    ]
    updated["edges"].append(deepcopy(edge))
    return seal_goal_graph(updated)


__all__ = [
    "CANONICAL_GOAL_GRAPH_VERSION",
    "GOAL_PORT_VERSION",
    "TYPED_TARGET_BINDING_VERSION",
    "TYPED_DATAFLOW_EDGE_VERSION",
    "SEMANTIC_DEPENDENCY_EDGE_VERSION",
    "VERIFIED_ARTIFACT_REF_VERSION",
    "artifact_ref_contains_business_payload",
    "canonical_digest",
    "make_goal_port",
    "make_semantic_dependency_edge",
    "make_unresolved_target_binding",
    "make_verified_artifact_ref",
    "make_verified_dataflow_edge",
    "make_verified_historical_target_binding",
    "normalize_cardinality",
    "normalize_scope",
    "requested_effect_subject_type",
    "requested_output_ids",
    "seal_goal_graph",
    "with_verified_dataflow_edge",
]
