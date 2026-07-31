from __future__ import annotations

from agent_core.rag.embeddings import sparse_vector


class LocalSparseEmbeddingProvider:
    name = "local_sparse"
    dimension = None

    def embed_query(self, text: str) -> dict[str, float]:
        return sparse_vector(text)

    def embed_documents(self, texts: list[str]) -> list[dict[str, float]]:
        return [sparse_vector(text) for text in texts]
