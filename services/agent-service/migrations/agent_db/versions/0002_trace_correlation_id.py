"""add correlation id to agent traces

Revision ID: 0002_trace_correlation_id
Revises: 0001_initial_agent_schema
Create Date: 2026-07-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_trace_correlation_id"
down_revision = "0001_initial_agent_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {str(row["name"]) for row in inspector.get_columns("agent_trace_logs")}
    if "correlation_id" not in columns:
        with op.batch_alter_table("agent_trace_logs") as batch:
            batch.add_column(sa.Column("correlation_id", sa.String(length=64), nullable=True))
    indexes = {str(row["name"]) for row in inspector.get_indexes("agent_trace_logs")}
    if "idx_agent_trace_logs_correlation_id" not in indexes:
        op.create_index("idx_agent_trace_logs_correlation_id", "agent_trace_logs", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("idx_agent_trace_logs_correlation_id", table_name="agent_trace_logs")
    with op.batch_alter_table("agent_trace_logs") as batch:
        batch.drop_column("correlation_id")
