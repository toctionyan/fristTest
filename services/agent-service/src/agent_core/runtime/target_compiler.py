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


__all__ = [
    "COMPILED_RUNTIME_TARGET_VERSION",
    "compile_frozen_reference_target",
]
