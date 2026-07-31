"""Concrete persistence implementations and StoreProvider composition.

The stable repository ports live in :mod:`agent_core.storage`.  This package is
its single concrete implementation owner and is intentionally consumed by the
application composition boundary, transaction coordinator, observability, and
migrations.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "build_store_provider",
    "build_sqlite_store_provider",
    "get_store_provider",
    "reset_store_provider_cache",
    "DatabaseSettings",
    "get_database_settings",
]


def __getattr__(name: str) -> Any:
    if name in {
        "build_store_provider",
        "build_sqlite_store_provider",
        "get_store_provider",
        "reset_store_provider_cache",
    }:
        from agent_core.persistence import store_provider

        return getattr(store_provider, name)
    if name in {"DatabaseSettings", "get_database_settings"}:
        from agent_core.persistence import database_settings

        return getattr(database_settings, name)
    raise AttributeError(name)
