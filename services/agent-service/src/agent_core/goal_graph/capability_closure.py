from __future__ import annotations

"""Typed, read-only Goal Graph -> Capability Contract compatibility proofs.

Stage 2A deliberately does not replace the current planner.  It proves whether
an already-frozen Goal Graph can be consumed by a Capability Contract v2
without falling back to model target selection, similarity, business facts, or
execution-side guesses.  The proof is suitable for shadow comparison before a
later planner cutover.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.runtime.capability_effects import (
    canonical_semantic_effect_identity,
    canonical_effect_identity,
    completion_effects_for_contract,
)

from .contracts import normalize_cardinality, normalize_scope
from .target_evidence import validate_target_evidence
from .verified_input_evidence import (
    VERIFIED_INPUT_EVIDENCE_VERSION,
    validate_verified_input_evidence,
)
from .verifier import dataflow_closure, graph_structural_integrity

TYPED_GOAL_CAPABILITY_COVERAGE_VERSION = "typed-goal-capability-coverage@2"
LEGACY_TYPED_GOAL_CAPABILITY_COVERAGE_VERSION = "typed-goal-capability-coverage@1"
TARGET_EVIDENCE_VERSION = "typed-target-evidence@2"
EXACT_EFFECT_IDENTITY_VERSION = "semantic-effect@2"
_INTERACTIVE_INPUT_SOURCES = {"user_input", "structured_interaction"}
_UPSTREAM_INPUT_SOURCES = {"capability_output"}


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _authority_matches(required: str, actual: str) -> bool:
    required_value = _text(required, limit=200)
    actual_value = _text(actual, limit=200)
    return bool(required_value and actual_value and required_value == actual_value)


def _exact_effect_match(goal: dict[str, Any], contract: Any) -> dict[str, Any]:
    """Prove v2 effect compatibility while retaining the legacy alias path."""
    requested = goal.get("requested_effect") if isinstance(goal.get("requested_effect"), dict) else {}
    requested_v2 = canonical_semantic_effect_identity(requested)
    declared = getattr(contract, "semantic_effects_v2", None)
    if declared is None:
        declared = getattr(contract, "effect_identities_v2", None)
    declared_values = {
        canonical_semantic_effect_identity(value) if isinstance(value, dict) else str(value)
        for value in tuple(declared or ())
        if str(value)
    }
    if requested_v2 and requested_v2 in declared_values:
        return {
            "status": "EXACT_V2",
            "identity": requested_v2,
            "version": EXACT_EFFECT_IDENTITY_VERSION,
            "legacy_alias_used": False,
        }
    legacy = canonical_effect_identity(requested)
    completion = set(completion_effects_for_contract(contract))
    if legacy and legacy in completion:
        return {
            "status": "LEGACY_EFFECT_COMPAT_ONLY",
            "identity": requested_v2 or legacy,
            "version": EXACT_EFFECT_IDENTITY_VERSION,
            "legacy_alias_used": True,
        }
    return {
        "status": "EFFECT_IDENTITY_UNPROVEN",
        "identity": requested_v2 or legacy or None,
        "version": EXACT_EFFECT_IDENTITY_VERSION,
        "legacy_alias_used": False,
    }


def _contract_effect_compatible(goal: dict[str, Any], contract: Any) -> bool:
    proof = _exact_effect_match(goal, contract)
    return proof["status"] in {"EXACT_V2", "LEGACY_EFFECT_COMPAT_ONLY"}


def _freshness_reason(
    *,
    freshness_seconds: int | None,
    expires_at: Any,
    evaluation_time: float | None,
) -> str | None:
    if freshness_seconds is None:
        return None
    try:
        required_seconds = int(freshness_seconds)
    except (TypeError, ValueError):
        return "INPUT_FRESHNESS_CONTRACT_INVALID"
    if required_seconds <= 0:
        return "INPUT_FRESHNESS_CONTRACT_INVALID"
    if evaluation_time is None:
        return "FRESHNESS_EVALUATION_TIME_REQUIRED"
    try:
        expiry = float(expires_at)
    except (TypeError, ValueError):
        return "INPUT_FRESHNESS_PROOF_REQUIRED"
    if expiry <= 0:
        return "INPUT_FRESHNESS_PROOF_REQUIRED"
    return "INPUT_EVIDENCE_EXPIRED" if float(evaluation_time) >= expiry else None


def _goal_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("goal_id"), limit=200): row
        for row in list(graph.get("goals") or [])
        if isinstance(row, dict) and _text(row.get("goal_id"), limit=200)
    }


def _incoming_edges(graph: dict[str, Any], *, goal_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in list(graph.get("edges") or [])
        if isinstance(row, dict)
        and bool(row.get("verified"))
        and _text(row.get("consumer_goal_id"), limit=200) == goal_id
    ]


def _target_port(goal: dict[str, Any]) -> dict[str, Any] | None:
    rows = [
        row
        for row in list(goal.get("input_ports") or [])
        if isinstance(row, dict) and str(row.get("name") or "") == "target"
    ]
    return rows[0] if len(rows) == 1 else None


def _cardinality_compatible(*, expected: str, actual: str) -> bool:
    expected = normalize_cardinality(expected)
    actual = normalize_cardinality(actual)
    if expected == "none":
        return actual == "none"
    if expected == "one_or_collection":
        return actual in {"exactly_one", "collection"}
    if expected in {"exactly_one", "collection"}:
        return expected == actual
    return False


def _target_evidence(
    goal: dict[str, Any],
    graph: dict[str, Any],
    *,
    target_contract: Any | None = None,
    evaluation_time: float | None = None,
    issuer_validator: Any | None = None,
) -> dict[str, Any]:
    """Return one deterministic target authority for a graph Goal.

    A verified frozen historical binding is already a target-resolver proof.
    A verified data edge into the target port is also eligible for deterministic
    target compilation, but this function only reports compatibility; it does
    not create an execution target or permit.
    """

    goal_id = _text(goal.get("goal_id"), limit=200)
    port = _target_port(goal)
    if port is None:
        return {
            "status": "NOT_REQUIRED",
            "available_source_types": ["none"],
            "preferred_source_types": ["none"],
            "resource_type": None,
            "cardinality": "none",
            "proof_refs": [],
        }

    typed = goal.get("target_evidence")
    if isinstance(typed, dict):
        source = graph.get("source_semantic_contract") if isinstance(graph.get("source_semantic_contract"), dict) else {}
        validation = validate_target_evidence(
            typed,
            expected_scope=graph.get("scope") if isinstance(graph.get("scope"), dict) else {},
            expected_resource_type=(next(iter(tuple(getattr(target_contract, "resource_types", ()) or ())), None) if target_contract is not None else None),
            expected_logical_type_name=None,
            expected_cardinality=getattr(target_contract, "cardinality", None) if target_contract is not None else None,
            expected_semantic_contract_id=source.get("semantic_contract_id"),
            expected_semantic_digest=source.get("semantic_digest"),
            evaluation_time=evaluation_time,
            issuer_validator=issuer_validator,
        )
        if not validation.get("ok"):
            return {
                "status": "UNRESOLVED",
                "available_source_types": [],
                "preferred_source_types": [],
                "resource_type": _text(typed.get("resource_type"), limit=200).casefold() or None,
                "logical_type_name": _text(typed.get("logical_type_name"), limit=240) or None,
                "cardinality": normalize_cardinality(typed.get("cardinality")),
                "proof_refs": [_text(typed.get("proof_ref"), limit=500)] if _text(typed.get("proof_ref"), limit=500) else [],
                "evidence_version": validation.get("version") or TARGET_EVIDENCE_VERSION,
                "validation": validation,
            }
        variant_source = typed.get("source") if isinstance(typed.get("source"), dict) else {}
        selected_source = {
            "historical_visible_result": "visible_result_ref",
            "deterministic_target_resolver_projection": "target_resolver",
            "same_turn_verified_capability_output": "capability_output",
        }.get(str(typed.get("variant") or ""), "")
        return {
            "status": "VERIFIED",
            "available_source_types": [selected_source] if selected_source else [],
            "preferred_source_types": [selected_source] if selected_source else [],
            "resource_type": _text(typed.get("resource_type"), limit=200).casefold() or None,
            "logical_type_name": _text(typed.get("logical_type_name"), limit=240) or None,
            "cardinality": normalize_cardinality(typed.get("cardinality")),
            "authority_by_source": {selected_source: _text(typed.get("authority"), limit=200)} if selected_source else {},
            "expires_at": typed.get("expires_at"),
            "proof_refs": [_text(typed.get("proof_ref"), limit=500)],
            "provenance": "validated_typed_target_evidence",
            "evidence_version": validation.get("version") or TARGET_EVIDENCE_VERSION,
            "validation": validation,
            "source": variant_source,
        }

    authorities: list[dict[str, Any]] = []
    binding = goal.get("target_binding") if isinstance(goal.get("target_binding"), dict) else {}
    if bool(binding.get("verified")) and str(binding.get("status") or "") == "VERIFIED":
        binding_source = _text(binding.get("binding_source"), limit=120) or "visible_result_ref"
        source_types = [binding_source]
        if "target_resolver" not in source_types:
            source_types.append("target_resolver")
        authorities.append(
            {
                "source_types": source_types,
                "preferred_source_types": source_types,
                "resource_type": _text(binding.get("resource_type"), limit=200).casefold(),
                "cardinality": normalize_cardinality(binding.get("cardinality")),
                "logical_type_name": None,
                "proof_ref": _text(binding.get("binding_digest"), limit=256),
                "authority_by_source": {source: "authoritative" for source in source_types},
                "expires_at": None,
                "provenance": "verified_target_binding",
            }
        )

    port_id = _text(port.get("port_id"), limit=500)
    for edge in _incoming_edges(graph, goal_id=goal_id):
        if _text(edge.get("consumer_port_id"), limit=500) != port_id:
            continue
        artifact = edge.get("artifact_ref") if isinstance(edge.get("artifact_ref"), dict) else {}
        authorities.append(
            {
                # A verified upstream output remains a capability_output.  The
                # same exact edge may also be deterministically compiled by the
                # target resolver when the downstream target contract permits it.
                "source_types": ["capability_output", "target_resolver"],
                "preferred_source_types": ["capability_output", "target_resolver"],
                "resource_type": _text(artifact.get("resource_type"), limit=200).casefold(),
                "cardinality": normalize_cardinality(artifact.get("cardinality")),
                "logical_type_name": _text(artifact.get("type_name"), limit=240) or None,
                "proof_ref": _text(edge.get("edge_id"), limit=500),
                "authority_by_source": {
                    "capability_output": _text(artifact.get("authority"), limit=200),
                    "target_resolver": "authoritative",
                },
                "expires_at": artifact.get("expires_at"),
                "provenance": "verified_dataflow_edge",
            }
        )

    if not authorities:
        return {
            "status": "UNRESOLVED",
            "available_source_types": [],
            "preferred_source_types": [],
            "resource_type": _text(port.get("type_name"), limit=200).casefold() or None,
            "cardinality": normalize_cardinality(port.get("cardinality")),
            "proof_refs": [],
        }
    if len(authorities) != 1:
        return {
            "status": "MULTIPLE_AUTHORITIES",
            "available_source_types": [],
            "preferred_source_types": [],
            "resource_type": None,
            "cardinality": "unknown",
            "proof_refs": [row["proof_ref"] for row in authorities if row.get("proof_ref")],
        }
    authority = authorities[0]
    return {
        "status": "VERIFIED",
        "available_source_types": list(authority.get("source_types") or []),
        "preferred_source_types": list(authority.get("preferred_source_types") or []),
        "resource_type": authority["resource_type"] or None,
        "cardinality": authority["cardinality"],
        "logical_type_name": authority.get("logical_type_name"),
        "authority_by_source": deepcopy(authority.get("authority_by_source") or {}),
        "expires_at": authority.get("expires_at"),
        "proof_refs": [authority["proof_ref"]] if authority.get("proof_ref") else [],
        "provenance": authority["provenance"],
    }


def _prove_target_contract(
    goal: dict[str, Any],
    graph: dict[str, Any],
    target_contract: Any,
    *,
    target_issuer_validator: Any | None = None,
    evaluation_time: float | None = None,
) -> dict[str, Any]:
    evidence = _target_evidence(
        goal,
        graph,
        target_contract=target_contract,
        evaluation_time=evaluation_time,
        issuer_validator=target_issuer_validator,
    )
    expected_cardinality = normalize_cardinality(getattr(target_contract, "cardinality", "unknown"))
    allowed_types = {
        _text(value, limit=200).casefold()
        for value in tuple(getattr(target_contract, "resource_types", ()) or ())
        if _text(value, limit=200)
    }
    allowed_sources = {
        _text(value, limit=120)
        for value in tuple(getattr(target_contract, "binding_sources", ()) or ())
        if _text(value, limit=120)
    }

    reasons: list[str] = []
    if expected_cardinality == "none":
        if evidence["status"] != "NOT_REQUIRED":
            reasons.append("CAPABILITY_TARGET_NOT_ALLOWED_FOR_GOAL")
    else:
        if evidence["status"] != "VERIFIED":
            reasons.append(f"GOAL_TARGET_{evidence['status']}")
        else:
            if evidence.get("resource_type") not in allowed_types:
                reasons.append("CAPABILITY_TARGET_RESOURCE_TYPE_MISMATCH")
            if not _cardinality_compatible(
                expected=expected_cardinality,
                actual=str(evidence.get("cardinality") or "unknown"),
            ):
                reasons.append("CAPABILITY_TARGET_CARDINALITY_MISMATCH")
            available_sources = list(evidence.get("available_source_types") or [])
            selected_source = next(
                (source for source in list(evidence.get("preferred_source_types") or []) if source in allowed_sources),
                None,
            )
            if selected_source is None:
                reasons.append("CAPABILITY_TARGET_BINDING_SOURCE_MISMATCH")
            selected_authority = _text(
                (evidence.get("authority_by_source") or {}).get(selected_source), limit=200
            ) if selected_source else ""
            evidence = {
                **evidence,
                "selected_source_type": selected_source,
                "selected_authority": selected_authority or None,
            }

    return {
        "ok": not reasons,
        "expected": {
            "resource_types": sorted(allowed_types),
            "cardinality": expected_cardinality,
            "binding_sources": sorted(allowed_sources),
        },
        "evidence": evidence,
        "reasons": reasons,
    }


def _matching_upstream_edges(
    graph: dict[str, Any],
    *,
    goal_id: str,
    type_name: str,
) -> list[dict[str, Any]]:
    return [
        edge
        for edge in _incoming_edges(graph, goal_id=goal_id)
        if _text((edge.get("artifact_ref") or {}).get("type_name"), limit=240) == type_name
    ]


def _typed_input_evidence_matches(
    graph: dict[str, Any],
    *,
    type_name: str,
    source_types: set[str],
    available_input_evidence: tuple[dict[str, Any], ...],
    evaluation_time: float | None = None,
    issuer_validator: Any | None = None,
) -> list[dict[str, Any]]:
    graph_scope = normalize_scope(graph.get("scope") if isinstance(graph.get("scope"), dict) else {})
    source = graph.get("source_semantic_contract") if isinstance(graph.get("source_semantic_contract"), dict) else {}
    matches: list[dict[str, Any]] = []
    for row in available_input_evidence:
        if not isinstance(row, dict):
            continue
        if _text(row.get("type_name"), limit=240) != type_name:
            continue
        if _text(row.get("source_type"), limit=120) not in source_types:
            continue
        validation = validate_verified_input_evidence(
            row,
            expected_scope=graph_scope,
            expected_type_name=type_name,
            expected_semantic_contract_id=source.get("semantic_contract_id"),
            expected_semantic_digest=source.get("semantic_digest"),
            evaluation_time=evaluation_time,
            issuer_validator=issuer_validator,
        )
        if validation.get("ok"):
            matches.append({**row, "validation": validation})
    return matches


def _prove_required_inputs(
    goal: dict[str, Any],
    graph: dict[str, Any],
    *,
    planning_contract: Any,
    target_proof: dict[str, Any],
    available_input_evidence: tuple[dict[str, Any], ...],
    evaluation_time: float | None,
    input_issuer_validator: Any | None = None,
    target_issuer_validator: Any | None = None,
) -> dict[str, Any]:
    goal_id = _text(goal.get("goal_id"), limit=200)
    target_sources = {
        _text(value, limit=120)
        for value in tuple(getattr(planning_contract.target, "binding_sources", ()) or ())
        if _text(value, limit=120)
    }
    rows: list[dict[str, Any]] = []
    blocking: list[str] = []
    collectable: list[str] = []

    for required in tuple(getattr(planning_contract, "requires", ()) or ()):
        if not bool(getattr(required, "required", True)):
            continue
        name = _text(getattr(required, "name", ""), limit=240)
        type_name = _text(getattr(required, "type_name", ""), limit=240)
        sources = {
            _text(value, limit=120)
            for value in tuple(getattr(required, "source_types", ()) or ())
            if _text(value, limit=120)
        }
        required_authority = _text(getattr(required, "authority", ""), limit=200)
        required_freshness = getattr(required, "freshness_seconds", None)
        status = "UNPROVEN"
        proof_refs: list[str] = []
        reason = "REQUIRED_INPUT_NOT_BOUND"
        evidence_authority = ""
        evidence_expires_at: Any = None

        # A capability may name its target input differently (for example
        # ``target_binding``).  The structural relation is declared by the
        # source type intersection with CapabilityTargetContract.binding_sources,
        # not by business-specific input names.
        selected_target_source = _text(
            (target_proof.get("evidence") or {}).get("selected_source_type"), limit=120
        )
        target_can_supply_input = (
            normalize_cardinality(getattr(planning_contract.target, "cardinality", "unknown")) != "none"
            and selected_target_source
            and selected_target_source in sources
        )
        if target_can_supply_input:
            target_evidence = target_proof.get("evidence") or {}
            target_logical_type = _text(target_evidence.get("logical_type_name"), limit=240)
            evidence_authority = _text(target_evidence.get("selected_authority"), limit=200)
            evidence_expires_at = target_evidence.get("expires_at")
            if not target_proof.get("ok"):
                reason = "TARGET_CONTRACT_NOT_CLOSED"
            elif selected_target_source == "capability_output" and target_logical_type != type_name:
                reason = "TARGET_CAPABILITY_OUTPUT_TYPE_MISMATCH"
            elif not _authority_matches(required_authority, evidence_authority):
                reason = "TARGET_INPUT_AUTHORITY_MISMATCH"
            elif (freshness_reason := _freshness_reason(
                freshness_seconds=required_freshness,
                expires_at=evidence_expires_at,
                evaluation_time=evaluation_time,
            )) is not None:
                reason = freshness_reason
            else:
                status = "SATISFIED_BY_TARGET"
                proof_refs = list(target_evidence.get("proof_refs") or [])
                reason = "TARGET_CONTRACT_PROOF"
        elif sources & _UPSTREAM_INPUT_SOURCES:
            matches = _matching_upstream_edges(graph, goal_id=goal_id, type_name=type_name)
            if len(matches) == 1:
                artifact = matches[0].get("artifact_ref") if isinstance(matches[0].get("artifact_ref"), dict) else {}
                evidence_authority = _text(artifact.get("authority"), limit=200)
                evidence_expires_at = artifact.get("expires_at")
                if not _authority_matches(required_authority, evidence_authority):
                    reason = "UPSTREAM_INPUT_AUTHORITY_MISMATCH"
                elif (freshness_reason := _freshness_reason(
                    freshness_seconds=required_freshness,
                    expires_at=evidence_expires_at,
                    evaluation_time=evaluation_time,
                )) is not None:
                    reason = freshness_reason
                else:
                    status = "SATISFIED_BY_UPSTREAM_OUTPUT"
                    proof_refs = [_text(matches[0].get("edge_id"), limit=500)]
                    reason = "EXACT_TYPED_UPSTREAM_OUTPUT"
            elif len(matches) > 1:
                reason = "MULTIPLE_UPSTREAM_INPUT_AUTHORITIES"
            else:
                reason = "UPSTREAM_CAPABILITY_OUTPUT_REQUIRED"
        else:
            evidence_matches = _typed_input_evidence_matches(
                graph,
                type_name=type_name,
                source_types=sources,
                available_input_evidence=available_input_evidence,
                evaluation_time=evaluation_time,
                issuer_validator=input_issuer_validator,
            )
            if len(evidence_matches) == 1:
                evidence_authority = _text(evidence_matches[0].get("authority"), limit=200)
                evidence_expires_at = evidence_matches[0].get("expires_at")
                if not _authority_matches(required_authority, evidence_authority):
                    reason = "TYPED_INPUT_AUTHORITY_MISMATCH"
                elif (freshness_reason := _freshness_reason(
                    freshness_seconds=required_freshness,
                    expires_at=evidence_expires_at,
                    evaluation_time=evaluation_time,
                )) is not None:
                    reason = freshness_reason
                else:
                    status = "SATISFIED_BY_TYPED_EVIDENCE"
                    proof_refs = [_text(evidence_matches[0].get("proof_ref"), limit=500)]
                    reason = "EXACT_TYPED_INPUT_EVIDENCE"
            elif len(evidence_matches) > 1:
                reason = "MULTIPLE_TYPED_INPUT_AUTHORITIES"
            elif sources & _INTERACTIVE_INPUT_SOURCES:
                status = "COLLECTABLE"
                reason = "STRUCTURED_INTERACTION_CAN_COLLECT"
            else:
                reason = "NO_TYPED_INPUT_SOURCE_PROOF"

        row = {
            "name": name,
            "type_name": type_name,
            "source_types": sorted(sources),
            "authority": required_authority,
            "freshness_seconds": required_freshness,
            "evidence_authority": evidence_authority or None,
            "evidence_expires_at": evidence_expires_at,
            "status": status,
            "reason": reason,
            "proof_refs": proof_refs,
        }
        rows.append(row)
        if status == "COLLECTABLE":
            collectable.append(name)
        elif not status.startswith("SATISFIED_"):
            blocking.append(name)

    return {
        "ok": not blocking,
        "inputs": rows,
        "blocking_input_names": sorted(blocking),
        "collectable_input_names": sorted(collectable),
        "readiness": (
            "NEEDS_INTERACTION" if collectable and not blocking
            else "READY" if not blocking
            else "BLOCKED"
        ),
    }


def _candidate_proof(
    goal: dict[str, Any],
    graph: dict[str, Any],
    *,
    tool_name: str,
    contract: Any,
    available_input_evidence: tuple[dict[str, Any], ...],
    evaluation_time: float | None,
    input_issuer_validator: Any | None = None,
    target_issuer_validator: Any | None = None,
) -> dict[str, Any]:
    requested_identity = canonical_effect_identity(goal.get("requested_effect"))
    reasons: list[str] = []
    if not requested_identity or not _contract_effect_compatible(goal, contract):
        reasons.append("CAPABILITY_EFFECT_MISMATCH")
    effect_proof = _exact_effect_match(goal, contract)

    planning = getattr(contract, "planning_contract", None)
    if str(getattr(contract, "contract_version", "")) != "2" or planning is None:
        reasons.append("CAPABILITY_CONTRACT_V2_REQUIRED")
        target_proof = {"ok": False, "reasons": ["CAPABILITY_CONTRACT_V2_REQUIRED"]}
        input_proof = {
            "ok": False,
            "inputs": [],
            "blocking_input_names": [],
            "collectable_input_names": [],
            "readiness": "BLOCKED",
        }
    else:
        target_proof = _prove_target_contract(
            goal,
            graph,
            planning.target,
            target_issuer_validator=target_issuer_validator,
            evaluation_time=evaluation_time,
        )
        if not target_proof["ok"]:
            reasons.extend(target_proof["reasons"])
        input_proof = _prove_required_inputs(
            goal,
            graph,
            planning_contract=planning,
            target_proof=target_proof,
            available_input_evidence=available_input_evidence,
            evaluation_time=evaluation_time,
            input_issuer_validator=input_issuer_validator,
            target_issuer_validator=target_issuer_validator,
        )
        if not input_proof["ok"]:
            reasons.append("CAPABILITY_REQUIRED_INPUTS_UNCLOSED")

    semantic_compatible = not any(
        reason in {
            "CAPABILITY_EFFECT_MISMATCH",
            "CAPABILITY_CONTRACT_V2_REQUIRED",
            "CAPABILITY_TARGET_NOT_ALLOWED_FOR_GOAL",
            "CAPABILITY_TARGET_RESOURCE_TYPE_MISMATCH",
            "CAPABILITY_TARGET_CARDINALITY_MISMATCH",
            "CAPABILITY_TARGET_BINDING_SOURCE_MISMATCH",
        }
        or reason.startswith("GOAL_TARGET_")
        for reason in reasons
    )
    if not semantic_compatible:
        status = "REJECTED"
    elif not input_proof["ok"]:
        status = "BLOCKED_INPUT"
    elif input_proof["readiness"] == "NEEDS_INTERACTION":
        status = "NEEDS_INTERACTION"
    else:
        status = "READY"

    payload = {
        "tool_name": tool_name,
        "capability_key": _text(getattr(contract, "key", ""), limit=300) or None,
        "requested_effect_identity": requested_identity or None,
        "exact_effect_proof": effect_proof,
        "status": status,
        "semantic_compatible": semantic_compatible,
        "target_proof": target_proof,
        "input_proof": input_proof,
        "authorization": (
            deepcopy(planning.authorization.as_dict())
            if planning is not None and hasattr(planning.authorization, "as_dict")
            else None
        ),
        "completion": (
            deepcopy(planning.completion.as_dict())
            if planning is not None and hasattr(planning.completion, "as_dict")
            else None
        ),
        "reasons": sorted(set(reasons)),
        "execution_authority_granted": False,
        "permit_created": False,
    }
    payload["proof_digest"] = _digest(payload)
    return payload


def build_typed_goal_capability_coverage(
    *,
    graph: dict[str, Any],
    capability_registry: CapabilityRegistry,
    frozen_contract: dict[str, Any] | None = None,
    available_input_evidence: tuple[dict[str, Any], ...] = (),
    evaluation_time: float | None = None,
    input_issuer_validator: Any | None = None,
    target_issuer_validator: Any | None = None,
) -> dict[str, Any]:
    """Build a typed compatibility proof without selecting or dispatching Tools."""

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
    source = graph.get("source_semantic_contract") if isinstance(graph.get("source_semantic_contract"), dict) else {}
    if not structural.get("ok"):
        payload = {
            "version": TYPED_GOAL_CAPABILITY_COVERAGE_VERSION,
            "authority": "read_only_typed_compatibility_not_execution_authority",
            "graph_id": graph.get("graph_id"),
            "graph_digest": graph.get("graph_digest"),
            "semantic_contract_id": source.get("semantic_contract_id"),
            "semantic_digest": source.get("semantic_digest"),
            "capability_registry_version": capability_registry.version,
            "target_evidence_version": TARGET_EVIDENCE_VERSION,
            "verified_input_evidence_version": VERIFIED_INPUT_EVIDENCE_VERSION,
            "exact_effect_identity_version": EXACT_EFFECT_IDENTITY_VERSION,
            "evaluation_time": evaluation_time,
            "coverage_status": "STRUCTURAL_INVALID",
            "dataflow_status": closure.get("code"),
            "goals": [],
            "must_not_dispatch": True,
            "creates_permit": False,
        }
        payload["coverage_digest"] = _digest(payload)
        return payload

    per_goal: list[dict[str, Any]] = []
    covered_required: set[str] = set()
    required_goal_ids: list[str] = []
    ready_goal_ids: list[str] = []
    interaction_goal_ids: list[str] = []

    for goal_id, goal in sorted(_goal_index(graph).items()):
        if bool(goal.get("required", True)):
            required_goal_ids.append(goal_id)
        requested_identity = canonical_effect_identity(goal.get("requested_effect"))
        candidates: list[dict[str, Any]] = []
        for tool_name in sorted(capability_registry.tool_names()):
            contract = capability_registry.contract_for_tool(tool_name)
            if contract is None or str(getattr(contract, "execution_kind", "")) in {
                "unsupported",
                "clarification_read",
            }:
                continue
            if not _contract_effect_compatible(goal, contract):
                continue
            candidates.append(
                _candidate_proof(
                    goal,
                    graph,
                    tool_name=tool_name,
                    contract=contract,
                    available_input_evidence=tuple(available_input_evidence or ()),
                    evaluation_time=evaluation_time,
                    input_issuer_validator=input_issuer_validator,
                    target_issuer_validator=target_issuer_validator,
                )
            )

        usable = [row for row in candidates if row["status"] == "READY"]
        ready = [row for row in candidates if row["status"] == "READY"]
        needs_interaction = [row for row in candidates if row["status"] == "NEEDS_INTERACTION"]
        if ready and bool(goal.get("required", True)):
            covered_required.add(goal_id)
        if ready:
            ready_goal_ids.append(goal_id)
        elif needs_interaction:
            interaction_goal_ids.append(goal_id)

        per_goal.append(
            {
                "goal_id": goal_id,
                "required": bool(goal.get("required", True)),
                "requested_effect_identity": requested_identity or None,
                "status": (
                    "TYPED_COVERED_READY" if ready
                    else "TYPED_COVERED_NEEDS_INTERACTION" if needs_interaction
                    else "EFFECT_MATCH_BUT_TYPED_UNCLOSED" if candidates
                    else "UNCOVERED"
                ),
                "closed_capability_tools": [row["tool_name"] for row in ready],
                "collectable_capability_tools": [row["tool_name"] for row in needs_interaction],
                "candidate_proofs": candidates,
            }
        )

    uncovered = sorted(set(required_goal_ids) - covered_required)
    coverage_status = "COMPLETE" if not uncovered else "INCOMPLETE"
    if not closure.get("ok"):
        coverage_status = "DATAFLOW_OPEN"

    payload = {
        "version": TYPED_GOAL_CAPABILITY_COVERAGE_VERSION,
        "authority": "read_only_typed_compatibility_not_execution_authority",
        "matching": "exact_effect_plus_typed_target_and_input_contracts",
        "graph_id": graph.get("graph_id"),
        "graph_digest": graph.get("graph_digest"),
        "semantic_contract_id": source.get("semantic_contract_id"),
        "semantic_digest": source.get("semantic_digest"),
        "capability_registry_version": capability_registry.version,
        "target_evidence_version": TARGET_EVIDENCE_VERSION,
        "verified_input_evidence_version": VERIFIED_INPUT_EVIDENCE_VERSION,
        "exact_effect_identity_version": EXACT_EFFECT_IDENTITY_VERSION,
        "evaluation_time": evaluation_time,
        "coverage_status": coverage_status,
        "dataflow_status": closure.get("code"),
        "dataflow_errors": list(closure.get("errors") or []),
        "derived_dependencies": deepcopy(closure.get("derived_dependencies") or {}),
        "required_goal_ids": required_goal_ids,
        "uncovered_goal_ids": uncovered,
        "ready_goal_ids": sorted(set(ready_goal_ids)),
        "interaction_goal_ids": sorted(set(interaction_goal_ids)),
        "goals": per_goal,
        "must_not_dispatch": True,
        "creates_permit": False,
        "mutates_graph": False,
        "mutates_semantics": False,
        "model_target_selection_authority": False,
        "execution_authority_granted": False,
    }
    payload["coverage_digest"] = _digest(payload)
    return payload


def replay_typed_goal_capability_coverage(coverage: Any) -> dict[str, Any]:
    """Replay a v2 shadow coverage envelope without consulting runtime state."""
    source = coverage if isinstance(coverage, dict) else {}
    errors: list[str] = []
    if source.get("version") != TYPED_GOAL_CAPABILITY_COVERAGE_VERSION:
        errors.append("COVERAGE_SCHEMA_VERSION_INVALID")
    expected_digest = _text(source.get("coverage_digest"), limit=128)
    unsigned = deepcopy(source)
    unsigned.pop("coverage_digest", None)
    actual_digest = _digest(unsigned)
    if not expected_digest or expected_digest != actual_digest:
        errors.append("COVERAGE_DIGEST_MISMATCH")
    for field, expected in (
        ("must_not_dispatch", True),
        ("creates_permit", False),
        ("mutates_graph", False),
        ("mutates_semantics", False),
        ("model_target_selection_authority", False),
        ("execution_authority_granted", False),
    ):
        if source.get(field) is not expected:
            errors.append(f"COVERAGE_{field.upper()}_INVARIANT_FAILED")
    required = {
        str(value)
        for value in list(source.get("required_goal_ids") or [])
        if str(value)
    }
    ready = {str(value) for value in list(source.get("ready_goal_ids") or []) if str(value)}
    interactive = {str(value) for value in list(source.get("interaction_goal_ids") or []) if str(value)}
    if ready.intersection(interactive):
        errors.append("COVERAGE_READY_AND_INTERACTIVE_GOAL_OVERLAP")
    if source.get("coverage_status") == "COMPLETE" and not required.issubset(ready):
        errors.append("COVERAGE_COMPLETE_REQUIRES_ALL_REQUIRED_GOALS_READY")
    for row in list(source.get("goals") or []):
        if not isinstance(row, dict):
            errors.append("COVERAGE_GOAL_ROW_INVALID")
            continue
    return {
        "ok": not errors,
        "version": source.get("version"),
        "expected_digest": expected_digest,
        "actual_digest": actual_digest,
        "errors": sorted(set(errors)),
    }


__all__ = [
    "EXACT_EFFECT_IDENTITY_VERSION",
    "LEGACY_TYPED_GOAL_CAPABILITY_COVERAGE_VERSION",
    "TYPED_GOAL_CAPABILITY_COVERAGE_VERSION",
    "build_typed_goal_capability_coverage",
    "replay_typed_goal_capability_coverage",
]
