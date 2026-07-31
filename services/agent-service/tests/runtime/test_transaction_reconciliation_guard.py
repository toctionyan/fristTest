from __future__ import annotations

from app.services.agent_service import AgentService


class _TransactionsWithoutAttempts:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def list_reconcilable_attempts(self, **kwargs):
        self.calls.append(kwargs)
        return []


class _Graph:
    pass


def test_empty_reconciliation_does_not_replace_a_live_authority_interrupt():
    """A form/authority interrupt is not an excuse to run an empty final branch."""
    service = object.__new__(AgentService)
    transactions = _TransactionsWithoutAttempts()
    service.transactions = transactions
    service._config_for_request = lambda *_args: {"configurable": {"thread_id": "safe-thread"}}
    service._checkpoint_values = lambda *_args, **_kwargs: {
        "phase": "offer_confirmation",
        "status": "TransactionInteractionRequired",
        "active_draft_id": "h_offer:pending",
    }
    service._lifecycle_command_runner = lambda: (_ for _ in ()).throw(AssertionError("empty reconciliation must not advance graph state"))

    result = AgentService._reconcile_pending_transaction_attempts(
        service,
        _Graph(),
        thread_id="safe-thread",
        user_id="u001",
        tenant_id="tenant-a",
    )

    assert result is None
    assert transactions.calls[0]["scope"].thread_id == "safe-thread"
