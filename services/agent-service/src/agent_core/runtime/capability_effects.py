from __future__ import annotations

"""Exact business-effect identities for the current deployment.

This module projects module-owned ``ToolCapabilityContract`` effect identities
into a bounded per-turn capability surface. It deliberately contains no
natural-language matching. The model proposes an open ``requested_effect``;
Runtime compares that structured identity with completion/support identities
registered by each business module. Unknown effects remain unknown and are
never coerced to a nearby capability.

Runtime owns only indexing and exact proof. Business modules own the effect
vocabulary they implement; Tool names, descriptions and similarity are never
used as formal capability identity.
"""

from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from itertools import combinations
import json
from typing import Any, Iterable

from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.kernel.semantic_contract import semantic_goals

CAPABILITY_EFFECT_INDEX_VERSION = "capability-effect-index@2"
SEMANTIC_EFFECT_IDENTITY_VERSION = "semantic-effect@2"


def _clean(value: Any) -> str:
    return str(value or "").strip().casefold()


_SEMANTIC_OUTPUT_PREFIX = "semantic-output:"
_SEMANTIC_OUTPUT_SET_PREFIX = "semantic-output-set:"


def requested_semantic_output_ids(raw: Any) -> tuple[str, ...]:
    row = raw if isinstance(raw, dict) else {}
    values = row.get("requested_outputs")
    if not isinstance(values, list):
        return ()
    result: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        output_id = _clean(item.get("output_id"))
        if output_id and output_id not in result:
            result.append(output_id)
    return tuple(sorted(result))


def _semantic_output_identity(output_ids: Iterable[str]) -> str:
    values = tuple(sorted(dict.fromkeys(_clean(value) for value in output_ids if _clean(value))))
    if not values:
        return ""
    if len(values) == 1:
        return f"{_SEMANTIC_OUTPUT_PREFIX}{values[0]}"
    return f"{_SEMANTIC_OUTPUT_SET_PREFIX}{'|'.join(values)}"


def canonical_effect_identity(raw: Any) -> str:
    """Return one exact identity without language inference or similarity.

    New turns are keyed by the frozen requested semantic-output set. Legacy
    ``domain.operation:object_type`` identities remain readable only as a
    migration compatibility representation.
    """
    outputs = requested_semantic_output_ids(raw)
    if outputs:
        return _semantic_output_identity(outputs)
    row = raw if isinstance(raw, dict) else {}
    domain = _clean(row.get("domain")) or "open"
    operation = _clean(row.get("operation"))
    object_type = _clean(row.get("object_type")) or "unspecified"
    return f"{domain}.{operation}:{object_type}" if operation else ""


def canonical_semantic_effect_identity(raw: Any) -> str:
    """Return a collision-resistant v2 identity without changing legacy aliases."""
    row = raw if isinstance(raw, dict) else {}
    outputs = tuple(sorted(requested_semantic_output_ids(row)))
    payload = {
        "effect_kind": _clean(row.get("effect_kind")),
        "domain": _clean(row.get("domain")),
        "operation": _clean(row.get("operation")),
        "object_type": _clean(row.get("object_type")),
        "subject_type": _clean(row.get("subject_type") or row.get("object_type")),
        "requested_output_set": list(outputs),
    }
    if not any(payload.values()):
        return ""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{SEMANTIC_EFFECT_IDENTITY_VERSION}:{sha256(encoded.encode('utf-8')).hexdigest()}"


def effect_identity(domain: str, operation: str, object_type: str) -> str:
    return canonical_effect_identity(
        {"domain": domain, "operation": operation, "object_type": object_type}
    )


