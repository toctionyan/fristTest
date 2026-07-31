from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from agent_core.config import get_storage_paths
from agent_core.rag.seed_catalog import builtin_knowledge_documents
from agent_core.rag.loader import load_text_from_file
from agent_core.rag.splitter import split_text
from agent_core.rag.vector_store import LocalVectorStore


class LocalSparseRagProvider:
    backend_name = "local_sparse"

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or get_storage_paths()["vector_db"]

    def _store(self) -> LocalVectorStore:
        return LocalVectorStore(self.db_path)

    def upsert_document(self, doc_id: str, title: str, source: str, chunks: list[str], metadata: dict[str, Any] | None = None) -> int:
        with self._store() as store:
            return store.add_document(doc_id=doc_id, title=title, source=source, chunks=chunks, metadata=metadata or {})

    def ingest_file(self, path: str | Path, title: str | None = None, source: str | None = None, metadata: dict[str, Any] | None = None, *, doc_id: str | None = None) -> dict[str, Any]:
        p = Path(path)
        text = load_text_from_file(p)
        chunks = split_text(text)
        doc_id = doc_id or f"doc_{uuid.uuid4().hex[:12]}"
        meta = {"filename": p.name, **(metadata or {})}
        count = self.upsert_document(doc_id=doc_id, title=title or p.name, source=source or str(p), chunks=chunks, metadata=meta)
        return {"doc_id": doc_id, "title": title or p.name, "chunks": count, "source": source or str(p), "backend": self.backend_name}

    def seed_builtin_knowledge(self) -> dict[str, Any]:
        documents = builtin_knowledge_documents()
        total = 0
        for item in documents:
            chunks = split_text(item["content"])
            total += self.upsert_document(
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
        return {"documents": len(documents), "chunks": total, "backend": self.backend_name}

    def search(self, query: str, top_k: int = 5, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        # LocalVectorStore applies access/metadata filters *before* ranking and
        # top-k truncation.  Do not post-filter a global top-k result.
        with self._store() as store:
            return store.search(query, top_k=top_k, filters=filters)

    def get_document(self, doc_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._store() as store:
            return store.get_document(doc_id, filters=filters)

    def list_documents(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._store() as store:
            return store.list_documents(filters=filters)

    def list_chunks(self, doc_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._store() as store:
            return store.list_chunks(doc_id, filters=filters)


def _apply_filters(rows: list[dict[str, Any]], filters: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not filters:
        return rows
    out = []
    for row in rows:
        meta = row.get("metadata") or {}
        ok = True
        for key, expected in filters.items():
            if expected is None:
                continue
            value = meta.get(key) or row.get(key)
            if isinstance(expected, (list, tuple, set)):
                if value not in expected and not (isinstance(value, list) and any(v in expected for v in value)):
                    ok = False
                    break
            elif value is not None and value != expected:
                ok = False
                break
        if ok:
            out.append(row)
    return out
