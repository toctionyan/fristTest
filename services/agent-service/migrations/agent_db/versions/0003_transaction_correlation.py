"""add correlation id to Agent transaction lifecycle tables.

Revision ID: 0003_transaction_correlation
Revises: 0002_trace_correlation_id
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_transaction_correlation"
down_revision = "0002_trace_correlation_id"
branch_labels = None
depends_on = None

_TABLES = (
    "agent_transaction_grants",
    "agent_transaction_attempts",
    "agent_transaction_drafts",
    "agent_transaction_receipts",
)

def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in _TABLES:
        columns = {str(row["name"]) for row in inspector.get_columns(table)}
        if "correlation_id" not in columns:
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column("correlation_id", sa.String(length=64), nullable=True))
        index_name = f"idx_{table}_correlation_id"
        indexes = {str(row["name"]) for row in inspector.get_indexes(table)}
        if index_name not in indexes:
            op.create_index(index_name, table, ["correlation_id"])

def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_index(f"idx_{table}_correlation_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column("correlation_id")
