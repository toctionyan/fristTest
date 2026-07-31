from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_core.persistence.database_settings import get_database_settings  # noqa: E402
from agent_core.persistence.sqlalchemy_provider import _define_tables  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

metadata = sa.MetaData()
_define_tables(sa, metadata)
target_metadata = metadata


def _database_url() -> str:
    return os.getenv("AGENT_DATABASE_URL") or os.getenv("DATABASE_URL") or get_database_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = sa.create_engine(_database_url(), pool_pre_ping=True, future=True)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
