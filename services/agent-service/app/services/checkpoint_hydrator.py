"""Checkpoint snapshot reader and State Schema v2 migration boundary."""
from __future__ import annotations

from typing import Any, Callable

from agent_core.lifecycle.state_schema import migrate_checkpoint_state
from app.services.lifecycle_command_runner import persist_checkpoint_migration


class CheckpointHydrator:
    """Load and deterministically migrate an existing checkpoint.

    The serving process may promote only explicit structured legacy state.  It
    never infers user intent or silently revives retired semantics.  Ambiguous
    legacy state raises ``LEGACY_STATE_REQUIRES_RESTART``.
    """

    def __init__(
        self,
        *,
        config_for_request: Callable[[str, str, str | None], dict[str, Any]],
        transactions: Any,
        trace_logger: Any,
    ) -> None:
        self._config_for_request = config_for_request
        self._transactions = transactions
        self._trace_logger = trace_logger

    def values(
        self,
        graph: Any,
        *,
        thread_id: str,
        user_id: str,
        tenant_id: str | None,
    ) -> dict[str, Any]:
        config = self._config_for_request(thread_id, user_id, tenant_id)
        snapshot = graph.get_state(config)
        persisted = dict(getattr(snapshot, "values", {}) or {})
        migrated, report = migrate_checkpoint_state(persisted)
        if report.get("changed"):
            migration_patch = {
                key: migrated.get(key)
                for key in (
                    "state_schema_version",
                    "state_migration",
                    "legacy_compatibility_metrics",
                    "frozen_semantic_contract",
                    "goal_records",
                    "goal_blockers",
                    "frozen_plan_definition",
                    "plan_run",
                    "grounded_execution_plan",
                    "turn_goal_plan",
                    "workflow_plan",
                    "pending_clarification",
                )
                if key in migrated
            }
            persist_checkpoint_migration(
                graph=graph,
                config=config,
                migration_patch=migration_patch,
            )
        logger = self._trace_logger
        if report.get("changed") and callable(getattr(logger, "log_event", None)):
            logger.log_event(
                thread_id,
                user_id,
                "checkpoint_state_schema_migrated",
                output_data=report,
            )
        return migrated
