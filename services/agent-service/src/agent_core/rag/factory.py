from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def get_rag_provider():
    backend = (os.getenv("RAG_BACKEND") or "local_sparse").strip().lower()
    if backend in {"local", "local_sparse", "sqlite", "sparse"}:
        from agent_core.rag.providers.local_sparse_provider import LocalSparseRagProvider
        return LocalSparseRagProvider()
    if backend in {"pgvector", "postgres_vector", "postgres"}:
        from agent_core.rag.providers.pgvector_provider import PgVectorRagProvider
        return PgVectorRagProvider()
    if backend in {"qdrant"}:
        from agent_core.rag.providers.qdrant_provider import QdrantRagProvider
        return QdrantRagProvider()
    raise ValueError(f"Unsupported RAG_BACKEND={backend!r}. Expected local_sparse, pgvector or qdrant.")


def reset_rag_provider_cache() -> None:
    get_rag_provider.cache_clear()
