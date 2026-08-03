from __future__ import annotations

"""State Schema v2 cutover and one-time legacy checkpoint migration.

The migration never reinterprets user language.  It only promotes legacy data
when the old checkpoint already contains explicit structured effect/goal
identity.  Ambiguous legacy state fails closed and requires a new conversation.
"""

from copy import deepcopy
from typing import Any

from agent_core.kernel.state_schema_contract import (
    CURRENT_STATE_SCHEMA_VERSION,
    LEGACY_STATE_SCHEMA_VERSION
)
from agent_core.lifecycle.goal_lifecycle import apply_semantic_contract_to_goal_records
from agent_core.lifecycle.plan_execution import project_grounded_execution_plan
from agent_core.lifecycle.semantic_contract import (
    freeze_semantic_contract,
    semantic_contract_integrity,
)

RETIRED_TOP_LEVEL_FIELDS = (
    "turn_goal_plan",
    "workflow_plan",
    "pending_clarification",
)


class LegacyStateRestartRequired(ValueError):
    code = "LEGACY_STATE_REQUIRES_RESTART"

    def __init__(self, reason: str, *, details: dict[str, Any] | None = None) -> None:
        self.reason = str(reason or "legacy_state_not_safely_migratable")
        self.details = deepcopy(details or {})
        super().__init__(f"{self.code}:{self.reason}")


def _legacy_goals(plan: Any) -> list[dict[str, Any]]:
    if not isinstance(plan, dict):
        return []
    return [deepcopy(row) for row in list(plan.get("goals") or []) if isinstance(row, dict)]


def _migrate_turn_goal_plan(state: dict[str, Any]) -> dict[str, Any] | None:
    existing = state.get("frozen_semantic_contract")
    if isinstance(existing, dict):
        integrity = semantic_contract_integrity(existing)
        if not integrity.get("ok"):
            raise LegacyStateRestartRequired(
                "existing_frozen_semantic_contract_invalid",
                details={"integrity": integrity},
            )
        return deepcopy(existing)

    legacy = state.get("turn_goal_plan")
    goals = _legacy_goals(legacy)
    if not goals:
        return None
    missing_effect = [
        str(row.get("goal_id") or f"index:{index}")
        for index, row in enumerate(goals)
        if not isinstance(row.get("requested_effect"), dict)
        or not str((row.get("requested_effect") or {}).get("operation") or "").strip()
    ]
    missing_identity = [
        str(row.get("goal_id") or f"index:{index}")
        for index, row in enumerate(goals)
        if not str(row.get("goal_id") or "").strip()
        or not str(row.get("description") or "").strip()
        or not str(row.get("evidence_span") or "").strip()
    ]
    if missing_effect or missing_identity:
        raise LegacyStateRestartRequired(
            "legacy_goal_plan_lacks_explicit_semantic_identity",
            details={
                "missing_requested_effect_goal_ids": missing_effect,
                "missing_identity_goal_ids": missing_identity,
            },
        )
    return freeze_semantic_contract(
        turn=int((legacy or {}).get("turn") or state.get("turn_index") or 0),
        user_text=str((legacy or {}).get("user_text") or state.get("current_user_input") or ""),
        summary=str((legacy or {}).get("summary") or "legacy checkpoint migration"),
        goals=goals,
        alignment_proof={
            "verdict": "legacy_structured_state_import",
            "authority": "migration_only_no_language_reinterpretation",
            "source_contract": str((legacy or {}).get("version") or "unknown"),
        },
    )


