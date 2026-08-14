from __future__ import annotations

"""Deterministic structural and dataflow-closure verification for Goal Graphs."""

from copy import deepcopy
from typing import Any

from agent_core.kernel.semantic_contract import (
    FROZEN_SEMANTIC_CONTRACT_VERSION,
    semantic_contract_integrity,
)

from .contracts import (
    CANONICAL_GOAL_GRAPH_VERSION,
    GOAL_PORT_VERSION,
    TYPED_DATAFLOW_EDGE_VERSION,
    TYPED_TARGET_BINDING_VERSION,
    VERIFIED_ARTIFACT_REF_VERSION,
    artifact_ref_contains_business_payload,
    canonical_digest,
    normalize_cardinality,
    normalize_scope,
)


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _result(*, ok: bool, code: str, errors: list[str], **extra: Any) -> dict[str, Any]:
    payload = {"ok": bool(ok), "code": code, "errors": list(dict.fromkeys(errors))}
    payload.update(extra)
    return payload


def _expected_graph_digest(graph: dict[str, Any]) -> tuple[str, str]:
    payload = deepcopy(graph)
    payload.pop("graph_digest", None)
    payload.pop("graph_id", None)
    digest = canonical_digest(payload)
    turn = int((graph.get("source_semantic_contract") or {}).get("turn") or 0)
    return digest, f"goal-graph:{turn}:{digest[:20]}"


def _goal_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for goal in list(graph.get("goals") or []):
        if not isinstance(goal, dict):
            continue
        goal_id = _text(goal.get("goal_id"), limit=200)
        if goal_id and goal_id not in result:
            result[goal_id] = goal
    return result


def _port_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for goal in list(graph.get("goals") or []):
        if not isinstance(goal, dict):
            continue
        for key in ("input_ports", "output_ports"):
            for port in list(goal.get(key) or []):
                if not isinstance(port, dict):
                    continue
                port_id = _text(port.get("port_id"), limit=500)
                if port_id and port_id not in result:
                    result[port_id] = port
    return result


def _artifact_ref_errors(
    ref: dict[str, Any],
    *,
    graph: dict[str, Any],
    producer_goal_id: str,
) -> list[str]:
    errors: list[str] = []
    if str(ref.get("version") or "") != VERIFIED_ARTIFACT_REF_VERSION:
        return ["VERIFIED_ARTIFACT_REF_VERSION_INVALID"]
    if not bool(ref.get("verified")) or str(ref.get("status") or "") != "VERIFIED":
        errors.append("VERIFIED_ARTIFACT_REF_NOT_VERIFIED")
    if artifact_ref_contains_business_payload(ref):
        errors.append("VERIFIED_ARTIFACT_REF_COPIES_BUSINESS_FACTS")
    if not _text(ref.get("artifact_ref"), limit=500):
        errors.append("VERIFIED_ARTIFACT_REF_POINTER_REQUIRED")
    if not _text(ref.get("type_name"), limit=240):
        errors.append("VERIFIED_ARTIFACT_REF_TYPE_NAME_UNPROVEN")
    if not _text(ref.get("authority"), limit=200):
        errors.append("VERIFIED_ARTIFACT_REF_AUTHORITY_UNPROVEN")
    resource_type = _text(ref.get("resource_type"), limit=200).casefold() or "unspecified"
    if resource_type == "unspecified":
        errors.append("VERIFIED_ARTIFACT_REF_RESOURCE_TYPE_UNPROVEN")
    cardinality = normalize_cardinality(ref.get("cardinality"))
    if cardinality not in {"exactly_one", "collection"}:
        errors.append("VERIFIED_ARTIFACT_REF_CARDINALITY_UNPROVEN")
    if not _text(ref.get("source_ref_id"), limit=500) or not _text(ref.get("proof_digest"), limit=256):
        errors.append("VERIFIED_ARTIFACT_REF_PROVENANCE_REQUIRED")
    if ref.get("expires_at") is not None:
        try:
            if float(ref.get("expires_at")) <= 0:
                errors.append("VERIFIED_ARTIFACT_REF_EXPIRY_INVALID")
        except (TypeError, ValueError):
            errors.append("VERIFIED_ARTIFACT_REF_EXPIRY_INVALID")
    if _text(ref.get("producer_goal_id"), limit=200) != producer_goal_id:
        errors.append("VERIFIED_ARTIFACT_REF_PRODUCER_MISMATCH")
    if normalize_scope(ref.get("scope") if isinstance(ref.get("scope"), dict) else {}) != normalize_scope(
        graph.get("scope") if isinstance(graph.get("scope"), dict) else {}
    ):
        errors.append("VERIFIED_ARTIFACT_REF_SCOPE_MISMATCH")
    source = graph.get("source_semantic_contract") if isinstance(graph.get("source_semantic_contract"), dict) else {}
    if _text(ref.get("semantic_contract_id"), limit=500) != _text(source.get("semantic_contract_id"), limit=500):
        errors.append("VERIFIED_ARTIFACT_REF_SEMANTIC_CONTRACT_MISMATCH")
    if _text(ref.get("semantic_digest"), limit=128) != _text(source.get("semantic_digest"), limit=128):
        errors.append("VERIFIED_ARTIFACT_REF_SEMANTIC_DIGEST_MISMATCH")
    unsigned = deepcopy(ref)
    stored = _text(unsigned.pop("ref_digest", None), limit=128)
    if not stored or canonical_digest(unsigned) != stored:
        errors.append("VERIFIED_ARTIFACT_REF_DIGEST_INVALID")
    return errors


