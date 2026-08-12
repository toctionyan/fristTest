from __future__ import annotations

"""Deterministic Runtime target compilation from already-frozen semantics.

This module is intentionally narrower than execution planning.  It never asks a
model to reinterpret a target, never resolves a fresh natural-language target,
and never grants an ExecutionPermit.  It only converts an integrity-checked,
UNIQUE frozen historical reference into one target binding that is compatible
with a registered CapabilityTargetContract.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from agent_core.kernel.capability import CapabilityTargetContract
from agent_core.kernel.semantic_contract import semantic_contract_integrity, semantic_goals

COMPILED_RUNTIME_TARGET_VERSION = "compiled-runtime-target@1"
EXECUTION_TARGET_AUTHORITY_VERSION = "execution-target-authority@1"


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _result(
    *,
    status: str,
    reason_code: str,
    contract: dict[str, Any] | None,
    goal_id: str,
    target_contract: CapabilityTargetContract,
    binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    semantic = contract if isinstance(contract, dict) else {}
    payload: dict[str, Any] = {
        "version": COMPILED_RUNTIME_TARGET_VERSION,
        "status": status,
        "reason_code": reason_code,
        "authority": "deterministic_frozen_target_compiler",
        "goal_id": str(goal_id or ""),
        "semantic_contract_id": str(semantic.get("semantic_contract_id") or "") or None,
        "semantic_digest": str(semantic.get("semantic_digest") or "") or None,
        "target_contract": target_contract.as_dict(),
        "binding": deepcopy(binding) if isinstance(binding, dict) else None,
        "model_target_reinterpretation_allowed": False,
        "auto_substitution_used": False,
        "similarity_used": False,
        "execution_authority_granted": False,
    }
    digest_payload = deepcopy(payload)
    payload["compile_digest"] = _digest(digest_payload)
    return payload


def compile_frozen_reference_target(
    frozen_contract: dict[str, Any] | None,
    *,
    goal_id: str,
    target_contract: CapabilityTargetContract,
) -> dict[str, Any]:
    """Compile one UNIQUE frozen historical member into a Runtime target binding.

    C1 deliberately covers only the strongest deterministic case: one frozen
    historical member.  Collection pipelines and fresh literal targets remain
    outside this compiler until their own deterministic contracts are defined.
    Every non-provable case fails closed and returns no binding.
    """

    integrity = semantic_contract_integrity(frozen_contract)
    if not integrity.get("ok"):
        return _result(
            status="REJECTED",
            reason_code=str(integrity.get("code") or "SEMANTIC_CONTRACT_INVALID"),
            contract=frozen_contract,
            goal_id=goal_id,
            target_contract=target_contract,
        )

    goals = [row for row in semantic_goals(frozen_contract or {}) if str(row.get("goal_id") or "") == str(goal_id or "")]
    if len(goals) != 1:
        return _result(
            status="REJECTED",
            reason_code="FROZEN_GOAL_NOT_UNIQUE",
            contract=frozen_contract,
            goal_id=goal_id,
            target_contract=target_contract,
        )
    goal = goals[0]

    if target_contract.cardinality == "none":
        return _result(
            status="NOT_REQUIRED",
            reason_code="CAPABILITY_TARGET_NOT_REQUIRED",
            contract=frozen_contract,
            goal_id=goal_id,
            target_contract=target_contract,
        )
    if "target_resolver" not in set(target_contract.binding_sources):
        return _result(
            status="REJECTED",
            reason_code="TARGET_RESOLVER_BINDING_NOT_ALLOWED",
            contract=frozen_contract,
            goal_id=goal_id,
            target_contract=target_contract,
        )

    resolved = goal.get("resolved_reference") if isinstance(goal.get("resolved_reference"), dict) else {}
    proof = goal.get("referent_resolution_proof") if isinstance(goal.get("referent_resolution_proof"), dict) else {}
    result_ref = str(resolved.get("result_ref") or "").strip()
    member_handles = [str(value) for value in list(resolved.get("member_handles") or []) if str(value)]
    proof_digest = str(resolved.get("proof_digest") or "").strip()
    if not result_ref or not member_handles or not proof_digest:
        return _result(
            status="NOT_APPLICABLE",
            reason_code="FROZEN_RESOLVED_REFERENCE_REQUIRED",
            contract=frozen_contract,
            goal_id=goal_id,
            target_contract=target_contract,
        )
    if str(proof.get("resolution_status") or "") != "UNIQUE" or str(proof.get("proof_digest") or "") != proof_digest:
        return _result(
            status="REJECTED",
            reason_code="FROZEN_REFERENCE_PROOF_MISMATCH",
            contract=frozen_contract,
            goal_id=goal_id,
            target_contract=target_contract,
        )
    if len(member_handles) != 1:
        return _result(
            status="REJECTED",
            reason_code="SINGLE_MEMBER_REFERENCE_REQUIRED",
            contract=frozen_contract,
            goal_id=goal_id,
            target_contract=target_contract,
        )
    if target_contract.cardinality not in {"exactly_one", "one_or_collection"}:
        return _result(
            status="REJECTED",
            reason_code="CAPABILITY_CARDINALITY_INCOMPATIBLE_WITH_SINGLE_MEMBER",
            contract=frozen_contract,
            goal_id=goal_id,
            target_contract=target_contract,
        )

    selected_candidates = [
        row
        for row in list(proof.get("candidate_refs") or [])
        if isinstance(row, dict) and str(row.get("result_ref") or "") == result_ref
    ]
    proven_resource_types = {
        str(resource_type)
        for row in selected_candidates
        for resource_type in list(row.get("resource_types") or [])
        if str(resource_type)
        and bool((row.get("checks") or {}).get("object_type_match", True))
        and bool((row.get("checks") or {}).get("object_type_proven", False))
    }
    allowed_resource_types = set(target_contract.resource_types)
    compatible_types = sorted(proven_resource_types & allowed_resource_types)
    if not compatible_types:
        return _result(
            status="REJECTED",
            reason_code="TARGET_RESOURCE_TYPE_NOT_PROVEN",
            contract=frozen_contract,
            goal_id=goal_id,
            target_contract=target_contract,
        )

    binding = {
        "binding_source": "target_resolver",
        "binding_kind": "resolved_historical_member",
        "cardinality": "exactly_one",
        "resource_type": compatible_types[0],
        "result_ref": result_ref,
        "member_handle": member_handles[0],
        "referent_resolution_proof_digest": proof_digest,
        **(
            {"position": int(resolved["position"])}
            if isinstance(resolved.get("position"), int) and not isinstance(resolved.get("position"), bool)
            else {}
        ),
    }
    return _result(
        status="COMPILED",
        reason_code="UNIQUE_FROZEN_REFERENCE_COMPILED",
        contract=frozen_contract,
        goal_id=goal_id,
        target_contract=target_contract,
        binding=binding,
    )


def _authority_evidence(
    *,
    status: str,
    reason_code: str,
    target_contract: CapabilityTargetContract,
    goal_ids: list[str],
    per_goal: list[dict[str, Any]],
    candidate_target_replaced: bool = False,
    binding: dict[str, Any] | None = None,
    projected_target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": EXECUTION_TARGET_AUTHORITY_VERSION,
        "status": status,
        "reason_code": reason_code,
        "authority": "runtime_deterministic_target_authority",
        "goal_ids": list(goal_ids),
        "target_contract": target_contract.as_dict(),
        "per_goal": deepcopy(per_goal),
        "binding": deepcopy(binding) if isinstance(binding, dict) else None,
        "projected_target": deepcopy(projected_target) if isinstance(projected_target, dict) else None,
        "candidate_target_replaced": bool(candidate_target_replaced),
        "model_target_selection_authority": False if status == "COMPILED" else None,
        "execution_authority_granted": False,
    }
    payload["authority_digest"] = _digest(deepcopy(payload))
    return payload


def compile_runtime_target_arguments(
    frozen_contract: dict[str, Any] | None,
    *,
    goal_ids: list[str] | tuple[str, ...],
    target_contract: CapabilityTargetContract,
    arguments: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project frozen deterministic target authority into module-declared args."""

    original = deepcopy(dict(arguments or {}))
    projection = target_contract.argument_projection
    normalized_goal_ids = list(dict.fromkeys(str(value) for value in goal_ids if str(value)))
    if projection is None or "target_resolver" not in set(target_contract.binding_sources) or not normalized_goal_ids:
        return original, _authority_evidence(
            status="NOT_APPLICABLE",
            reason_code="DETERMINISTIC_TARGET_PROJECTION_NOT_CONFIGURED",
            target_contract=target_contract,
            goal_ids=normalized_goal_ids,
            per_goal=[],
        )

    per_goal = [
        compile_frozen_reference_target(
            frozen_contract,
            goal_id=goal_id,
            target_contract=target_contract,
        )
        for goal_id in normalized_goal_ids
    ]
    if any(str(row.get("status") or "") == "REJECTED" for row in per_goal):
        return original, _authority_evidence(
            status="REJECTED",
            reason_code="FROZEN_TARGET_COMPILATION_REJECTED",
            target_contract=target_contract,
            goal_ids=normalized_goal_ids,
            per_goal=per_goal,
        )

    compiled = [row for row in per_goal if str(row.get("status") or "") == "COMPILED"]
    if not compiled:
        status = (
            "NOT_REQUIRED"
            if per_goal and all(str(row.get("status") or "") == "NOT_REQUIRED" for row in per_goal)
            else "NOT_APPLICABLE"
        )
        return original, _authority_evidence(
            status=status,
            reason_code=(
                "CAPABILITY_TARGET_NOT_REQUIRED"
                if status == "NOT_REQUIRED"
                else "NO_FROZEN_HISTORICAL_TARGET_TO_COMPILE"
            ),
            target_contract=target_contract,
            goal_ids=normalized_goal_ids,
            per_goal=per_goal,
        )
    if len(compiled) != len(per_goal):
        return original, _authority_evidence(
            status="REJECTED",
            reason_code="MIXED_COMPILED_AND_UNCOMPILED_GOAL_TARGETS",
            target_contract=target_contract,
            goal_ids=normalized_goal_ids,
            per_goal=per_goal,
        )

    identities = {
        (
            str((row.get("binding") or {}).get("resource_type") or ""),
            str((row.get("binding") or {}).get("member_handle") or ""),
        )
        for row in compiled
        if isinstance(row.get("binding"), dict)
    }
    if len(identities) != 1:
        return original, _authority_evidence(
            status="REJECTED",
            reason_code="MULTI_GOAL_COMPILED_TARGET_CONFLICT",
            target_contract=target_contract,
            goal_ids=normalized_goal_ids,
            per_goal=per_goal,
        )

    binding = dict(compiled[0].get("binding") or {})
    projected_target: dict[str, Any] = {field: value for field, value in projection.constant_fields}
    for argument_field, binding_field in projection.binding_fields:
        value = binding.get(binding_field)
        if value in (None, ""):
            return original, _authority_evidence(
                status="REJECTED",
                reason_code="TARGET_ARGUMENT_PROJECTION_SOURCE_MISSING",
                target_contract=target_contract,
                goal_ids=normalized_goal_ids,
                per_goal=per_goal,
                binding=binding,
            )
        projected_target[argument_field] = deepcopy(value)

    projected = deepcopy(original)
    previous_target = original.get(projection.argument_name)
    projected[projection.argument_name] = projected_target
    return projected, _authority_evidence(
        status="COMPILED",
        reason_code="FROZEN_TARGET_ARGUMENTS_PROJECTED",
        target_contract=target_contract,
        goal_ids=normalized_goal_ids,
        per_goal=per_goal,
        candidate_target_replaced=previous_target != projected_target,
        binding=binding,
        projected_target=projected_target,
    )


__all__ = [
    "COMPILED_RUNTIME_TARGET_VERSION",
    "EXECUTION_TARGET_AUTHORITY_VERSION",
    "compile_frozen_reference_target",
    "compile_runtime_target_arguments",
]
