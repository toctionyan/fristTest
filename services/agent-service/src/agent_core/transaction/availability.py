from __future__ import annotations

"""Fail-closed availability guard for the durable transaction repository.

The guard is deliberately small: it performs a scope-first read before any
transactional read/create/continue/submit entry.  A successful health probe is
not a transaction lock and does not replace normal repository error handling;
it only prevents the runtime from interpreting an unavailable repository as an
empty transaction history.
"""

from typing import Any

from agent_core.kernel.outcome_contract import OutcomeFactory, OutcomeReadModel
from agent_core.storage.repositories.base import TransactionLifecycleRepository, TransactionScope


def transaction_repository_unavailable_outcome(
    *,
    correlation_id: str | None = None,
    outcome_factory: OutcomeFactory,
    reason: str = "transaction_repository_unavailable",
) -> OutcomeReadModel:
    return outcome_factory(
        "system_unavailable",
        correlation_id=correlation_id,
        customer_safe_summary=(
            "当前无法确认或继续办理记录，系统未创建或提交新的业务申请。"
            "请稍后刷新，或在事务中心查看已有记录。"
        ),
        next_interaction="retry_later",
        payload={"reason": reason},
    )


def check_transaction_repository_available(
    transactions: TransactionLifecycleRepository | None,
    *,
    scope: TransactionScope,
    correlation_id: str | None = None,
    outcome_factory: OutcomeFactory,
) -> OutcomeReadModel | None:
    """Return a safe Outcome when the scoped lifecycle repository is unreadable.

    The scope-first list operation is intentionally used instead of a provider-
    specific ping so SQLite and SQLAlchemy providers share the same contract.
    """
    if transactions is None:
        return transaction_repository_unavailable_outcome(
            correlation_id=correlation_id,
            outcome_factory=outcome_factory,
            reason="transaction_repository_missing",
        )
    try:
        transactions.list_drafts_for_scope(scope=scope, states=None, limit=1)
    except Exception:
        return transaction_repository_unavailable_outcome(
            correlation_id=correlation_id,
            outcome_factory=outcome_factory,
            reason="transaction_repository_unavailable",
        )
    return None
