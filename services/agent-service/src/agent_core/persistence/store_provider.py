from __future__ import annotations

from functools import lru_cache
from dataclasses import dataclass

from agent_core.config import get_storage_paths
from agent_core.persistence.trace_store import TraceLogger
from agent_core.persistence.action_audit_store import ActionAuditStore
from agent_core.persistence.action_lifecycle_store import ActionLockStore, ActionRunStore, IdempotencyStore, OutboxStore, TransactionLifecycleStore
from agent_core.persistence.message_store import MessageStore
from agent_core.persistence.thread_store import ThreadStore
from agent_core.storage.repositories.base import StoreProvider
from agent_core.persistence.database_settings import (
    get_database_settings,
    DatabaseSettings,
    validate_database_settings,
)


@dataclass
class SqliteStoreProvider(StoreProvider):
    settings: DatabaseSettings
    threads: ThreadStore
    messages: MessageStore
    traces: TraceLogger
    action_audits: ActionAuditStore
    idempotency: IdempotencyStore
    locks: ActionLockStore
    outbox: OutboxStore
    action_runs: ActionRunStore
    transactions: TransactionLifecycleStore

    def close(self) -> None:
        seen: set[int] = set()
        for store in [self.threads, self.messages, self.traces, self.action_audits, self.idempotency, self.locks, self.outbox, self.action_runs, self.transactions]:
            if id(store) in seen:
                continue
            seen.add(id(store))
            close = getattr(store, "close", None)
            if callable(close):
                close()


def build_sqlite_store_provider(settings: DatabaseSettings | None = None) -> SqliteStoreProvider:
    settings = settings or get_database_settings()
    validate_database_settings(settings)
    if settings.normalized_backend not in {"sqlite", "sqlalchemy"}:
        raise ValueError("SQLite provider requires sqlite or explicit sqlalchemy backend")
    db_path = settings.sqlite_path or get_storage_paths()["sqlite_db"]
    return SqliteStoreProvider(
        settings=settings,
        threads=ThreadStore(db_path),
        messages=MessageStore(db_path),
        traces=TraceLogger(db_path),
        action_audits=ActionAuditStore(db_path),
        idempotency=IdempotencyStore(db_path),
        locks=ActionLockStore(db_path),
        outbox=OutboxStore(db_path),
        action_runs=ActionRunStore(db_path),
        transactions=TransactionLifecycleStore(db_path),
    )


def build_store_provider(settings: DatabaseSettings | None = None) -> StoreProvider:
    settings = settings or get_database_settings()
    validate_database_settings(settings)
    backend = settings.normalized_backend
    if backend == "sqlite":
        return build_sqlite_store_provider(settings)
    if backend in {"sqlalchemy", "postgres", "postgresql", "mysql"}:
        from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider
        return build_sqlalchemy_store_provider(settings)
    raise ValueError(f"Unsupported AGENT_DB_BACKEND={settings.backend!r}. Expected sqlite, sqlalchemy, postgres or mysql.")


@lru_cache(maxsize=1)
def get_store_provider() -> StoreProvider:
    return build_store_provider()


def reset_store_provider_cache() -> None:
    """Close the cached provider before removing the process-wide reference."""
    if get_store_provider.cache_info().currsize:
        provider = get_store_provider()
        close = getattr(provider, "close", None)
        try:
            if callable(close):
                close()
        finally:
            get_store_provider.cache_clear()
    else:
        get_store_provider.cache_clear()
