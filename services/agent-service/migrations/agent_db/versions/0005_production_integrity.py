"""add fenced conversation leases and shared document indexing queue.

Revision ID: 0005_production_integrity
Revises: 0004_pgvector_rag_schema
"""
from __future__ import annotations

from alembic import op

revision = "0005_production_integrity"
down_revision = "0004_pgvector_rag_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_action_lock_tokens (
            token BIGSERIAL PRIMARY KEY,
            lock_key VARCHAR(255) NOT NULL,
            issued_at VARCHAR(64) NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_action_lock_tokens_lock_key ON agent_action_lock_tokens(lock_key)")
    op.execute("ALTER TABLE agent_action_locks ADD COLUMN IF NOT EXISTS fencing_token BIGINT")
    op.execute("ALTER TABLE agent_action_locks ADD COLUMN IF NOT EXISTS renewed_at VARCHAR(64)")
    op.execute("UPDATE agent_action_locks SET fencing_token=0 WHERE fencing_token IS NULL")
    op.execute("UPDATE agent_action_locks SET renewed_at=COALESCE(renewed_at, created_at) WHERE renewed_at IS NULL")
    op.execute("ALTER TABLE agent_action_locks ALTER COLUMN fencing_token SET NOT NULL")
    op.execute("ALTER TABLE agent_action_locks ALTER COLUMN renewed_at SET NOT NULL")

    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_document_index_jobs (
            job_id VARCHAR(80) PRIMARY KEY,
            tenant_id VARCHAR(255) NOT NULL,
            user_id VARCHAR(255) NOT NULL,
            visibility VARCHAR(32) NOT NULL,
            object_uri TEXT NOT NULL,
            title VARCHAR(512) NOT NULL,
            source TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            state VARCHAR(32) NOT NULL,
            doc_id VARCHAR(255),
            chunks INTEGER,
            error TEXT,
            worker_id VARCHAR(255),
            claim_token VARCHAR(64),
            lease_until VARCHAR(64),
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            next_attempt_at VARCHAR(64) NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_document_jobs_scope ON agent_document_index_jobs(tenant_id,user_id,state,created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_document_jobs_claim ON agent_document_index_jobs(state,next_attempt_at,lease_until,created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_document_jobs_worker ON agent_document_index_jobs(worker_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_document_index_jobs")
    op.execute("ALTER TABLE agent_action_locks DROP COLUMN IF EXISTS renewed_at")
    op.execute("ALTER TABLE agent_action_locks DROP COLUMN IF EXISTS fencing_token")
    op.execute("DROP TABLE IF EXISTS agent_action_lock_tokens")
