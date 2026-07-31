"""create pgvector RAG schema with scope columns.

Revision ID: 0004_pgvector_rag_schema
Revises: 0003_transaction_correlation
"""
from __future__ import annotations
import os
from alembic import op
import sqlalchemy as sa

revision = "0004_pgvector_rag_schema"
down_revision = "0003_transaction_correlation"
branch_labels = None
depends_on = None

def upgrade() -> None:
    dimension = int(os.getenv("EMBEDDING_DIM", "1536"))
    if dimension < 1 or dimension > 32768:
        raise RuntimeError("invalid EMBEDDING_DIM")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    bind = op.get_bind()
    vector_schema = bind.execute(
        sa.text(
            "SELECT namespace.nspname "
            "FROM pg_extension extension "
            "JOIN pg_namespace namespace ON namespace.oid = extension.extnamespace "
            "WHERE extension.extname = 'vector'"
        )
    ).scalar_one()
    qualified_vector = (
        f"{bind.dialect.identifier_preparer.quote_schema(vector_schema)}.vector"
    )
    op.execute("""CREATE TABLE IF NOT EXISTS rag_documents (
        doc_id TEXT PRIMARY KEY, tenant_id TEXT, owner_id TEXT, visibility TEXT NOT NULL DEFAULT 'tenant',
        collection TEXT NOT NULL, title TEXT, source TEXT, metadata_json JSONB, status TEXT NOT NULL DEFAULT 'published',
        created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now()
    )""")
    op.execute(f"""CREATE TABLE IF NOT EXISTS rag_chunks (
        chunk_id TEXT PRIMARY KEY, doc_id TEXT REFERENCES rag_documents(doc_id) ON DELETE CASCADE,
        tenant_id TEXT, owner_id TEXT, visibility TEXT NOT NULL DEFAULT 'tenant', collection TEXT NOT NULL,
        title TEXT, content TEXT, source TEXT, embedding {qualified_vector}({dimension}), metadata_json JSONB,
        status TEXT NOT NULL DEFAULT 'published', created_at TIMESTAMPTZ DEFAULT now()
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS idx_rag_documents_scope ON rag_documents(collection,status,visibility,tenant_id,owner_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_scope ON rag_chunks(collection,status,visibility,tenant_id,owner_id)")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_chunks")
    op.execute("DROP TABLE IF EXISTS rag_documents")
