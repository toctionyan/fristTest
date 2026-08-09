"""Privileged bridge for structured commands entering formal lifecycle nodes.

It resumes normal routing and never interprets language or creates commands.
"""
from __future__ import annotations

from typing import Any, Callable


def persist_checkpoint_migration(
    *,
    graph: Any,
    config: dict[str, Any],
    migration_patch: dict[str, Any],
) -> None:
    """Persist a deterministic checkpoint-schema migration through the sole writer.

    The caller owns migration validation; this function owns only the privileged
    checkpoint write.  It does not resume the graph or interpret user language.
    """
    if not migration_patch:
        return
    graph.update_state(config, dict(migration_patch))


class LifecycleCommandRunner:
    """Privileged bridge from application commands to formal lifecycle nodes.

    Only this class may persist an externally-triggered structured transition
    using ``graph.update_state(..., as_node=...)``.  All callers receive the
    fully resumed state, not a raw low-level node patch.
    """

    def __init__(self, service: Any) -> None:
        self._service = service

    def _transaction_execution(self) -> Any | None:
        """Return composed dependencies; isolated facade doubles may omit them."""
        runtime_deps = getattr(self._service, "runtime_deps", None)
        return getattr(runtime_deps, "transaction_execution", None)

    @staticmethod
    def _invoke_transaction_node(node: Callable[..., dict[str, Any]], state: dict[str, Any], deps: Any | None) -> dict[str, Any]:
        if deps is None:
            return node(state)
        return node(state, transaction_execution=deps)

    @staticmethod
    def _state_values(graph: Any, config: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
        getter = getattr(graph, "get_state", None)
        if not callable(getter):
            return dict(fallback)
        snapshot = getter(config)
        values = dict(getattr(snapshot, "values", {}) or {})
        return values or dict(fallback)

    def _resume_from_named_boundary(
        self,
        *,
        graph: Any,
        config: dict[str, Any],
        node_name: str,
        update: dict[str, Any],
        base_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a formal node update and let the compiled graph route it.

        LangGraph schedules outgoing edges from the supplied ``as_node``.  A
        subsequent ``invoke(None, config=...)`` resumes that scheduled work,
        so classification/finalization are not skipped by a UI/API path.  A
        minimal test double may not implement ``invoke``; it still receives a
        fully classified patch because the public wrapper already appended the
        formal ExecutionDisposition.
        """
        ingress_keys = ("current_thread_id", "current_user_id", "current_role", "current_tenant_id", "current_subject", "turn_index", "ledger_schema_version")
        verified_ingress = {key: base_state[key] for key in ingress_keys if key in base_state}
        merged = {**verified_ingress, **dict(update or {})}
        graph.update_state(config, merged, as_node=node_name)
        invoke = getattr(graph, "invoke", None)
        if callable(invoke):
            resumed = invoke(None, config=config)
            if isinstance(resumed, dict):
                return dict(resumed)
        return self._state_values(graph, config, merged)

    def advance_gateway(
        self,
        *,
        graph: Any,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the public action-gateway node and resume formal routing."""
        from agent_core.lifecycle.nodes import action_gateway_node

        update = self._invoke_transaction_node(
            action_gateway_node,
            state,
            self._transaction_execution(),
        )
        return self._resume_from_named_boundary(
            graph=graph,
            config=config,
            node_name="action_gateway",
            update=update,
            base_state=state,
        )

    def commit_if_pending(
        self,
        *,
        graph: Any,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit only a checkpoint already advanced to ``commit_action``."""
        if str(state.get("phase") or "") != "commit_action":
            return dict(state)
        from agent_core.lifecycle.nodes import commit_action_node

        update = self._invoke_transaction_node(
            commit_action_node,
            state,
            self._transaction_execution(),
        )
        return self._resume_from_named_boundary(
            graph=graph,
            config=config,
            node_name="commit_action",
            update=update,
            base_state=state,
        )

    def reconcile_submission(
        self,
        *,
        graph: Any,
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Run same-idempotency-key reconciliation through formal routing."""
        from agent_core.lifecycle.nodes import reconcile_submission_node

        update = self._invoke_transaction_node(
            reconcile_submission_node,
            state,
            self._transaction_execution(),
        )
        return self._resume_from_named_boundary(
            graph=graph,
            config=config,
            node_name="reconcile_submission",
            update=update,
            base_state=state,
        )
