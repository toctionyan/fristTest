from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.persistence.database_settings import DatabaseSettings, get_database_settings


def test_preprod_postgres_backend_requires_explicit_postgres_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "preprod")
    monkeypatch.delenv("AGENT_DB_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_BACKEND", raising=False)
    monkeypatch.delenv("AGENT_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="PostgreSQL database URL"):
        get_database_settings()


def test_postgres_backend_rejects_sqlite_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PostgreSQL URL"):
        DatabaseSettings(
            backend="postgres",
            database_url=f"sqlite:///{tmp_path / 'agent.db'}",
            sqlite_path=tmp_path / "agent.db",
            create_schema=False,
        )


def test_sqlite_backend_rejects_postgres_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="SQLite URL"):
        DatabaseSettings(
            backend="sqlite",
            database_url="postgresql+psycopg://agent:secret@db.example/agent",
            sqlite_path=tmp_path / "agent.db",
            create_schema=True,
        )


def test_mysql_backend_rejects_sqlite_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="MySQL URL"):
        DatabaseSettings(
            backend="mysql",
            database_url=f"sqlite:///{tmp_path / 'agent.db'}",
            sqlite_path=tmp_path / "agent.db",
            create_schema=False,
        )


def test_generic_sqlalchemy_backend_may_use_explicit_sqlite_url(tmp_path: Path) -> None:
    settings = DatabaseSettings(
        backend="sqlalchemy",
        database_url=f"sqlite:///{tmp_path / 'agent.db'}",
        sqlite_path=tmp_path / "agent.db",
        create_schema=True,
    )
    assert settings.normalized_backend == "sqlalchemy"
