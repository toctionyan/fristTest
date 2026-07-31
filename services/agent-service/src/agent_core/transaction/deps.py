from __future__ import annotations

"""Explicit execution dependencies consumed by transaction state machines."""

from dataclasses import dataclass

from agent_core.business import BusinessPort
from agent_core.kernel.outcome_contract import OutcomeFactory


@dataclass(frozen=True)
class TransactionExecutionDeps:
    """Dependencies selected by application/runtime composition, never discovered here."""

    business_port: BusinessPort
    outcome_factory: OutcomeFactory


__all__ = ["TransactionExecutionDeps"]
