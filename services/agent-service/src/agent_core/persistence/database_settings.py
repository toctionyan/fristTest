from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit

from agent_core.config import project_path
from agent_core.kernel.profile import RuntimeProfile, require_runtime_profile


_POSTGRES_SCHEMES = {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}
_MYSQL_SCHEMES = {"mysql", "mysql+pymysql", "mysql+mysqldb"}
_SQLITE_SCHEMES = {"sqlite", "sqlite+pysqlite"}
_SUPPORTED_BACKENDS = {"sqlite", "sqlalchemy", "postgres", "postgresql", "mysql"}


def _url_scheme(database_url: str) -> str:
    return urlsplit((database_url or "").strip()).scheme.lower()


def validate_database_settings(settings: "DatabaseSettings") -> None:
    """Bind the declared backend and effective URL to one database authority.

    This validation is intentionally callable both from ``DatabaseSettings``
    construction and from provider factories.  Provider-side revalidation
    protects against manually supplied or deserialized settings that bypassed
    normal environment resolution.
    """

    backend = settings.normalized_backend
    if backend not in _SUPPORTED_BACKENDS:
        raise ValueError(
            f"Unsupported AGENT_DB_BACKEND={settings.backend!r}. "
            "Expected sqlite, sqlalchemy, postgres, postgresql or mysql."
        )

    database_url = (settings.database_url or "").strip()
    if not database_url:
        raise ValueError("Agent database URL must not be empty")
    scheme = _url_scheme(database_url)

    if backend == "sqlite" and scheme not in _SQLITE_SCHEMES:
        raise ValueError("AGENT_DB_BACKEND=sqlite requires a SQLite URL")
    if backend in {"postgres", "postgresql"} and scheme not in _POSTGRES_SCHEMES:
        raise ValueError("AGENT_DB_BACKEND=postgres requires a PostgreSQL URL")
    if backend == "mysql" and scheme not in _MYSQL_SCHEMES:
        raise ValueError("AGENT_DB_BACKEND=mysql requires a MySQL URL")
    if backend == "sqlalchemy" and scheme not in (_SQLITE_SCHEMES | _POSTGRES_SCHEMES | _MYSQL_SCHEMES):
        raise ValueError(
            "AGENT_DB_BACKEND=sqlalchemy requires a supported SQLite, PostgreSQL or MySQL URL"
        )


@dataclass(frozen=True)
class DatabaseSettings:
    backend: str
    database_url: str
    sqlite_path: Path
    create_schema: bool = True

    def __post_init__(self) -> None:
        validate_database_settings(self)

    @property
    def normalized_backend(self) -> str:
        return (self.backend or "sqlite").strip().lower()


def get_database_settings() -> DatabaseSettings:
    profile = require_runtime_profile()
    strict = profile in {RuntimeProfile.PREPROD, RuntimeProfile.PRODUCTION}
    default_backend = "postgres" if strict else "sqlite"
    default_create_schema = "false" if strict else "true"
    backend = (os.getenv("AGENT_DB_BACKEND") or os.getenv("DATABASE_BACKEND") or default_backend).strip()
    normalized_backend = backend.lower()
    sqlite_db_path = project_path(os.getenv("SQLITE_DB_PATH"), "runtime/sqlite/app.db")
    configured_url = (os.getenv("AGENT_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()

    if configured_url:
        database_url = configured_url
    elif normalized_backend == "sqlite":
        database_url = f"sqlite:///{sqlite_db_path}"
    elif normalized_backend in {"postgres", "postgresql"}:
        raise RuntimeError(
            "AGENT_DB_BACKEND=postgres requires an explicit PostgreSQL database URL "
            "in AGENT_DATABASE_URL or DATABASE_URL"
        )
    elif normalized_backend == "mysql":
        raise RuntimeError(
            "AGENT_DB_BACKEND=mysql requires an explicit MySQL database URL "
            "in AGENT_DATABASE_URL or DATABASE_URL"
        )
    else:
        raise RuntimeError(
            "AGENT_DB_BACKEND=sqlalchemy requires an explicit database URL "
            "in AGENT_DATABASE_URL or DATABASE_URL"
        )

    return DatabaseSettings(
        backend=backend,
        database_url=database_url,
        sqlite_path=sqlite_db_path,
        create_schema=(
            os.getenv("AGENT_DB_CREATE_SCHEMA", default_create_schema).lower()
            in {"1", "true", "yes", "on"}
        ),
    )
