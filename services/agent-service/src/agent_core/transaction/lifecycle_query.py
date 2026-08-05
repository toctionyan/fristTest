from __future__ import annotations

"""Read-only transaction lifecycle query; never re-enters action preparation.

This module is the transaction continuation boundary for phrases such as “成功了吗” or
“刚才那个到哪一步了”.  It only resolves durable, scope-verified transaction
records; it never treats an old chat phrase as a new write authorization.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_core.kernel.outcome_contract import OutcomeFactory, OutcomeReadModel
from agent_core.storage.repositories.base import TransactionLifecycleRepository, TransactionScope
from agent_core.transaction.focus import get_focused_draft_id


_RECENT_WINDOW = timedelta(days=14)
_NON_ACTIONABLE_STATES = {"COMMITTED", "FAILED_FINAL", "EXPIRED", "REVOKED", "REQUIRES_REVIEW"}


@dataclass(frozen=True)
class TransactionReferenceResolution:
    draft_id: str | None
    mode: str
    candidates: tuple[dict[str, Any], ...] = ()


class ConversationContinuationResolver:
    """Resolves a status-query continuation without keyword-to-action routing.

    A model/tool proposal supplies the *kind* of request (lifecycle query).
    This resolver then uses durable candidates, current interaction state and
    strict tenant/user scope to decide whether one transaction may be queried
    or the user must select a record.
    """

    def __init__(self, transactions: TransactionLifecycleRepository) -> None:
        self._transactions = transactions

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _recent_candidates(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - _RECENT_WINDOW
        candidates: list[dict[str, Any]] = []
        for row in rows:
            state = str(row.get("draft_state") or "").upper()
            updated = self._parse_time(row.get("updated_at") or row.get("created_at"))
            # Nonterminal records remain candidates even if dates are absent;
            # terminal historical entries must be recent to avoid guessing a
            # stale transaction merely because it is newest in the database.
            if state in _NON_ACTIONABLE_STATES and updated is not None and updated < cutoff:
                continue
            candidates.append(dict(row))
        return candidates

    def resolve(
        self,
        *,
        state: dict[str, Any],
        scope: TransactionScope,
        explicit_handle: str | None = None,
    ) -> TransactionReferenceResolution:
        user_scope = TransactionScope(tenant_id=scope.tenant_id, user_id=scope.user_id, thread_id=None)
        handle = str(explicit_handle or "").strip()
        if handle:
            row = self._transactions.get_draft_for_scope(scope=user_scope, draft_id=handle)
            return TransactionReferenceResolution(handle if row else None, "explicit_handle")

        focused = get_focused_draft_id(state)
        if focused:
            row = self._transactions.get_draft_for_scope(scope=user_scope, draft_id=focused)
            if row:
                return TransactionReferenceResolution(focused, "focused_draft")

        # Thread helps describe the reference but is never an authorization
        # boundary.  Never choose a thread-local record if this user has other
        # current candidates: the customer must select one instead.
        user_rows = self._recent_candidates(
            self._transactions.list_drafts_for_scope(scope=user_scope, states=None, limit=20)
        )
        if len(user_rows) > 1:
            return TransactionReferenceResolution(None, "need_selection", tuple(user_rows[:6]))
        if len(user_rows) == 1:
            only = str(user_rows[0].get("draft_id") or "") or None
            mode = "user_recent_unique"
            if scope.thread_id and str(user_rows[0].get("thread_id") or "") == str(scope.thread_id):
                mode = "thread_recent_unique"
            return TransactionReferenceResolution(only, mode)
        return TransactionReferenceResolution(None, "none", ())


# Explicit alias used by callers that only need a durable transaction reference.
TransactionReferenceResolver = ConversationContinuationResolver


def lifecycle_from_draft_state(draft_state: str) -> str:
    mapping = {
        "NEEDS_INPUT": "collecting_input",
        "READY": "queued",
        "AWAITING_AUTHORIZATION": "awaiting_authority",
        "AUTHORIZED": "submitting",
        "COMMITTING": "submitting",
        "SUBMISSION_UNKNOWN": "submission_unknown",
        "RECONCILIATION_REQUIRED": "submission_unknown",
        "COMMITTED": "committed",
        "FAILED_RETRYABLE": "retryable_failure",
        "FAILED_FINAL": "failed",
        "EXPIRED": "closed",
        "REVOKED": "closed",
        "REQUIRES_REVIEW": "review_required",
    }
    return mapping.get(str(draft_state or "").upper(), "unknown")


class TransactionLifecycleQuery:
    def __init__(self, transactions: TransactionLifecycleRepository, *, outcome_factory: OutcomeFactory) -> None:
        self._transactions = transactions
        self._resolver = ConversationContinuationResolver(transactions)
        self._outcome = outcome_factory

    def query(
        self,
        *,
        state: dict[str, Any],
        explicit_handle: str | None = None,
        correlation_id: str | None = None,
    ) -> OutcomeReadModel:
        scope = TransactionScope(
            tenant_id=str(state.get("current_tenant_id") or "default"),
            user_id=str(state.get("current_user_id") or ""),
            thread_id=str(state.get("current_thread_id") or "") or None,
        )
        if not scope.user_id:
            return self._outcome(
                "failure",
                correlation_id=correlation_id,
                customer_safe_summary="当前无法确认办理记录所属用户，未执行任何业务操作。",
            )
        try:
            resolved = self._resolver.resolve(state=state, scope=scope, explicit_handle=explicit_handle)
        except Exception:
            return self._outcome(
                "system_unavailable",
                correlation_id=correlation_id,
                customer_safe_summary="当前无法查询办理状态，未创建或提交新的业务申请。请稍后刷新或在事务中心查看。",
                next_interaction="retry_later",
            )
        if not resolved.draft_id:
            if resolved.mode == "need_selection":
                choices = [
                    {
                        "draft_id": row.get("draft_id"),
                        "action_id": row.get("action_id"),
                        "draft_state": row.get("draft_state"),
                        "label": (row.get("projection") or {}).get("label") if isinstance(row.get("projection"), dict) else row.get("action_id"),
                    }
                    for row in resolved.candidates
                ]
                return self._outcome(
                    "clarification",
                    correlation_id=correlation_id,
                    customer_safe_summary="当前存在多笔可查询的办理记录，请说明要查看哪一笔。",
                    next_interaction="need_selection",
                    payload={"candidates": choices},
                )
            return self._outcome(
                "transaction_status",
                correlation_id=correlation_id,
                customer_safe_summary="当前没有可查询的办理记录；系统未确认创建或提交任何新的业务申请。",
                next_interaction="show_status",
                payload={"draft": None, "attempts": [], "receipt": None, "reference_mode": resolved.mode},
            )

        user_scope = TransactionScope(tenant_id=scope.tenant_id, user_id=scope.user_id, thread_id=None)
        draft = self._transactions.get_draft_for_scope(scope=user_scope, draft_id=resolved.draft_id)
        if not draft:
            return self._outcome(
                "transaction_status",
                correlation_id=correlation_id,
                customer_safe_summary="当前未找到可查询的办理记录；系统未创建或提交新的业务申请。",
                next_interaction="show_status",
                payload={"draft": None, "attempts": [], "receipt": None, "reference_mode": resolved.mode},
            )
        attempts = self._transactions.list_attempts_for_draft(scope=user_scope, draft_id=resolved.draft_id)
        receipt = self._transactions.get_latest_receipt_for_draft(scope=user_scope, draft_id=resolved.draft_id)
        state_name = str(draft.get("draft_state") or "")
        if state_name == "COMMITTED":
            message = "该办理记录已提交并确认完成。"
        elif state_name in {"SUBMISSION_UNKNOWN", "RECONCILIATION_REQUIRED", "COMMITTING"}:
            message = "该办理记录的提交结果仍在确认中，请勿重复提交。"
        elif state_name in {"NEEDS_INPUT", "AWAITING_AUTHORIZATION", "READY", "AUTHORIZED"}:
            message = "该办理记录尚未完成提交，仍需要在办理卡中补充信息或确认。"
        elif state_name in {"EXPIRED", "REVOKED", "REQUIRES_REVIEW"}:
            message = "该办理记录当前不能继续提交，可查看详情、取消或重新发起办理。"
        else:
            message = "已查询该办理记录的当前状态。"
        return self._outcome(
            "transaction_status",
            correlation_id=correlation_id,
            evidence_handles=[resolved.draft_id],
            customer_safe_summary=message,
            next_interaction="show_status",
            payload={
                "draft": dict(draft),
                "attempts": [dict(row) for row in attempts],
                "receipt": dict(receipt) if isinstance(receipt, dict) else None,
                "lifecycle": lifecycle_from_draft_state(state_name),
                "reference_mode": resolved.mode,
            },
        )
