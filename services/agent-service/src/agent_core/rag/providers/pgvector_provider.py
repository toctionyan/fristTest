from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from agent_core.rag.embedding_providers import build_embedding_provider
from agent_core.rag.loader import load_text_from_file
from agent_core.rag.splitter import split_text
from agent_core.rag.access import is_visible
from agent_core.runtime.profile import is_local_profile


class PgVectorRagProvider:
    """PostgreSQL + pgvector RAG provider.

    Requires `psycopg[binary]` and PostgreSQL with `CREATE EXTENSION vector`.
    It uses plain SQL so the Agent can switch RAG_BACKEND=pgvector without
    coupling RAG to the general StoreProvider ORM choice.
    """

    backend_name = "pgvector"

    def __init__(self, database_url: str | None = None, collection: str | None = None, embedding_provider: Any | None = None):
        self.database_url = database_url or os.getenv("RAG_DATABASE_URL") or os.getenv("AGENT_DATABASE_URL") or os.getenv("DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("RAG_DATABASE_URL or AGENT_DATABASE_URL is required for RAG_BACKEND=pgvector")
        if self.database_url.startswith("postgresql+psycopg://"):
            self.database_url = "postgresql://" + self.database_url.removeprefix("postgresql+psycopg://")
        self.collection = collection or os.getenv("RAG_COLLECTION", "agent_knowledge")
        self.embedding_provider = embedding_provider or build_embedding_provider()
        self.dimension = int(os.getenv("EMBEDDING_DIM", "1536"))
        try:
            import psycopg  # type: ignore
        except Exception as e:
            raise RuntimeError("RAG_BACKEND=pgvector requires psycopg[binary].") from e
        self.psycopg = psycopg
        # Schema DDL is local-only by default.  Preprod/production must apply
        # Alembic migrations before readiness succeeds; serving processes only
        # verify/read the schema and never mutate it at startup.
        raw_create = os.getenv("RAG_CREATE_SCHEMA")
        create_schema = is_local_profile() if raw_create is None else raw_create.lower() in {"1", "true", "yes", "on"}
        if create_schema:
            self.init_schema()

    def _connect(self):
        return self.psycopg.connect(self.database_url)

    def init_schema(self) -> None:
        """Local-only bootstrap schema.

        Preprod/production use the Alembic RAG migration.  The same explicit
        scope columns are kept here so an isolated local pgvector developer
        database behaves like the deployed schema.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rag_documents (
                        doc_id TEXT PRIMARY KEY,
                        tenant_id TEXT,
                        owner_id TEXT,
                        visibility TEXT NOT NULL DEFAULT 'tenant',
                        collection TEXT NOT NULL,
                        title TEXT,
                        source TEXT,
                        metadata_json JSONB,
                        status TEXT NOT NULL DEFAULT 'published',
                        created_at TIMESTAMPTZ DEFAULT now(),
                        updated_at TIMESTAMPTZ DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS rag_chunks (
                        chunk_id TEXT PRIMARY KEY,
                        doc_id TEXT REFERENCES rag_documents(doc_id) ON DELETE CASCADE,
                        tenant_id TEXT,
                        owner_id TEXT,
                        visibility TEXT NOT NULL DEFAULT 'tenant',
                        collection TEXT NOT NULL,
                        title TEXT,
                        content TEXT,
                        source TEXT,
                        embedding vector({self.dimension}),
                        metadata_json JSONB,
                        status TEXT NOT NULL DEFAULT 'published',
                        created_at TIMESTAMPTZ DEFAULT now()
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_rag_documents_scope ON rag_documents(collection,status,visibility,tenant_id,owner_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_scope ON rag_chunks(collection,status,visibility,tenant_id,owner_id)")
            conn.commit()

    def upsert_document(self, doc_id: str, title: str, source: str, chunks: list[str], metadata: dict[str, Any] | None = None) -> int:
        metadata = metadata or {}
        tenant_id = str(metadata.get("tenant_id") or os.getenv("RAG_DEFAULT_TENANT") or "default")
        owner_id = str(metadata.get("owner_id") or "")
        visibility = str(metadata.get("visibility") or "tenant").lower()
        status = str(metadata.get("status") or "published")
        embeddings = self.embedding_provider.embed_documents(chunks)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rag_documents(doc_id, tenant_id, owner_id, visibility, collection, title, source, metadata_json, status, updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,now())
                    ON CONFLICT(doc_id) DO UPDATE SET tenant_id=EXCLUDED.tenant_id, owner_id=EXCLUDED.owner_id,
                        visibility=EXCLUDED.visibility, title=EXCLUDED.title, source=EXCLUDED.source,
                        metadata_json=EXCLUDED.metadata_json, status=EXCLUDED.status, updated_at=now()
                    """,
                    (doc_id, tenant_id, owner_id, visibility, self.collection, title, source, json.dumps(metadata, ensure_ascii=False), status),
                )
                cur.execute("DELETE FROM rag_chunks WHERE doc_id=%s", (doc_id,))
                for idx, content in enumerate(chunks):
                    chunk_id = f"{doc_id}::chunk_{idx:04d}"
                    cur.execute(
                        """
                        INSERT INTO rag_chunks(chunk_id, doc_id, tenant_id, owner_id, visibility, collection, title, content, source, embedding, metadata_json, status)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                        """,
                        (chunk_id, doc_id, tenant_id, owner_id, visibility, self.collection, title, content, source, embeddings[idx], json.dumps(metadata, ensure_ascii=False), status),
                    )
            conn.commit()
        return len(chunks)

    def ingest_file(self, path: str | Path, title: str | None = None, source: str | None = None, metadata: dict[str, Any] | None = None, *, doc_id: str | None = None) -> dict[str, Any]:
        p = Path(path)
        chunks = split_text(load_text_from_file(p))
        doc_id = doc_id or f"doc_{uuid.uuid4().hex[:12]}"
        meta = {"filename": p.name, **(metadata or {})}
        count = self.upsert_document(doc_id, title or p.name, source or str(p), chunks, meta)
        return {"doc_id": doc_id, "title": title or p.name, "chunks": count, "source": source or str(p), "backend": self.backend_name}

    @staticmethod
    def _scope_sql(scope: dict[str, Any]) -> tuple[str, tuple[str, str, str]]:
        tenant_id = str(scope.get("tenant_id") or "")
        owner_id = str(scope.get("owner_id") or "")
        clause = "(visibility = 'public' OR (tenant_id = %s AND visibility IN ('tenant','internal')) OR (tenant_id = %s AND owner_id = %s AND visibility IN ('private','user')))"
        return clause, (tenant_id, tenant_id, owner_id)

    def search(self, query: str, top_k: int = 5, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        q = self.embedding_provider.embed_query(query)
        filters = filters or {}
        status = str(filters.get("status") or "published")
        scope = dict(filters.get("__access_scope__") or {})
        clause, scope_args = self._scope_sql(scope)
        metadata_filters = {
            str(key): value
            for key, value in filters.items()
            if key not in {"__access_scope__", "status", "tenant_id"} and value is not None
        }
        metadata_clause = " AND metadata_json @> %s::jsonb" if metadata_filters else ""
        metadata_args = (json.dumps(metadata_filters, ensure_ascii=False),) if metadata_filters else ()
        sql = f"""
            SELECT chunk_id, doc_id, title, content, source, metadata_json,
                   1 - (embedding <=> %s::vector) AS score
            FROM rag_chunks
            WHERE collection=%s AND status=%s AND {clause}{metadata_clause}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        q,
                        self.collection,
                        status,
                        *scope_args,
                        *metadata_args,
                        q,
                        max(1, min(int(top_k), 100)),
                    ),
                )
                rows = cur.fetchall()
        return [{"chunk_id": r[0], "doc_id": r[1], "title": r[2], "content": r[3], "source": r[4], "metadata": r[5] or {}, "score": round(float(r[6] or 0), 4)} for r in rows]

    def get_document(self, doc_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any] | None:
        filters = filters or {}
        clause, scope_args = self._scope_sql(dict(filters.get("__access_scope__") or {}))
        sql = f"SELECT doc_id,title,source,metadata_json,status,created_at,updated_at FROM rag_documents WHERE doc_id=%s AND collection=%s AND {clause}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (doc_id, self.collection, *scope_args))
                row = cur.fetchone()
        if not row:
            return None
        return {"doc_id": row[0], "title": row[1], "source": row[2], "metadata": row[3] or {}, "status": row[4], "created_at": str(row[5]), "updated_at": str(row[6])}

    def list_documents(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        clause, scope_args = self._scope_sql(dict(filters.get("__access_scope__") or {}))
        sql = f"SELECT doc_id,title,source,metadata_json,status,created_at FROM rag_documents WHERE collection=%s AND {clause} ORDER BY created_at DESC"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (self.collection, *scope_args))
                rows = cur.fetchall()
        return [{"doc_id": r[0], "title": r[1], "source": r[2], "metadata": r[3] or {}, "status": r[4], "created_at": str(r[5])} for r in rows]

    def list_chunks(self, doc_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if self.get_document(doc_id, filters=filters) is None:
            return []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT chunk_id,doc_id,title,content,source,metadata_json,created_at FROM rag_chunks WHERE doc_id=%s ORDER BY chunk_id", (doc_id,))
                rows = cur.fetchall()
        return [{"chunk_id": r[0], "doc_id": r[1], "title": r[2], "content": r[3], "source": r[4], "metadata": r[5] or {}, "created_at": str(r[6])} for r in rows]