def _contract_effects(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        raw = str(value or "").strip().casefold()
        if not raw or ":" not in raw or "." not in raw.split(":", 1)[0]:
            continue
        if raw not in result:
            result.append(raw)
    return tuple(result)


def _legacy_semantic_aliases() -> dict[str, tuple[str, ...]]:
    try:
        from agent_core.modules.registry import current_module_registry
        return current_module_registry().legacy_semantic_output_aliases()
    except RuntimeError:
        return {}


@lru_cache(maxsize=256)
def _semantic_identities_for_output_ids(output_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Expand one exact output set once; alias/provider changes remain outside the cache."""
    identities: list[str] = []
    for size in range(1, len(output_ids) + 1):
        for subset in combinations(output_ids, size):
            identity = _semantic_output_identity(subset)
            if identity and identity not in identities:
                identities.append(identity)
    return tuple(identities)


def _semantic_identities_for_legacy_effects(values: Iterable[str]) -> tuple[str, ...]:
    aliases = _legacy_semantic_aliases()
    output_ids: list[str] = []
    for legacy in _contract_effects(values):
        for output_id in aliases.get(legacy, ()):
            if output_id not in output_ids:
                output_ids.append(output_id)
    # ModuleRegistry bounds one legacy alias to at most eight output IDs, so
    # exact subset identities remain finite and deterministic. This lets one
    # legacy broad logistics contract prove status, ETA, tracking, or an exact
    # requested combination without a model mapper. The expansion itself is
    # pure for one exact output set, so cache only that combinatorial step.
    return _semantic_identities_for_output_ids(tuple(sorted(output_ids)))


def completion_effects_for_contract(contract: Any) -> tuple[str, ...]:
    legacy = _contract_effects(getattr(contract, "completion_effects", ()) or ())
    semantic = _semantic_identities_for_legacy_effects(legacy)
    return tuple(dict.fromkeys((*legacy, *semantic)))


def support_effects_for_contract(contract: Any) -> tuple[str, ...]:
    legacy = _contract_effects(getattr(contract, "support_effects", ()) or ())
    semantic = _semantic_identities_for_legacy_effects(legacy)
    return tuple(dict.fromkeys((*legacy, *semantic)))


def _effect_semantic_guidance(contract: Any) -> dict[str, Any]:
    """Project bounded module-owned semantics without granting execution authority."""
    planning = getattr(contract, "planning_contract", None)
    target = getattr(planning, "target", None)
    bounded = lambda values: [str(value)[:96] for value in tuple(values or ())[:8] if str(value)]
    return {
        "planner_rule": str(getattr(contract, "planner_rule", "") or "")[:320],
        "target_cardinality": str(getattr(target, "cardinality", "") or "") or None,
        "target_resource_types": bounded(getattr(target, "resource_types", ()) if target is not None else ()),
        "discovery_examples": bounded(getattr(contract, "discovery_examples", ())),
        "exclusion_examples": bounded(getattr(contract, "exclusion_examples", ())),
    }


def _effect_index_row() -> dict[str, Any]:
    return {"completion_tools": [], "support_tools": [], "semantic_guidance": []}


def capability_effect_index(registry: CapabilityRegistry) -> dict[str, Any]:
    """Publish compact business-effect vocabulary, not tool implementation details."""

    grouped: dict[str, dict[str, Any]] = {}
    for tool_name in sorted(registry.tool_names()):
        contract = registry.contract_for_tool(tool_name)
        if contract is None or contract.execution_kind in {"unsupported", "clarification_read"}:
            continue
        for identity in completion_effects_for_contract(contract):
            row = grouped.setdefault(identity, _effect_index_row())
            row["completion_tools"].append(tool_name)
            guidance = _effect_semantic_guidance(contract)
            if guidance not in row["semantic_guidance"]:
                row["semantic_guidance"].append(guidance)
        for identity in support_effects_for_contract(contract):
            grouped.setdefault(identity, _effect_index_row())["support_tools"].append(tool_name)
    return {
        "version": CAPABILITY_EFFECT_INDEX_VERSION,
        "matching": "structured_identity_exact_only",
        "unknown_effect_policy": "preserve_goal_and_prove_absent",
        "effects": [
            {
                "requested_effect_identity": identity,
                "completion_tool_count": len(set(row["completion_tools"])),
                "support_tool_count": len(set(row["support_tools"])),
                "semantic_guidance": list(row.get("semantic_guidance") or []),
            }
            for identity, row in sorted(grouped.items())
        ],
    }


def _unsupported_tool(registry: CapabilityRegistry) -> str | None:
    for name in sorted(registry.tool_names()):
        contract = registry.contract_for_tool(name)
        if contract is not None and contract.execution_kind == "unsupported":
            return name
    return None


def discover_exact_effect_surface(
    registry: CapabilityRegistry,
    goals: Iterable[dict[str, Any]],
    *,
    verified_continuation_tools_by_goal: dict[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    """Build a bounded surface from exact structured Goal identities.

    Continuation hints may add only a tool whose registered contract already
    has an exact completion/support relation with the continued Goal.  They
    never bypass identity matching.
    """

    continuation_hints = verified_continuation_tools_by_goal or {}
    unsupported = _unsupported_tool(registry)
    selected: list[str] = []
    decisions: list[dict[str, Any]] = []
    unsupported_goal_ids: list[str] = []

    for raw_goal in goals:
        if not isinstance(raw_goal, dict):
            continue
        goal_id = str(raw_goal.get("goal_id") or "")
        requested = deepcopy(raw_goal.get("requested_effect")) if isinstance(
            raw_goal.get("requested_effect"), dict
        ) else {}
        identity = canonical_effect_identity(requested)
        completion: list[str] = []
        support: list[str] = []
        for name in sorted(registry.tool_names()):
            contract = registry.contract_for_tool(name)
            if contract is None or contract.execution_kind in {
                "unsupported",
                "clarification_read",
            }:
                continue
            if identity and identity in completion_effects_for_contract(contract):
                completion.append(name)
            if identity and identity in support_effects_for_contract(contract):
                support.append(name)

        hinted: list[str] = []
        for name in continuation_hints.get(goal_id, ()):
            contract = registry.contract_for_tool(str(name or ""))
            if contract is None:
                continue
            if identity in {
                *completion_effects_for_contract(contract),
                *support_effects_for_contract(contract),
            }:
                hinted.append(str(name))

        candidates = list(dict.fromkeys([*support, *completion, *hinted]))
        if completion:
            status = "exact_supported"
        elif candidates:
            # A prerequisite alone cannot prove the requested effect exists.
            status = "completion_capability_absent"
            unsupported_goal_ids.append(goal_id)
            if unsupported:
                candidates.append(unsupported)
        else:
            status = "absent_proven"
            unsupported_goal_ids.append(goal_id)
            if unsupported:
                candidates = [unsupported]

        selected.extend(candidates)
        decisions.append(
            {
                "goal_id": goal_id,
                "requested_effect": requested,
                "requested_effect_identity": identity or None,
                "status": status,
                "candidate_tools": list(dict.fromkeys(candidates)),
                "completion_tools": list(dict.fromkeys(completion)),
                "support_tools": list(dict.fromkeys(support)),
                "continuation_tools": list(dict.fromkeys(hinted)),
                "match_basis": "structured_identity_exact_only",
                "similarity_used": False,
            }
        )

    return {
        "version": "capability-surface@exact-effects-1",
        "registry_version": registry.version,
        "effect_index_version": CAPABILITY_EFFECT_INDEX_VERSION,
        "tool_names": list(dict.fromkeys(selected)),
        "goals": decisions,
        "unsupported_goal_ids": list(dict.fromkeys(unsupported_goal_ids)),
        "match_basis": "structured_identity_exact_only",
        "similarity_used": False,
    }


def goal_effect_match_proof(
    *,
    state: dict[str, Any],
    tool_name: str,
    goal_ids: Iterable[str],
    registry: CapabilityRegistry,
) -> dict[str, Any]:
    """Prove a candidate tool completes or supports every bound Goal."""

    contract = registry.contract_for_tool(tool_name)
    formal = {
        str(row.get("goal_id") or ""): row
        for row in semantic_goals(state)
        if str(row.get("goal_id") or "")
    }
    requested_ids = [str(value) for value in goal_ids if str(value)]
    rows: list[dict[str, Any]] = []
    all_allowed = bool(requested_ids)
    has_completion = False
    surface = state.get("capability_surface") if isinstance(state.get("capability_surface"), dict) else {}
    surface_by_goal = {
        str(row.get("goal_id") or ""): row
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }

    for goal_id in requested_ids:
        goal = formal.get(goal_id)
        identity = canonical_effect_identity((goal or {}).get("requested_effect"))
        role = "none"
        if contract is not None and identity in completion_effects_for_contract(contract):
            role = "completion"
            has_completion = True
        elif contract is not None and identity in support_effects_for_contract(contract):
            role = "support"
        elif contract is not None and contract.execution_kind == "unsupported":
            decision = surface_by_goal.get(goal_id, {})
            if str(decision.get("status") or "") in {
                "absent_proven",
                "completion_capability_absent",
            } and tool_name in {
                str(value) for value in list(decision.get("candidate_tools") or [])
            }:
                role = "unsupported_report"
        completion_proof_output = None
        if (
            contract is not None
            and contract.contract_version == "2"
            and contract.planning_contract is not None
            and contract.planning_contract.completion.mode == "tool_output"
            and role == "completion"
        ):
            completion_proof_output = str(
                contract.planning_contract.completion.output_name or ""
            ) or None
        multi_goal_completion_proof_required = len(requested_ids) > 1 and role == "completion"
        allowed = bool(
            goal is not None
            and identity
            and role != "none"
            and (not multi_goal_completion_proof_required or completion_proof_output)
        )
        all_allowed = all_allowed and allowed
        rows.append(
            {
                "goal_id": goal_id,
                "requested_effect_identity": identity or None,
                "role": role,
                "completion_proof_output": completion_proof_output,
                "multi_goal_completion_proof_required": multi_goal_completion_proof_required,
                "allowed": allowed,
            }
        )

    return {
        "version": "goal-effect-match-proof@1",
        "tool_name": tool_name,
        "capability_key": contract.key if contract else None,
        "goal_ids": requested_ids,
        "goals": rows,
        "allowed": bool(all_allowed),
        "completes_any_goal": has_completion,
        "matching": "structured_identity_exact_only",
        "similarity_used": False,
    }
