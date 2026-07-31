"""Stable persistence ports and value contracts.

Concrete SQLite/SQLAlchemy implementations and provider construction belong to
:mod:`agent_core.persistence`.  Keeping this package implementation-neutral
allows lifecycle, runtime and transaction code to depend on storage contracts
without creating an abstraction-to-implementation cycle.
"""
from agent_core.storage.repositories.base import (
    ActiveDraftValidationCode,
    ActiveDraftValidationResult,
    StoreProvider,
    TransactionScope,
)

__all__ = [
    "ActiveDraftValidationCode",
    "ActiveDraftValidationResult",
    "StoreProvider",
    "TransactionScope",
]
