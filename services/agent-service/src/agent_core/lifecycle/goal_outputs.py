from __future__ import annotations

"""Typed, scope-bound outputs produced by completed semantic Goals.

GoalOutputRef is planning evidence only. It never replaces the verified ledger,
TargetResolver, CapabilityGate, ExecutionPermit, transaction authority, or the
Business Service. A ref is reusable only while its referenced ledger artifact
is active, its scope and semantic contract still match, and its producer Goal
has reached the durable COMPLETED lifecycle.
"""

from copy import deepcopy
from hashlib import sha256
import json
from time import time
from typing import Any, Iterable

from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.kernel.semantic_contract import (
    GOAL_INPUT_BINDING_AUTHORITY,
    derive_goal_target_identity,
    semantic_goals,
)
from agent_core.lifecycle.semantic_contract import prove_goal_target_compatibility
from agent_core.runtime.capability_effects import canonical_effect_identity, completion_effects_for_contract, support_effects_for_contract
from agent_core.ledger import find_handle, scope_for_state

GOAL_OUTPUT_REF_VERSION = "goal-output-ref@1"
_ACTIVE_STATUS = "ACTIVE"
_COMPLETED_GOAL_LIFECYCLES = {"COMPLETED"}


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _scope_matches(ref_scope: dict[str, Any], state: dict[str, Any]) -> bool:
    current = scope_for_state(state)
    for key in ("tenant_id", "user_id", "thread_id"):
        expected = str(current.get(key) or "")
        actual = str((ref_scope or {}).get(key) or "")
        if expected and actual != expected:
            return False
    return True


def _completed_goal_ids(state: dict[str, Any]) -> set[str]:
    return {
        str(row.get("goal_id") or "")
        for row in list(state.get("goal_records") or [])
        if isinstance(row, dict)
        and str(row.get("lifecycle") or "").upper() in _COMPLETED_GOAL_LIFECYCLES
        and str(row.get("goal_id") or "")
    }


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value:
            yield value
        return
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
        return
    if isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _walk_strings(child)


