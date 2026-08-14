from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest


@pytest.mark.integration
def test_fresh_postgres_schema_upgrades_to_head_without_duplicate_columns() -> None:
    base_url = os.getenv("AGENT_TEST_POSTGRES_URL")
    if not base_url:
        pytest.fail("AGENT_TEST_POSTGRES_URL is required")
    psycopg_url = base_url.replace("postgresql+psycopg://", "postgresql://", 1)
    import psycopg

    schema = f"migration_{uuid4().hex[:16]}"
    admin = psycopg.connect(psycopg_url, autocommit=True)
    # Reproduce a shared production database where pgvector is already
    # installed outside the application's isolated migration schema.
    admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    admin.execute(f'CREATE SCHEMA "{schema}"')
    separator = "&" if "?" in base_url else "?"
    schema_url = f"{base_url}{separator}options={quote(f'-csearch_path={schema}') }"
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(
        {
            "APP_PROFILE": "preprod",
            "AGENT_DB_BACKEND": "postgres",
            "AGENT_DATABASE_URL": schema_url,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "-c", "migrations/alembic.ini", "upgrade", "head"],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        row = admin.execute(
            f'SELECT version_num FROM "{schema}".alembic_version'
        ).fetchone()
        assert row and row[0] == "0006_dependency_auth_control"
        vector_column = admin.execute(
            "SELECT udt_schema, udt_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'rag_chunks' "
            "AND column_name = 'embedding'",
            (schema,),
        ).fetchone()
        assert vector_column == ("public", "vector")
    finally:
        admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.close()