def _target_binding_errors(
    binding: dict[str, Any],
    *,
    graph: dict[str, Any],
    goal_id: str,
    target_port: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    if str(binding.get("version") or "") != TYPED_TARGET_BINDING_VERSION:
        return ["TARGET_BINDING_VERSION_INVALID"]
    if _text(binding.get("goal_id"), limit=200) != goal_id:
        errors.append("TARGET_BINDING_GOAL_MISMATCH")
    if normalize_scope(binding.get("scope") if isinstance(binding.get("scope"), dict) else {}) != normalize_scope(
        graph.get("scope") if isinstance(graph.get("scope"), dict) else {}
    ):
        errors.append("TARGET_BINDING_SCOPE_MISMATCH")
    source = graph.get("source_semantic_contract") if isinstance(graph.get("source_semantic_contract"), dict) else {}
    if _text(binding.get("semantic_contract_id"), limit=500) != _text(source.get("semantic_contract_id"), limit=500):
        errors.append("TARGET_BINDING_SEMANTIC_CONTRACT_MISMATCH")
    if _text(binding.get("semantic_digest"), limit=128) != _text(source.get("semantic_digest"), limit=128):
        errors.append("TARGET_BINDING_SEMANTIC_DIGEST_MISMATCH")

    if bool(binding.get("verified")):
        if str(binding.get("status") or "") != "VERIFIED":
            errors.append("TARGET_BINDING_STATUS_INVALID")
        if not _text(binding.get("binding_source"), limit=120):
            errors.append("TARGET_BINDING_SOURCE_REQUIRED")
        if not _text(binding.get("result_ref"), limit=500):
            errors.append("TARGET_BINDING_RESULT_REF_REQUIRED")
        members = [str(value) for value in list(binding.get("member_handles") or []) if str(value)]
        if not members:
            errors.append("TARGET_BINDING_MEMBER_REQUIRED")
        if not _text(binding.get("proof_digest"), limit=256):
            errors.append("TARGET_BINDING_PROOF_REQUIRED")
        unsigned = deepcopy(binding)
        stored = _text(unsigned.pop("binding_digest", None), limit=128)
        if not stored or canonical_digest(unsigned) != stored:
            errors.append("TARGET_BINDING_DIGEST_INVALID")
        if target_port is None:
            errors.append("TARGET_BINDING_PORT_MISSING")
        else:
            binding_type = _text(binding.get("resource_type"), limit=200).casefold() or "unspecified"
            port_type = _text(target_port.get("type_name"), limit=200).casefold() or "unspecified"
            if port_type != "unspecified" and binding_type != "unspecified" and port_type != binding_type:
                errors.append("TARGET_BINDING_TYPE_MISMATCH")
            if not _port_accepts_artifact(target_port.get("cardinality"), binding.get("cardinality")):
                errors.append("TARGET_BINDING_CARDINALITY_MISMATCH")
    return errors


def _edge_projection_allows_cardinality(
    *,
    producer_cardinality: str,
    artifact_cardinality: str,
    projection: dict[str, Any],
    artifact_pointer: str,
) -> bool:
    if producer_cardinality in {"unknown", "one_or_collection", artifact_cardinality}:
        return True
    if producer_cardinality == "collection" and artifact_cardinality == "exactly_one":
        return (
            str(projection.get("kind") or "") == "verified_member_selection"
            and bool(_text(projection.get("proof_digest"), limit=256))
            and bool(_text(projection.get("source_result_ref"), limit=500))
            and _text(projection.get("member_handle"), limit=500) == artifact_pointer
        )
    return False


def _port_accepts_artifact(port_cardinality: Any, artifact_cardinality: Any) -> bool:
    port = normalize_cardinality(port_cardinality)
    artifact = normalize_cardinality(artifact_cardinality)
    if port in {"unknown", "one_or_collection"}:
        return artifact in {"exactly_one", "collection"}
    return port == artifact


def _edge_errors(
    edge: dict[str, Any],
    *,
    graph: dict[str, Any],
    ports: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if str(edge.get("version") or "") != TYPED_DATAFLOW_EDGE_VERSION:
        return ["DATAFLOW_EDGE_VERSION_INVALID"]
    unsigned_edge = deepcopy(edge)
    stored_edge_id = _text(unsigned_edge.pop("edge_id", None), limit=500)
    expected_edge_id = f"dataflow:{canonical_digest(unsigned_edge)[:24]}"
    if not stored_edge_id or stored_edge_id != expected_edge_id:
        errors.append("DATAFLOW_EDGE_ID_INVALID")
    if not bool(edge.get("verified")):
        errors.append("DATAFLOW_EDGE_NOT_VERIFIED")
    if not _text(edge.get("verification_proof_digest"), limit=256):
        errors.append("DATAFLOW_EDGE_PROOF_REQUIRED")
    if normalize_scope(edge.get("scope") if isinstance(edge.get("scope"), dict) else {}) != normalize_scope(
        graph.get("scope") if isinstance(graph.get("scope"), dict) else {}
    ):
        errors.append("DATAFLOW_EDGE_SCOPE_MISMATCH")
    source = graph.get("source_semantic_contract") if isinstance(graph.get("source_semantic_contract"), dict) else {}
    if _text(edge.get("semantic_contract_id"), limit=500) != _text(source.get("semantic_contract_id"), limit=500):
        errors.append("DATAFLOW_EDGE_SEMANTIC_CONTRACT_MISMATCH")
    if _text(edge.get("semantic_digest"), limit=128) != _text(source.get("semantic_digest"), limit=128):
        errors.append("DATAFLOW_EDGE_SEMANTIC_DIGEST_MISMATCH")

    producer_port = ports.get(_text(edge.get("producer_port_id"), limit=500))
    consumer_port = ports.get(_text(edge.get("consumer_port_id"), limit=500))
    if producer_port is None:
        errors.append("DATAFLOW_EDGE_PRODUCER_PORT_MISSING")
    if consumer_port is None:
        errors.append("DATAFLOW_EDGE_CONSUMER_PORT_MISSING")
    if producer_port is not None and str(producer_port.get("direction") or "") != "output":
        errors.append("DATAFLOW_EDGE_PRODUCER_PORT_DIRECTION_INVALID")
    if consumer_port is not None and str(consumer_port.get("direction") or "") != "input":
        errors.append("DATAFLOW_EDGE_CONSUMER_PORT_DIRECTION_INVALID")

    producer_goal_id = _text(edge.get("producer_goal_id"), limit=200)
    consumer_goal_id = _text(edge.get("consumer_goal_id"), limit=200)
    if producer_port is not None and _text(producer_port.get("goal_id"), limit=200) != producer_goal_id:
        errors.append("DATAFLOW_EDGE_PRODUCER_GOAL_MISMATCH")
    if consumer_port is not None and _text(consumer_port.get("goal_id"), limit=200) != consumer_goal_id:
        errors.append("DATAFLOW_EDGE_CONSUMER_GOAL_MISMATCH")

    artifact = edge.get("artifact_ref") if isinstance(edge.get("artifact_ref"), dict) else {}
    errors.extend(_artifact_ref_errors(artifact, graph=graph, producer_goal_id=producer_goal_id))
    if producer_port is not None and consumer_port is not None and artifact:
        artifact_pointer = _text(artifact.get("artifact_ref"), limit=500)
        artifact_type = _text(artifact.get("resource_type"), limit=200).casefold() or "unspecified"
        producer_type = _text(producer_port.get("type_name"), limit=200).casefold() or "unspecified"
        consumer_type = _text(consumer_port.get("type_name"), limit=200).casefold() or "unspecified"
        if producer_type != "unspecified" and artifact_type != "unspecified" and producer_type != artifact_type:
            errors.append("DATAFLOW_EDGE_PRODUCER_TYPE_MISMATCH")
        if consumer_type != "unspecified" and artifact_type != "unspecified" and consumer_type != artifact_type:
            errors.append("DATAFLOW_EDGE_CONSUMER_TYPE_MISMATCH")
        artifact_cardinality = normalize_cardinality(artifact.get("cardinality"))
        projection = edge.get("projection") if isinstance(edge.get("projection"), dict) else {}
        if not _edge_projection_allows_cardinality(
            producer_cardinality=normalize_cardinality(producer_port.get("cardinality")),
            artifact_cardinality=artifact_cardinality,
            projection=projection,
            artifact_pointer=artifact_pointer,
        ):
            errors.append("DATAFLOW_EDGE_PRODUCER_CARDINALITY_MISMATCH")
        if not _port_accepts_artifact(consumer_port.get("cardinality"), artifact_cardinality):
            errors.append("DATAFLOW_EDGE_CONSUMER_CARDINALITY_MISMATCH")
    return errors


def _edge_cycle(edges: list[dict[str, Any]], known_goals: set[str]) -> list[str]:
    graph: dict[str, list[str]] = {goal_id: [] for goal_id in known_goals}
    for edge in edges:
        producer = _text(edge.get("producer_goal_id"), limit=200)
        consumer = _text(edge.get("consumer_goal_id"), limit=200)
        if producer in known_goals and consumer in known_goals and consumer not in graph[producer]:
            graph[producer].append(consumer)
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> list[str]:
        if node in visiting:
            index = stack.index(node)
            return [*stack[index:], node]
        if node in visited:
            return []
        visiting.add(node)
        stack.append(node)
        for child in sorted(graph.get(node, [])):
            cycle = visit(child)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in sorted(graph):
        cycle = visit(node)
        if cycle:
            return cycle
    return []


def _source_identity_errors(
    graph: dict[str, Any],
    *,
    frozen_contract: dict[str, Any] | None,
) -> list[str]:
    source = graph.get("source_semantic_contract") if isinstance(graph.get("source_semantic_contract"), dict) else {}
    errors: list[str] = []
    if str(source.get("version") or "") != FROZEN_SEMANTIC_CONTRACT_VERSION:
        errors.append("GOAL_GRAPH_SOURCE_SEMANTIC_VERSION_INVALID")
    if not _text(source.get("semantic_contract_id"), limit=500) or not _text(source.get("semantic_digest"), limit=128):
        errors.append("GOAL_GRAPH_SEMANTIC_IDENTITY_REQUIRED")
    if frozen_contract is None:
        return errors
    integrity = semantic_contract_integrity(frozen_contract)
    if not integrity.get("ok"):
        errors.append(f"GOAL_GRAPH_SOURCE_SEMANTIC_INVALID:{integrity.get('code') or 'UNKNOWN'}")
        return errors
    if (
        _text(source.get("semantic_contract_id"), limit=500)
        != _text(frozen_contract.get("semantic_contract_id"), limit=500)
        or _text(source.get("semantic_digest"), limit=128)
        != _text(frozen_contract.get("semantic_digest"), limit=128)
        or int(source.get("turn") or 0) != int(frozen_contract.get("turn") or 0)
    ):
        errors.append("GOAL_GRAPH_SOURCE_SEMANTIC_MISMATCH")
    return errors


def graph_structural_integrity(
    graph: dict[str, Any] | None,
    *,
    frozen_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(graph, dict):
        return _result(ok=False, code="GOAL_GRAPH_REQUIRED", errors=["GOAL_GRAPH_REQUIRED"])
    errors: list[str] = []
    if str(graph.get("version") or "") != CANONICAL_GOAL_GRAPH_VERSION:
        errors.append("GOAL_GRAPH_VERSION_INVALID")
    if not bool(graph.get("immutable")):
        errors.append("GOAL_GRAPH_IMMUTABLE_REQUIRED")
    graph_scope = normalize_scope(graph.get("scope") if isinstance(graph.get("scope"), dict) else {})
    for field in ("tenant_id", "user_id", "thread_id"):
        if not graph_scope.get(field):
            errors.append(f"GOAL_GRAPH_SCOPE_REQUIRED:{field}")
    errors.extend(_source_identity_errors(graph, frozen_contract=frozen_contract))
    expected_digest, expected_id = _expected_graph_digest(graph)
    if _text(graph.get("graph_digest"), limit=128) != expected_digest:
        errors.append("GOAL_GRAPH_DIGEST_INVALID")
    if _text(graph.get("graph_id"), limit=500) != expected_id:
        errors.append("GOAL_GRAPH_ID_INVALID")

    goal_rows = [row for row in list(graph.get("goals") or []) if isinstance(row, dict)]
    goal_ids = [_text(row.get("goal_id"), limit=200) for row in goal_rows]
    if any(not value for value in goal_ids) or len(goal_ids) != len(set(goal_ids)):
        errors.append("GOAL_GRAPH_GOAL_IDS_INVALID")
    known_goals = set(goal_ids)

    seen_ports: set[str] = set()
    for goal in goal_rows:
        goal_id = _text(goal.get("goal_id"), limit=200)
        target_port: dict[str, Any] | None = None
        for key, direction in (("input_ports", "input"), ("output_ports", "output")):
            for port in list(goal.get(key) or []):
                if not isinstance(port, dict) or str(port.get("version") or "") != GOAL_PORT_VERSION:
                    errors.append("GOAL_PORT_VERSION_INVALID")
                    continue
                port_id = _text(port.get("port_id"), limit=500)
                if not port_id or port_id in seen_ports:
                    errors.append("GOAL_PORT_ID_INVALID")
                seen_ports.add(port_id)
                if _text(port.get("goal_id"), limit=200) != goal_id:
                    errors.append("GOAL_PORT_OWNER_MISMATCH")
                if str(port.get("direction") or "") != direction:
                    errors.append("GOAL_PORT_DIRECTION_INVALID")
                if direction == "input" and str(port.get("name") or "") == "target":
                    if target_port is not None:
                        errors.append("TARGET_INPUT_PORT_DUPLICATE")
                    target_port = port
        binding = goal.get("target_binding")
        if isinstance(binding, dict):
            errors.extend(
                _target_binding_errors(
                    binding,
                    graph=graph,
                    goal_id=goal_id,
                    target_port=target_port,
                )
            )
        compatibility = goal.get("compatibility") if isinstance(goal.get("compatibility"), dict) else {}
        for dependency in list(compatibility.get("legacy_dependency_claims") or []):
            if str(dependency) not in known_goals:
                errors.append("DEPENDENCY_CLAIM_UNKNOWN_GOAL")

    ports = _port_index(graph)
    edge_rows = [row for row in list(graph.get("edges") or []) if isinstance(row, dict)]
    edge_ids = [_text(row.get("edge_id"), limit=500) for row in edge_rows]
    if any(not value for value in edge_ids) or len(edge_ids) != len(set(edge_ids)):
        errors.append("DATAFLOW_EDGE_IDS_INVALID")
    for edge in edge_rows:
        errors.extend(_edge_errors(edge, graph=graph, ports=ports))
    cycle = _edge_cycle(edge_rows, known_goals)
    if cycle:
        errors.append(f"DATAFLOW_EDGE_CYCLE:{'->'.join(cycle)}")

    return _result(
        ok=not errors,
        code="GOAL_GRAPH_STRUCTURALLY_VALID" if not errors else "GOAL_GRAPH_STRUCTURAL_INVALID",
        errors=errors,
    )


def dataflow_closure(
    graph: dict[str, Any] | None,
    *,
    frozen_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    structural = graph_structural_integrity(graph, frozen_contract=frozen_contract)
    if not structural.get("ok"):
        return _result(
            ok=False,
            code="GOAL_GRAPH_STRUCTURAL_INVALID",
            errors=list(structural.get("errors") or []),
            derived_dependencies={},
        )
    assert isinstance(graph, dict)
    goals = _goal_index(graph)
    edges = [row for row in list(graph.get("edges") or []) if isinstance(row, dict)]
    incoming_by_port: dict[str, list[dict[str, Any]]] = {}
    derived_dependencies: dict[str, list[str]] = {goal_id: [] for goal_id in goals}
    for edge in edges:
        incoming_by_port.setdefault(str(edge.get("consumer_port_id") or ""), []).append(edge)
        consumer = _text(edge.get("consumer_goal_id"), limit=200)
        producer = _text(edge.get("producer_goal_id"), limit=200)
        if consumer in derived_dependencies and producer and producer not in derived_dependencies[consumer]:
            derived_dependencies[consumer].append(producer)
    for consumer in derived_dependencies:
        derived_dependencies[consumer].sort()

    errors: list[str] = []
    for goal_id, goal in goals.items():
        binding = goal.get("target_binding") if isinstance(goal.get("target_binding"), dict) else None
        binding_verified = bool(binding and binding.get("verified") and binding.get("status") == "VERIFIED")
        for port in list(goal.get("input_ports") or []):
            if not isinstance(port, dict) or not bool(port.get("required")):
                continue
            port_id = _text(port.get("port_id"), limit=500)
            incoming = incoming_by_port.get(port_id, [])
            target_binding_applies = str(port.get("name") or "") == "target" and binding_verified
            authority_count = (1 if target_binding_applies else 0) + len(incoming)
            if authority_count == 0:
                errors.append(f"REQUIRED_INPUT_UNRESOLVED:{goal_id}:{port_id}")
            elif authority_count > 1:
                errors.append(f"REQUIRED_INPUT_MULTIPLE_AUTHORITIES:{goal_id}:{port_id}")
        compatibility = goal.get("compatibility") if isinstance(goal.get("compatibility"), dict) else {}
        for dependency in [
            str(value)
            for value in list(compatibility.get("legacy_dependency_claims") or [])
            if str(value)
        ]:
            if dependency not in derived_dependencies.get(goal_id, []):
                errors.append(f"DEPENDENCY_CLAIM_UNVERIFIED:{goal_id}:{dependency}")

    return _result(
        ok=not errors,
        code="GOAL_GRAPH_DATAFLOW_CLOSED" if not errors else "GOAL_GRAPH_DATAFLOW_OPEN",
        errors=errors,
        derived_dependencies=derived_dependencies,
        dependency_authority="verified_dataflow_edges_only",
    )


def verify_goal_graph(
    graph: dict[str, Any] | None,
    *,
    frozen_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    structural = graph_structural_integrity(graph, frozen_contract=frozen_contract)
    closure = (
        dataflow_closure(graph, frozen_contract=frozen_contract)
        if structural.get("ok")
        else {
            "ok": False,
            "code": "GOAL_GRAPH_STRUCTURAL_INVALID",
            "errors": list(structural.get("errors") or []),
            "derived_dependencies": {},
        }
    )
    return {
        "ok": bool(structural.get("ok") and closure.get("ok")),
        "structural": structural,
        "dataflow": closure,
    }


__all__ = ["dataflow_closure", "graph_structural_integrity", "verify_goal_graph"]
