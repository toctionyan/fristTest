from __future__ import annotations

"""ContextBundle: bounded model context with explicit semantic ownership.

The bundle contains the recent raw conversation first, then verified tool
observations, prior customer-visible result references, active transaction
state and compact verified facts.  It is rebuilt per model call and has no
power to choose an entity, action or capability.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Protocol

from agent_core.context.audit_inspection import build_audit_index
from agent_core.context.conversation_protocol import compile_provider_context
from agent_core.context.visible_result_refs import visible_result_refs_from_ledger
from agent_core.context.projection import partition_tool_trace
from agent_core.context.state_projection import (
    active_goal_blockers,
    clarification_context_projection,
    goal_records_context_projection,
)
from agent_core.ledger import ledger_cards, scope_for_state



@dataclass(frozen=True)
class ContextTransactionScope:
    tenant_id: str
    user_id: str
    thread_id: str | None = None


class TransactionContextRepository(Protocol):
    def list_drafts_for_scope(
        self,
        *,
        scope: ContextTransactionScope,
        states: set[str] | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]: ...


_ACTIVE_TRANSACTION_STATES = {
    "NEEDS_INPUT", "READY", "AWAITING_AUTHORIZATION", "AUTHORIZED",
    "COMMITTING", "SUBMISSION_UNKNOWN", "FAILED_RETRYABLE",
    "RECONCILIATION_REQUIRED",
}


def _scope(state: dict[str, Any]) -> ContextTransactionScope:
    raw = scope_for_state(state)
    return ContextTransactionScope(
        tenant_id=str(raw.get("tenant_id") or "default"),
        user_id=str(raw.get("user_id") or state.get("current_user_id") or ""),
        thread_id=str(raw.get("thread_id") or state.get("current_thread_id") or "") or None,
    )


def _content(value: Any, *, limit: int = 1200) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
        except Exception:
            text = str(value)
    return text[:limit]


def _message_role(message: Any) -> str:
    name = message.__class__.__name__
    if name == "HumanMessage":
        return "user"
    if name == "ToolMessage":
        return "tool"
    if name == "AIMessage":
        return "assistant"
    return "other"


def recent_conversation_window(state: dict[str, Any], *, limit: int = 12) -> tuple[list[dict[str, Any]], int]:
    compiled = compile_provider_context(
        state.get("messages") or [],
        max_messages=max(12, int(limit) * 8),
        max_chars=48_000,
        max_exchanges=max(1, int(limit)),
        compact_completed_history=True,
    )
    selected = list(compiled.messages)
    return [
        {
            "role": _message_role(message),
            "content": _content(getattr(message, "content", "")),
            "tool_name": str(getattr(message, "name", "") or "") or None,
        }
        for message in selected
    ], compiled.omitted_message_count


def _handles_from_result(result: dict[str, Any]) -> list[str]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    handles: list[str] = []
    for key, value in data.items():
        if key.endswith("_handle") and value:
            handles.append(str(value))
        elif key.endswith("_handles") and isinstance(value, list):
            handles.extend(str(item) for item in value if item)
    for entry in result.get("entries") or []:
        if isinstance(entry, dict) and entry.get("handle"):
            handles.append(str(entry["handle"]))
    return list(dict.fromkeys(handles))


def recent_tool_observations(state: dict[str, Any], *, limit: int = 6) -> tuple[list[dict[str, Any]], int]:
    """Compatibility view containing verified observations only.

    Failed/rejected calls are kept in ``execution_diagnostics`` so they remain
    useful for recovery without becoming user intent, target authority or a
    business fact in the next semantic compile.
    """
    trace = [row for row in state.get("tool_trace") or [] if isinstance(row, dict)]
    omitted = max(0, len(trace) - max(0, int(limit)))
    verified, _diagnostics = partition_tool_trace(trace, limit=limit)
    return verified, omitted


def recent_execution_diagnostics(state: dict[str, Any], *, limit: int = 6) -> tuple[list[dict[str, Any]], int]:
    trace = [row for row in state.get("tool_trace") or [] if isinstance(row, dict)]
    omitted = max(0, len(trace) - max(0, int(limit)))
    _verified, diagnostics = partition_tool_trace(trace, limit=limit)
    return diagnostics, omitted


def _active_transactions(
    state: dict[str, Any], *, transactions: TransactionContextRepository, trace_logger: Any | None
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    try:
        drafts = transactions.list_drafts_for_scope(scope=_scope(state), states=_ACTIVE_TRANSACTION_STATES, limit=20)
    except Exception as exc:
        if trace_logger is not None:
            try:
                trace_logger.log_event(
                    str(state.get("current_thread_id") or ""),
                    str(state.get("current_user_id") or "") or None,
                    "context_bundle_transactions_unavailable",
                    node="context_bundle",
                    output_data={"error_type": exc.__class__.__name__},
                )
            except Exception:
                pass
        return [], {"transactions": "unavailable"}
    rows: list[dict[str, Any]] = []
    for draft in drafts:
        projection = draft.get("projection") if isinstance(draft.get("projection"), dict) else {}
        target = projection.get("target") if isinstance(projection.get("target"), dict) else {}
        required_inputs = projection.get("required_inputs") if isinstance(projection.get("required_inputs"), list) else []
        rows.append(
            {
                "draft_id": str(draft.get("draft_id") or ""),
                "action_id": str(draft.get("action_id") or ""),
                "draft_state": str(draft.get("draft_state") or ""),
                "target_handle": str(projection.get("target_handle") or draft.get("target_handle") or target.get("handle") or "") or None,
                "target_summary": str(projection.get("label") or projection.get("target_label") or "") or None,
                "required_inputs": [str(item.get("name") or item) for item in required_inputs if item],
                "revision": draft.get("draft_revision"),
            }
        )
    return rows, {"transactions": "ok"}


def _verified_fact_summary(state: dict[str, Any], *, limit: int = 12) -> list[dict[str, Any]]:
    cards = ledger_cards(state.get("artifact_ledger") or [], scope=scope_for_state(state))
    facts = [
        {
            "handle": row.get("handle"),
            "resource_type": row.get("type"),
            "label": row.get("label"),
            "freshness_version": row.get("version"),
            "updated_turn": row.get("updated_turn"),
        }
        for row in cards.get("artifacts") or []
        if isinstance(row, dict)
    ]
    return facts[:max(0, int(limit))]


@dataclass(frozen=True)
class ContextBundleBuilder:
    transactions: TransactionContextRepository
    trace_logger: Any | None = None
    conversation_window_limit: int = 12
    tool_observation_limit: int = 6
    visible_result_ref_limit: int = 12

    def build(self, state: dict[str, Any]) -> dict[str, Any]:
        conversation, omitted_messages = recent_conversation_window(state, limit=self.conversation_window_limit)
        observations, omitted_observations = recent_tool_observations(state, limit=self.tool_observation_limit)
        diagnostics, omitted_diagnostics = recent_execution_diagnostics(
            state, limit=self.tool_observation_limit
        )
        active_transaction_state, context_health = _active_transactions(
            state, transactions=self.transactions, trace_logger=self.trace_logger
        )
        refs = visible_result_refs_from_ledger(
            state.get("artifact_ledger") or [], state=state, limit=self.visible_result_ref_limit
        )
        audit_index = build_audit_index(state.get("conversation_event_log") or [])
        bundle = {
            "bundle_version": "context_projection.v2",
            "semantic_owner": "llm",
            "runtime_auto_select_target": False,
            "runtime_auto_switch_target": False,
            "current_turn": int(state.get("turn_index") or 0),
            "recent_conversation_window": conversation,
            # The legacy key now aliases verified observations only.
            "recent_tool_observations": observations,
            "verified_tool_observations": observations,
            "execution_diagnostics": diagnostics,
            "visible_result_refs": refs,
            "active_transaction_state": active_transaction_state,
            "verified_fact_summary": _verified_fact_summary(state),
            # Multiple blockers may coexist and each remains goal-scoped.
            "goal_blockers": active_goal_blockers(state),
            "goal_records": goal_records_context_projection(state),
            "clarification_context": clarification_context_projection(state),
            "context_health": context_health,
            "omitted_context_audit": {
                "omitted_message_count": omitted_messages,
                "omitted_tool_observation_count": omitted_observations,
                "omitted_execution_diagnostic_count": omitted_diagnostics,
                "audit_index": audit_index,
                "expired_or_unavailable_result_refs_not_injected": True,
            },
        }
        digest_source = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        bundle["context_digest"] = sha256(digest_source.encode("utf-8")).hexdigest()
        return bundle


def build_context_bundle(
    state: dict[str, Any], *, transactions: TransactionContextRepository, trace_logger: Any | None = None
) -> dict[str, Any]:
    return ContextBundleBuilder(transactions=transactions, trace_logger=trace_logger).build(state)


def render_context_bundle(
    bundle: dict[str, Any],
    *,
    include_conversation: bool = True,
    include_digest: bool = True,
) -> str:
    """Render a deterministic ContextBundle projection for a model call.

    The durable bundle keeps recent dialogue for audit/debug consumers.  The
    agent loop already sends that dialogue as native provider messages, where
    role and tool-call linkage are preserved.  Callers can therefore omit the
    duplicate JSON conversation (and the audit-only digest) without weakening
    model context.
    """
    projection = dict(bundle)
    if not include_conversation:
        projection.pop("recent_conversation_window", None)
    if not include_digest:
        projection.pop("context_digest", None)
    return json.dumps(projection, ensure_ascii=False, separators=(",", ":"), default=str)
