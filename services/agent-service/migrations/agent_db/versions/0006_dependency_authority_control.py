"""add append-only dependency-authority control and rollback stores.

Revision ID: 0006_dependency_authority_control
Revises: 0005_production_integrity

The customer-serving Agent has no write API for these tables. Production
governance/operator tooling should append immutable rows using separately
managed database privileges; runtime workers only need SELECT.
"""
from __future__ import annotations

from alembic import op

revision = "0006_dependency_authority_control"
down_revision = "0005_production_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_dependency_authority_control_records (
            control_epoch BIGINT PRIMARY KEY,
            revision VARCHAR(500) NOT NULL UNIQUE,
            snapshot_digest VARCHAR(128) NOT NULL UNIQUE,
            record_json TEXT NOT NULL,
            stored_at VARCHAR(64) NOT NULL,
            CHECK (control_epoch > 0)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_dependency_control_epoch "
        "ON agent_dependency_authority_control_records(control_epoch)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_dependency_authority_rollback_directives (
            rollback_epoch BIGINT PRIMARY KEY,
            revision VARCHAR(500) NOT NULL UNIQUE,
            snapshot_digest VARCHAR(128) NOT NULL UNIQUE,
            directive_json TEXT NOT NULL,
            stored_at VARCHAR(64) NOT NULL,
            CHECK (rollback_epoch > 0)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_dependency_rollback_epoch "
        "ON agent_dependency_authority_rollback_directives(rollback_epoch)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_dependency_authority_rollback_directives")
    op.execute("DROP TABLE IF EXISTS agent_dependency_authority_control_records")
