from __future__ import annotations

"""Read-only migration verification for readiness checks.

Application processes never run schema/data migrations.  Preprod/production
readiness verifies the revision that deployment automation already installed.
"""

import os
from typing import Any


def required_agent_revision() -> str:
    return os.getenv("AGENT_REQUIRED_ALEMBIC_REVISION", "0006_dependency_auth_control").strip()


def verify_agent_migration() -> dict[str, Any]:
    url = os.getenv("AGENT_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return {"ready": False, "error": "AGENT_DATABASE_URL/DATABASE_URL is not configured"}
    try:
        import sqlalchemy as sa
    except Exception as exc:  # pragma: no cover
        return {"ready": False, "error": f"SQLAlchemy unavailable: {exc}"}
    expected = required_agent_revision()
    try:
        engine = sa.create_engine(url, pool_pre_ping=True, future=True)
        try:
            with engine.connect() as conn:
                revision = conn.execute(sa.text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
        finally:
            engine.dispose()
    except Exception as exc:
        return {"ready": False, "expected": expected, "error": f"migration check failed: {exc}"}
    if str(revision or "") != expected:
        return {"ready": False, "expected": expected, "installed": revision, "error": "migration revision mismatch"}
    return {"ready": True, "expected": expected, "installed": revision}
