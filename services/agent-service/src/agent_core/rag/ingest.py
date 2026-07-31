from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_core.rag.seed_catalog import builtin_knowledge_documents
from agent_core.rag.factory import get_rag_provider
from agent_core.rag.splitter import split_text


def ingest_file(path: str | Path, title: str | None = None, source: str | None = None, metadata: dict[str, Any] | None = None, *, doc_id: str | None = None) -> dict:
    return get_rag_provider().ingest_file(
        path, title=title, source=source, metadata=metadata, doc_id=doc_id
    )


def seed_builtin_knowledge() -> dict:
    # Composition is explicit: ensure installed modules have contributed their
    # knowledge before a provider receives the generic documents.
    from agent_core.modules import current_module_registry

    current_module_registry()
    provider = get_rag_provider()
    if hasattr(provider, "seed_builtin_knowledge"):
        return provider.seed_builtin_knowledge()
    total = 0
    documents = builtin_knowledge_documents()
    for item in documents:
        chunks = split_text(item["content"])
        total += provider.upsert_document(
            doc_id=item["doc_id"],
            title=item["title"],
            source=item["source"],
            chunks=chunks,
            metadata={
                "builtin": True,
                "visibility": "public",
                "status": "published",
                **dict(item.get("metadata") or {}),
            },
        )
    return {"documents": len(documents), "chunks": total, "backend": getattr(provider, "backend_name", "unknown")}