def _target_binding(entry: dict[str, Any], *, ledger: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    target_handle = str(entry.get("target_handle") or "")
    if str(entry.get("kind") or "") == "artifact":
        target_handle = str(entry.get("handle") or "")
    target = find_handle(
        ledger,
        target_handle,
        scope=scope_for_state(state),
        allowed_kinds={"artifact"},
    ) if target_handle else None
    return {
        "target_handle": target_handle or None,
        "resource_type": str((target or {}).get("resource_type") or "") or None,
        "resource_id": str((target or {}).get("resource_id") or "") or None,
    }


def _with_digest(ref: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(ref)
    row.pop("ref_digest", None)
    row["ref_digest"] = _digest(row)
    return row


def validate_goal_output_ref(
    ref: dict[str, Any],
    *,
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
    now: float | None = None,
) -> dict[str, Any]:
    current = time() if now is None else float(now)
    if not isinstance(ref, dict) or str(ref.get("version") or "") != GOAL_OUTPUT_REF_VERSION:
        return {"ok": False, "code": "GOAL_OUTPUT_REF_VERSION_INVALID"}
    expected_digest = str(ref.get("ref_digest") or "")
    unsigned = deepcopy(ref)
    unsigned.pop("ref_digest", None)
    if not expected_digest or expected_digest != _digest(unsigned):
        return {"ok": False, "code": "GOAL_OUTPUT_REF_DIGEST_INVALID"}
    if str(ref.get("status") or "") != _ACTIVE_STATUS or not bool(ref.get("verified")):
        return {"ok": False, "code": "GOAL_OUTPUT_REF_NOT_ACTIVE"}
    if str(ref.get("producer_goal_id") or "") not in _completed_goal_ids(state):
        return {"ok": False, "code": "GOAL_OUTPUT_REF_PRODUCER_NOT_COMPLETED"}
    contract = state.get("frozen_semantic_contract") if isinstance(state.get("frozen_semantic_contract"), dict) else {}
    if str(ref.get("semantic_contract_id") or "") != str(contract.get("semantic_contract_id") or ""):
        return {"ok": False, "code": "GOAL_OUTPUT_REF_SEMANTIC_CONTRACT_MISMATCH"}
    if str(ref.get("semantic_digest") or "") != str(contract.get("semantic_digest") or ""):
        return {"ok": False, "code": "GOAL_OUTPUT_REF_SEMANTIC_DIGEST_MISMATCH"}
    if str(ref.get("capability_registry_version") or "") != str(capability_registry.version or ""):
        return {"ok": False, "code": "GOAL_OUTPUT_REF_REGISTRY_VERSION_MISMATCH"}
    if not _scope_matches(ref.get("scope") if isinstance(ref.get("scope"), dict) else {}, state):
        return {"ok": False, "code": "GOAL_OUTPUT_REF_SCOPE_MISMATCH"}
    try:
        expires_at = float(ref.get("expires_at") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "code": "GOAL_OUTPUT_REF_EXPIRY_INVALID"}
    if expires_at > 0 and current >= expires_at:
        return {"ok": False, "code": "GOAL_OUTPUT_REF_EXPIRED"}
    artifact_ref = str(ref.get("artifact_ref") or "")
    if not artifact_ref:
        return {"ok": False, "code": "GOAL_OUTPUT_REF_ARTIFACT_REQUIRED"}
    artifact = find_handle(
        state.get("artifact_ledger") or [],
        artifact_ref,
        scope=scope_for_state(state),
        active_only=True,
    )
    if artifact is None:
        return {"ok": False, "code": "GOAL_OUTPUT_REF_ARTIFACT_INVALID"}
    expected_target_binding = _target_binding(
        artifact,
        ledger=list(state.get("artifact_ledger") or []),
        state=state,
    )
    if ref.get("target_binding") != expected_target_binding:
        return {"ok": False, "code": "GOAL_OUTPUT_REF_TARGET_BINDING_MISMATCH"}
    if contract.get("dependency_authority") == GOAL_INPUT_BINDING_AUTHORITY:
        semantic_output_ids = ref.get("semantic_output_ids")
        if not isinstance(semantic_output_ids, list) or not semantic_output_ids:
            return {"ok": False, "code": "GOAL_OUTPUT_REF_SEMANTIC_OUTPUT_REQUIRED"}
        if any(not str(value or "").strip() for value in semantic_output_ids):
            return {"ok": False, "code": "GOAL_OUTPUT_REF_SEMANTIC_OUTPUT_INVALID"}
        if str(ref.get("output_cardinality") or "") not in {
            "single", "collection", "unknown"
        }:
            return {"ok": False, "code": "GOAL_OUTPUT_REF_CARDINALITY_INVALID"}
        target_identity = ref.get("producer_target_identity")
        if not isinstance(target_identity, dict):
            return {"ok": False, "code": "GOAL_OUTPUT_REF_TARGET_IDENTITY_REQUIRED"}
        producer_goal = next(
            (
                row
                for row in semantic_goals(contract)
                if str(row.get("goal_id") or "")
                == str(ref.get("producer_goal_id") or "")
            ),
            None,
        )
        if producer_goal is None:
            return {"ok": False, "code": "GOAL_OUTPUT_REF_PRODUCER_GOAL_UNKNOWN"}
        expected_semantic_outputs = sorted({
            str(row.get("output_id") or "").strip().casefold()
            for row in list(
                (producer_goal.get("requested_effect") or {}).get("requested_outputs")
                or []
            )
            if isinstance(row, dict) and str(row.get("output_id") or "").strip()
        })
        if sorted(str(value or "").casefold() for value in semantic_output_ids) != expected_semantic_outputs:
            return {"ok": False, "code": "GOAL_OUTPUT_REF_SEMANTIC_OUTPUT_MISMATCH"}
        if str(ref.get("output_cardinality") or "") != str(
            producer_goal.get("expected_result_cardinality") or "unknown"
        ):
            return {"ok": False, "code": "GOAL_OUTPUT_REF_CARDINALITY_MISMATCH"}
        if target_identity != derive_goal_target_identity(producer_goal):
            return {"ok": False, "code": "GOAL_OUTPUT_REF_TARGET_IDENTITY_MISMATCH"}
        if str(ref.get("runtime_target_identity_digest") or "") != _digest(
            expected_target_binding
        ):
            return {"ok": False, "code": "GOAL_OUTPUT_REF_RUNTIME_TARGET_DIGEST_INVALID"}
    return {"ok": True, "code": "GOAL_OUTPUT_REF_VALID", "ref": deepcopy(ref), "artifact": artifact}


def normalize_goal_output_refs(
    refs: Iterable[dict[str, Any]] | None,
    *,
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
) -> tuple[list[dict[str, Any]], list[str]]:
    valid: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for raw in list(refs or []):
        if not isinstance(raw, dict):
            continue
        check = validate_goal_output_ref(raw, state=state, capability_registry=capability_registry)
        if not check.get("ok"):
            errors.append(str(check.get("code") or "GOAL_OUTPUT_REF_INVALID"))
            continue
        ref = deepcopy(raw)
        key = str(ref.get("goal_output_ref_id") or ref.get("ref_digest") or "")
        if key:
            valid[key] = ref
    return sorted(valid.values(), key=lambda row: (int(row.get("created_turn") or 0), str(row.get("goal_output_ref_id") or ""))), sorted(set(errors))


def _output_artifact_ref(
    *,
    output_name: str,
    result: dict[str, Any],
    additions: list[dict[str, Any]],
) -> str:
    addition_by_handle = {str(row.get("handle") or ""): row for row in additions if str(row.get("handle") or "")}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    direct_candidates: list[str] = []
    for key in (output_name, f"{output_name}_handle", f"{output_name}_ref"):
        value = data.get(key)
        if isinstance(value, str) and value in addition_by_handle:
            direct_candidates.append(value)
    plural = data.get(f"{output_name}_handles")
    if isinstance(plural, list):
        direct_candidates.extend(str(value) for value in plural if str(value) in addition_by_handle)
    direct_candidates = list(dict.fromkeys(direct_candidates))
    if len(direct_candidates) == 1:
        return direct_candidates[0]
    mentioned = list(dict.fromkeys(
        value for value in _walk_strings(data) if value in addition_by_handle
    ))
    if len(mentioned) == 1:
        return mentioned[0]
    non_artifact = [
        str(row.get("handle") or "")
        for row in additions
        if str(row.get("kind") or "") != "artifact" and str(row.get("handle") or "")
    ]
    return non_artifact[0] if len(non_artifact) == 1 else ""


def record_goal_outputs_from_tool_result(
    existing_refs: Iterable[dict[str, Any]] | None,
    *,
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
    tool_name: str,
    goal_ids: Iterable[str],
    effect_id: str,
    result: dict[str, Any],
    ledger_additions: Iterable[dict[str, Any]],
    merged_ledger: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not bool((result or {}).get("ok")):
        return [deepcopy(row) for row in list(existing_refs or []) if isinstance(row, dict)]
    contract = capability_registry.contract_for_tool(str(tool_name or ""))
    if contract is None or contract.contract_version != "2" or contract.planning_contract is None:
        return [deepcopy(row) for row in list(existing_refs or []) if isinstance(row, dict)]
    bound_goals = list(dict.fromkeys(str(value) for value in goal_ids if str(value)))
    if not bound_goals:
        return [deepcopy(row) for row in list(existing_refs or []) if isinstance(row, dict)]
    semantic = state.get("frozen_semantic_contract") if isinstance(state.get("frozen_semantic_contract"), dict) else {}
    if not semantic:
        return [deepcopy(row) for row in list(existing_refs or []) if isinstance(row, dict)]

    formal_by_id = {
        str(row.get("goal_id") or ""): row
        for row in semantic_goals(semantic)
        if str(row.get("goal_id") or "")
    }
    completion_effects = set(completion_effects_for_contract(contract))
    support_effects = set(support_effects_for_contract(contract))
    primary_completion_output = (
        str(contract.planning_contract.completion.output_name or "")
        if contract.planning_contract.completion.mode == "tool_output"
        else ""
    )
    # The caller is the post-CapabilityGate execution boundary.  Preserve all
    # explicitly bound formal Goals here; direct unit callers may not carry the
    # MatchProof object, while production dispatch has already proved the exact
    # completion/support role for every goal_id.
    eligible_goals = [goal_id for goal_id in bound_goals if goal_id in formal_by_id]
    if not eligible_goals:
        return [deepcopy(row) for row in list(existing_refs or []) if isinstance(row, dict)]
    if len(eligible_goals) > 1:
        target_compatibility = prove_goal_target_compatibility(
            formal_by_id[goal_id] for goal_id in eligible_goals
        )
        if target_compatibility.get("status") != "SAME":
            # CapabilityGate should already have rejected this call.  Keep the
            # persistent GoalOutputRef owner fail-closed as a second boundary so
            # forged/replayed direct callers cannot turn one artifact into proof
            # for multiple frozen targets.
            return [deepcopy(row) for row in list(existing_refs or []) if isinstance(row, dict)]

    additions = [
        deepcopy(row) for row in list(ledger_additions or [])
        if isinstance(row, dict) and str(row.get("handle") or "")
    ]
    refs = [deepcopy(row) for row in list(existing_refs or []) if isinstance(row, dict)]
    for output in contract.planning_contract.produces:
        artifact_ref = _output_artifact_ref(
            output_name=str(output.name), result=result or {}, additions=additions
        )
        if not artifact_ref:
            continue
        entry = find_handle(merged_ledger, artifact_ref, scope=scope_for_state(state), active_only=True)
        if entry is None:
            continue
        entry_expires = float(entry.get("expires_at") or 0)
        freshness_expires = time() + int(output.freshness_seconds or 0) if output.freshness_seconds else 0
        expires_candidates = [value for value in (entry_expires, freshness_expires) if value > 0]
        expires_at = min(expires_candidates) if expires_candidates else 0
        for goal_id in eligible_goals:
            effect_identity = canonical_effect_identity(formal_by_id[goal_id].get("requested_effect"))
            semantic_output_ids = sorted({
                str(row.get("output_id") or "").strip().casefold()
                for row in list(
                    (formal_by_id[goal_id].get("requested_effect") or {}).get(
                        "requested_outputs"
                    )
                    or []
                )
                if isinstance(row, dict) and str(row.get("output_id") or "").strip()
            })
            role = (
                "completion" if effect_identity in completion_effects
                else "support" if effect_identity in support_effects
                else "runtime_bound"
            )
            if len(eligible_goals) > 1:
                # A shared Tool call may attach completion evidence to multiple
                # Goals only through the single completion output explicitly
                # named by CapabilityCompletionContract.  Other produced values
                # remain Ledger facts and cannot become cross-Goal proof by
                # association.
                if role != "completion" or str(output.name) != primary_completion_output:
                    continue
            target_binding = _target_binding(
                entry,
                ledger=merged_ledger,
                state=state,
            )
            base = {
                "version": GOAL_OUTPUT_REF_VERSION,
                "status": _ACTIVE_STATUS,
                "verified": True,
                "producer_goal_id": goal_id,
                "producer_goal_ids": eligible_goals,
                "producer_tool_name": str(tool_name),
                "source_effect_id": str(effect_id or "") or None,
                "goal_effect_role": role,
                "output_name": str(output.name),
                "output_type": str(output.type_name),
                "output_authority": str(output.authority),
                "semantic_output_ids": semantic_output_ids,
                "output_cardinality": str(
                    formal_by_id[goal_id].get("expected_result_cardinality") or "unknown"
                ),
                "producer_target_identity": derive_goal_target_identity(
                    formal_by_id[goal_id]
                ),
                "completion_proof_output_name": (
                    primary_completion_output if role == "completion" else None
                ),
                "completion_proof": bool(
                    output.completion_proof
                    and role == "completion"
                    and (
                        len(eligible_goals) == 1
                        or str(output.name) == primary_completion_output
                    )
                ),
                "artifact_ref": artifact_ref,
                "target_binding": target_binding,
                "runtime_target_identity_digest": _digest(target_binding),
                "scope": deepcopy(entry.get("scope") or scope_for_state(state)),
                "semantic_contract_id": str(semantic.get("semantic_contract_id") or ""),
                "semantic_digest": str(semantic.get("semantic_digest") or ""),
                "capability_registry_version": str(capability_registry.version or ""),
                "created_turn": int(state.get("turn_index") or semantic.get("turn") or 0),
                "expires_at": expires_at,
            }
            identity = _digest({
                "producer_goal_id": goal_id,
                "output_type": base["output_type"],
                "artifact_ref": base["artifact_ref"],
                "semantic_contract_id": base["semantic_contract_id"],
            })
            base["goal_output_ref_id"] = f"goal-output:{identity[:24]}"
            candidate = _with_digest(base)
            refs = [
                row for row in refs
                if str(row.get("goal_output_ref_id") or "") != candidate["goal_output_ref_id"]
            ]
            refs.append(candidate)
    return refs


def reusable_goal_outputs_for_goal(
    *,
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
    goal_plan: dict[str, Any],
    dependency_goal_ids: set[str],
) -> tuple[dict[str, set[str]], list[dict[str, Any]], list[str]]:
    refs, errors = normalize_goal_output_refs(
        state.get("goal_output_refs") or [],
        state=state,
        capability_registry=capability_registry,
    )
    semantic = (
        state.get("frozen_semantic_contract")
        if isinstance(state.get("frozen_semantic_contract"), dict)
        else {}
    )
    formal_by_id = {
        str(row.get("goal_id") or ""): row
        for row in semantic_goals(semantic)
        if str(row.get("goal_id") or "")
    }
    consumer_goal_id = str(goal_plan.get("goal_id") or "")
    consumer_goal = formal_by_id.get(consumer_goal_id)
    consumer_target_identity = derive_goal_target_identity(consumer_goal)
    typed_contract = semantic.get("dependency_authority") == GOAL_INPUT_BINDING_AUTHORITY
    eligible_refs: list[dict[str, Any]] = []
    target_errors: list[str] = []
    for row in refs:
        producer_goal_id = str(row.get("producer_goal_id") or "")
        if producer_goal_id not in dependency_goal_ids:
            continue
        producer_goal = formal_by_id.get(producer_goal_id)
        if typed_contract:
            matching_bindings = [
                binding
                for binding in list((consumer_goal or {}).get("input_bindings") or [])
                if isinstance(binding, dict)
                and isinstance(binding.get("source"), dict)
                and binding["source"].get("kind") == "current_goal_output"
                and str(binding["source"].get("producer_goal_id") or "")
                == producer_goal_id
                and str(binding["source"].get("output_id") or "").casefold()
                in {
                    str(value or "").casefold()
                    for value in list(row.get("semantic_output_ids") or [])
                }
            ]
            if len(matching_bindings) != 1:
                target_errors.append("GOAL_OUTPUT_REF_INPUT_BINDING_MISMATCH")
                continue
            binding = matching_bindings[0]
            produced_cardinality = str(row.get("output_cardinality") or "unknown")
            required_cardinality = str(binding.get("expected_cardinality") or "unknown")
            if (
                required_cardinality not in {"unknown", produced_cardinality}
                or produced_cardinality not in {"single", "collection", "unknown"}
            ):
                target_errors.append("GOAL_OUTPUT_REF_CARDINALITY_MISMATCH")
                continue
        compatibility = prove_goal_target_compatibility(
            [goal for goal in (producer_goal, consumer_goal) if isinstance(goal, dict)]
        )
        status = str(compatibility.get("status") or "")
        if status == "SAME":
            eligible_refs.append(row)
        elif status == "DIFFERENT":
            target_errors.append("GOAL_OUTPUT_REF_TARGET_MISMATCH")
        elif (
            typed_contract
            and (
                (
                    str(binding.get("port") or "") == "target"
                    and str(binding.get("relation_kind") or "") == "result_reference"
                )
                or str(binding.get("relation_kind") or "") == "result_value_input"
            )
            and str(consumer_target_identity.get("status") or "") != "PROVEN"
            and str((row.get("target_binding") or {}).get("resource_type") or "")
            == str(
                ((consumer_goal or {}).get("requested_effect") or {}).get("subject_type")
                or ((consumer_goal or {}).get("requested_effect") or {}).get("object_type")
                or ""
            )
            and str((row.get("target_binding") or {}).get("resource_id") or "")
        ):
            # The consumer deliberately receives its target identity from the
            # producer output. Requiring a second independent target candidate
            # here would make the symbolic binding impossible to execute.
            eligible_refs.append(row)
        else:
            target_errors.append("GOAL_OUTPUT_REF_TARGET_UNPROVEN")
    errors = sorted(set([*errors, *target_errors]))
    reusable_by_tool: dict[str, set[str]] = {}
    consumed: list[dict[str, Any]] = []
    path_tools = {
        str(step.get("tool_name") or "")
        for path in list(goal_plan.get("candidate_paths") or [])
        if isinstance(path, dict) and str(path.get("status") or "") == "closed"
        for step in list(path.get("steps") or [])
        if isinstance(step, dict) and str(step.get("tool_name") or "")
    }
    for tool_name in sorted(path_tools):
        contract = capability_registry.contract_for_tool(tool_name)
        if contract is None or contract.contract_version != "2" or contract.planning_contract is None:
            continue
        produced_types = {str(output.type_name) for output in contract.planning_contract.produces}
        matches = [row for row in eligible_refs if str(row.get("output_type") or "") in produced_types]
        if not matches:
            continue
        reusable_by_tool.setdefault(tool_name, set()).update(str(row.get("goal_output_ref_id") or "") for row in matches)
        consumed.extend(matches)
    unique_consumed = {
        str(row.get("goal_output_ref_id") or row.get("ref_digest") or ""): row
        for row in consumed
        if str(row.get("goal_output_ref_id") or row.get("ref_digest") or "")
    }
    return reusable_by_tool, list(unique_consumed.values()), errors


__all__ = [
    "GOAL_OUTPUT_REF_VERSION",
    "normalize_goal_output_refs",
    "record_goal_outputs_from_tool_result",
    "reusable_goal_outputs_for_goal",
    "validate_goal_output_ref",
]
