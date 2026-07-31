from __future__ import annotations

from agent_core.config import retrieval_top_k
from agent_core.rag.factory import get_rag_provider


def retrieve(query: str, top_k: int | None = None, filters: dict | None = None) -> list[dict]:
    """Search through the configured RAG provider boundary."""
    return get_rag_provider().search(query, top_k or retrieval_top_k(), filters=filters)
