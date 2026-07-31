from __future__ import annotations

import os


def build_embedding_provider():
    provider = (os.getenv("EMBEDDING_PROVIDER") or "local_sparse").strip().lower()
    if provider in {"local", "local_sparse", "sparse"}:
        from agent_core.rag.embedding_providers.local_sparse import LocalSparseEmbeddingProvider
        return LocalSparseEmbeddingProvider()
    if provider in {"http", "local_http"}:
        from agent_core.rag.embedding_providers.http_provider import HttpEmbeddingProvider
        return HttpEmbeddingProvider()
    if provider in {"openai", "openai_compatible"}:
        from agent_core.rag.embedding_providers.openai_provider import OpenAIEmbeddingProvider
        return OpenAIEmbeddingProvider()
    raise ValueError(f"Unsupported EMBEDDING_PROVIDER={provider!r}")