def _legacy_blockers(state: dict[str, Any], contract: dict[str, Any] | None) -> list[dict[str, Any]]:
    existing = [deepcopy(row) for row in list(state.get("goal_blockers") or []) if isinstance(row, dict)]
    pending = state.get("pending_clarification")
    if not isinstance(pending, dict) or str(pending.get("status") or "").lower() not in {"pending", "resuming"}:
        return existing

    known_goals = {
        str(row.get("goal_id") or ""): row
        for row in list((contract or {}).get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }
    suspended = [row for row in list(pending.get("suspended_goals") or []) if isinstance(row, dict)]
    if not suspended and str(pending.get("goal_id") or ""):
        suspended = [{"goal_id": str(pending.get("goal_id") or "")}]
    if not suspended:
        raise LegacyStateRestartRequired("legacy_pending_clarification_has_no_goal_binding")

    additions: list[dict[str, Any]] = []
    for index, row in enumerate(suspended):
        goal_id = str(row.get("goal_id") or "").strip()
        if not goal_id or goal_id not in known_goals:
            raise LegacyStateRestartRequired(
                "legacy_pending_clarification_unknown_goal",
                details={"goal_id": goal_id or None},
            )
        goal = known_goals[goal_id]
        additions.append({
            "blocker_id": str(pending.get("clarification_id") or pending.get("checkpoint_id") or f"legacy-blocker:{goal_id}:{index}"),
            "goal_id": goal_id,
            "status": "OPEN",
            "missing_kind": str(pending.get("missing_kind") or "condition"),
            "question": str(pending.get("question") or ""),
            "reason": str(pending.get("reason") or "legacy checkpoint clarification"),
            "created_turn": int(pending.get("created_turn") or state.get("turn_index") or 0),
            "updated_turn": int(state.get("turn_index") or 0),
            "requested_effect": deepcopy(goal.get("requested_effect")),
            "source_user_request": str(pending.get("user_request") or ""),
            "completion_tool_names": [
                str(name) for name in list(row.get("completion_tool_names") or []) if str(name)
            ],
            "authority": "migrated_goal_scoped_orchestration_blocker_not_business_fact",
            "migration_source": str(pending.get("version") or "legacy_pending_clarification"),
        })
    by_id = {str(row.get("blocker_id") or ""): row for row in existing if str(row.get("blocker_id") or "")}
    for row in additions:
        by_id[str(row["blocker_id"])] = row
    return list(by_id.values())


def _inferred_schema_version(source: dict[str, Any]) -> int:
    explicit = source.get("state_schema_version")
    if explicit is not None:
        return int(explicit)
    # A fresh invocation envelope is already Schema v2 even though it contains
    # request-scoped fields.  Only explicit retired-state evidence may classify
    # an unversioned payload as a legacy checkpoint.
    if any(source.get(key) is not None for key in RETIRED_TOP_LEVEL_FIELDS):
        return LEGACY_STATE_SCHEMA_VERSION
    return CURRENT_STATE_SCHEMA_VERSION


def migrate_checkpoint_state(state: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    source = deepcopy(state or {})
    from_version = _inferred_schema_version(source)
    if from_version > CURRENT_STATE_SCHEMA_VERSION:
        raise LegacyStateRestartRequired(
            "checkpoint_schema_newer_than_runtime",
            details={"checkpoint_version": from_version, "runtime_version": CURRENT_STATE_SCHEMA_VERSION},
        )

    changed = from_version != CURRENT_STATE_SCHEMA_VERSION
    migrated_fields: list[str] = []
    rederived_fields: list[str] = []
    discarded_fields: list[str] = []
    if from_version < CURRENT_STATE_SCHEMA_VERSION:
        pending = source.get("pending_clarification")
        has_active_pending = bool(
            isinstance(pending, dict)
            and str(pending.get("status") or "").lower() in {"pending", "resuming"}
        )
        existing_contract = source.get("frozen_semantic_contract")
        needs_semantic_migration = bool(isinstance(existing_contract, dict) or has_active_pending)
        contract = _migrate_turn_goal_plan(source) if needs_semantic_migration else None
        if contract is not None:
            source["frozen_semantic_contract"] = contract
            migrated_fields.append("turn_goal_plan->frozen_semantic_contract")
        blockers = _legacy_blockers(source, contract)
        if blockers != list(source.get("goal_blockers") or []):
            source["goal_blockers"] = blockers
            migrated_fields.append("pending_clarification->goal_blockers")
        if has_active_pending and contract is not None:
            records = apply_semantic_contract_to_goal_records(
                source.get("goal_records") or [],
                contract,
                turn=int(source.get("turn_index") or contract.get("turn") or 0),
            )
            source["goal_records"] = records
            migrated_fields.append("legacy_active_goals->goal_records")
        elif isinstance(source.get("turn_goal_plan"), dict):
            discarded_fields.append("turn_goal_plan:completed_same_turn_projection")

        # Same-turn workflow projections are not durable authority.  V2 rebuilds
        # them from the next frozen semantic contract and plan definition/run.
        for key in ("workflow_plan", "grounded_execution_plan"):
            if isinstance(source.get(key), dict) and not (
                isinstance(source.get("frozen_plan_definition"), dict)
                and isinstance(source.get("plan_run"), dict)
            ):
                discarded_fields.append(f"{key}:non_authoritative_same_turn_projection")
                source[key] = None

    # Additive transaction-focus cutover inside State Schema v2. Older
    # checkpoints contain only active_draft_id. Once focused_draft_id exists,
    # even an explicit null is authoritative and the compatibility projection
    # is deterministically synchronized so stale legacy values cannot revive a
    # terminal interaction.
    if "focused_draft_id" not in source:
        legacy_focus = str(source.get("active_draft_id") or "").strip() or None
        source["focused_draft_id"] = legacy_focus
        if legacy_focus is not None:
            changed = True
            migrated_fields.append("active_draft_id->focused_draft_id")
    canonical_focus = str(source.get("focused_draft_id") or "").strip() or None
    if source.get("active_draft_id") != canonical_focus:
        changed = True
        rederived_fields.append("active_draft_id:compatibility_projection_from_focused_draft_id")
    source["active_draft_id"] = canonical_focus

    for key in RETIRED_TOP_LEVEL_FIELDS:
        if source.get(key) is not None:
            changed = True
            discarded_fields.append(key)
        # LangGraph checkpoint updates cannot delete keys.  A null tombstone is
        # persisted once; no V2 node produces a non-null value again.
        source[key] = None

    definition = source.get("frozen_plan_definition")
    plan_run = source.get("plan_run")
    persisted_projection = source.get("grounded_execution_plan")
    if isinstance(definition, dict) and isinstance(plan_run, dict):
        try:
            canonical_projection = project_grounded_execution_plan(
                definition=definition,
                plan_run=plan_run,
            )
        except ValueError:
            if persisted_projection is not None:
                changed = True
                discarded_fields.append(
                    "grounded_execution_plan:authoritative_plan_pair_invalid"
                )
            source["grounded_execution_plan"] = None
        else:
            if persisted_projection != canonical_projection:
                changed = True
                rederived_fields.append(
                    "grounded_execution_plan:rederived_from_frozen_plan_definition_and_plan_run"
                )
            source["grounded_execution_plan"] = canonical_projection
    elif persisted_projection is not None:
        changed = True
        discarded_fields.append(
            "grounded_execution_plan:missing_authoritative_plan_pair"
        )
        source["grounded_execution_plan"] = None

    source["state_schema_version"] = CURRENT_STATE_SCHEMA_VERSION
    report = {
        "version": "state-schema-migration-report@1",
        "from_version": from_version,
        "to_version": CURRENT_STATE_SCHEMA_VERSION,
        "changed": bool(changed or migrated_fields or discarded_fields),
        "migrated_fields": migrated_fields,
        "rederived_fields": rederived_fields,
        "retired_fields": list(RETIRED_TOP_LEVEL_FIELDS),
        "discarded_non_authoritative_fields": list(dict.fromkeys(discarded_fields)),
        "authority": "deterministic_structured_checkpoint_migration_no_language_inference",
    }
    source["state_migration"] = report
    metrics = dict(source.get("legacy_compatibility_metrics") or {})
    metrics.update({
        "schema_v2_active": True,
        "legacy_checkpoint_migrations": int(metrics.get("legacy_checkpoint_migrations") or 0) + (1 if from_version < CURRENT_STATE_SCHEMA_VERSION else 0),
        "legacy_fallback_allowed": False,
        "retired_field_non_null_count": 0,
    })
    source["legacy_compatibility_metrics"] = metrics
    return source, report



__all__ = [
    "CURRENT_STATE_SCHEMA_VERSION",
    "LEGACY_STATE_SCHEMA_VERSION",
    "RETIRED_TOP_LEVEL_FIELDS",
    "LegacyStateRestartRequired",
    "migrate_checkpoint_state",
]
