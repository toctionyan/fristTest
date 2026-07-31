"""initial agent schema

Revision ID: 0001_initial_agent_schema
Revises:
Create Date: 2026-07-01 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from agent_core.persistence.sqlalchemy_provider import _define_tables


revision = "0001_initial_agent_schema"
down_revision = None
branch_labels = None
depends_on = None


def _metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    _define_tables(sa, metadata)
    return metadata


def upgrade() -> None:
    metadata = _metadata()
    metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    metadata = _metadata()
    for table in reversed(metadata.sorted_tables):
        op.drop_table(table.name)
