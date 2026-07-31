from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from agent_core.rag.embedding_providers import build_embedding_provider
from agent_core.rag.loader import load_text_from_file
from agent_core.rag.splitter import split_text
from agent_core.rag.access import is_visible


class QdrantRagProvider:
    """Qdrant-backed RAG provider.

    Requires `qdrant-client`.  Each chunk is stored as one point with payload
    containing doc_id, title, content, source, tenant_id, status and metadata.
    """

    backend_name = "qdrant"

    def __init__(self, url: str | None = None, api_key: str | None = None, collection: str | None = None, embedding_provider: Any | None = None):
        try:
            from qdrant_client import QdrantClient  # type: ignore
            from qdrant_client.models import Distance, VectorParams, Filter, FieldCondition, MatchValue  # type: ignore
        except Exception as e:
            raise RuntimeError("RAG_BACKEND=qdrant requires qdrant-client.") from e
        self.QdrantClient = QdrantClient
        self.Distance = Distance
        self.VectorParams = VectorParams
        self.Filter = Filter
        self.FieldCondition = FieldCondition
        self.MatchValue = MatchValue
        self.url = url or os.getenv("QDRANT_URL") or "http://127.0.0.1:6333"
        self.api_key = api_key if api_key is not None else os.getenv("QDRANT_API_KEY")
        self.collection = collection or os.getenv("QDRANT_COLLECTION") or os.getenv("RAG_COLLECTION") or "agent_knowledge"
        self.embedding_provider = embedding_provider or build_embedding_provider()
        self.dimension = int(os.getenv("EMBEDDING_DIM", "1536"))
        self.client = QdrantClient(url=self.url, api_key=self.api_key or None)
        if os.getenv("RAG_CREATE_SCHEMA", "true").lower() in {"1", "true", "yes", "on"}:
            self.ensure_collection()

    def ensure_collection(self) -> None:
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection not in existing:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=self.VectorParams(size=self.dimension, distance=self.Distance.COSINE),
            )

    def upsert_document(self, doc_id: str, title: str, source: str, chunks: list[str], metadata: dict[str, Any] | None = None) -> int:
        metadata = metadata or {}
        tenant_id = metadata.get("tenant_id") or os.getenv("RAG_DEFAULT_TENANT") or "default"
        status = metadata.get("status") or "published"
        vectors = self.embedding_provider.embed_documents(chunks)
        points = []
        from qdrant_client.models import PointStruct  # type: ignore
        # delete old chunks for same doc first to keep doc replacement semantics
        self.client.delete(
            collection_name=self.collection,
            points_selector=self.Filter(must=[self.FieldCondition(key="doc_id", match=self.MatchValue(value=doc_id))]),
        )
        for idx, content in enumerate(chunks):
            chunk_id = f"{doc_id}::chunk_{idx:04d}"
            payload = {
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "title": title,
                "content": content,
                "source": source,
                "tenant_id": tenant_id,
                "status": status,
                "metadata": metadata,
            }
            points.append(PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id)), vector=vectors[idx], payload=payload))
        if points:
            self.client.upsert(collection_name=self.collection, points=points)
        return len(points)

    def ingest_file(self, path: str | Path, title: str | None = None, source: str | None = None, metadata: dict[str, Any] | None = None, *, doc_id: str | None = None) -> dict[str, Any]:
        p = Path(path)
        chunks = split_text(load_text_from_file(p))
        doc_id = doc_id or f"doc_{uuid.uuid4().hex[:12]}"
        meta = {"filename": p.name, **(metadata or {})}
        count = self.upsert_document(doc_id, title or p.name, source or str(p), chunks, meta)
        return {"doc_id": doc_id, "title": title or p.name, "chunks": count, "source": source or str(p), "backend": self.backend_name}

    def _filter(self, filters: dict[str, Any] | None = None):
        filters = filters or {}
        status = filters.get("status") or "published"
        scope = filters.get("__access_scope__") or {}
        tenant_id = str(scope.get("tenant_id") or filters.get("tenant_id") or os.getenv("RAG_DEFAULT_TENANT") or "")
        owner_id = str(scope.get("owner_id") or "")
        # Qdrant evaluates this filter before vector ranking/top-k.  Public
        # builtin/published content is globally readable; uploaded content is
        # tenant/private scoped via its metadata payload.
        must = [self.FieldCondition(key="status", match=self.MatchValue(value=status))]
        for key, expected in filters.items():
            if key in {"__access_scope__", "status", "tenant_id"} or expected is None:
                continue
            if isinstance(expected, (dict, list, tuple, set)):
                raise ValueError(f"unsupported qdrant metadata filter shape: {key}")
            must.append(
                self.FieldCondition(
                    key=f"metadata.{key}",
                    match=self.MatchValue(value=expected),
                )
            )
        if not scope:
            must.append(self.FieldCondition(key="tenant_id", match=self.MatchValue(value=tenant_id)))
            return self.Filter(must=must)
        should = [
            self.FieldCondition(key="metadata.visibility", match=self.MatchValue(value="public")),
            self.Filter(must=[
                self.FieldCondition(key="metadata.tenant_id", match=self.MatchValue(value=tenant_id)),
                self.FieldCondition(key="metadata.visibility", match=self.MatchValue(value="tenant")),
            ]),
            self.Filter(must=[
                self.FieldCondition(key="metadata.tenant_id", match=self.MatchValue(value=tenant_id)),
                self.FieldCondition(key="metadata.visibility", match=self.MatchValue(value="internal")),
            ]),
            self.Filter(must=[
                self.FieldCondition(key="metadata.tenant_id", match=self.MatchValue(value=tenant_id)),
                self.FieldCondition(key="metadata.owner_id", match=self.MatchValue(value=owner_id)),
                self.FieldCondition(key="metadata.visibility", match=self.MatchValue(value="private")),
            ]),
        ]
        return self.Filter(must=must, should=should)

    def search(self, query: str, top_k: int = 5, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        q = self.embedding_provider.embed_query(query)
        hits = self.client.search(collection_name=self.collection, query_vector=q, limit=top_k, query_filter=self._filter(filters))
        rows: list[dict[str, Any]] = []
        for hit in hits:
            payload = hit.payload or {}
            rows.append({
                "chunk_id": payload.get("chunk_id"),
                "doc_id": payload.get("doc_id"),
                "title": payload.get("title"),
                "content": payload.get("content"),
                "source": payload.get("source"),
                "metadata": payload.get("metadata") or {},
                "score": round(float(hit.score or 0), 4),
            })
        return rows

    def get_document(self, doc_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any] | None:
        points, _ = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=self.Filter(must=[self.FieldCondition(key="doc_id", match=self.MatchValue(value=doc_id))]),
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            return None
        payload = points[0].payload or {}
        row = {"doc_id": doc_id, "title": payload.get("title"), "source": payload.get("source"), "metadata": payload.get("metadata") or {}}
        if filters and "__access_scope__" in filters and not is_visible(row["metadata"], filters["__access_scope__"]):
            return None
        return row

    def list_documents(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        # Qdrant has no SQL-style DISTINCT; this scrolls a bounded window for admin UI.
        limit = int(os.getenv("QDRANT_LIST_DOCUMENT_LIMIT", "1000"))
        points, _ = self.client.scroll(collection_name=self.collection, limit=limit, with_payload=True, with_vectors=False)
        docs: dict[str, dict[str, Any]] = {}
        for p in points:
            payload = p.payload or {}
            doc_id = payload.get("doc_id")
            if doc_id and doc_id not in docs:
                docs[doc_id] = {"doc_id": doc_id, "title": payload.get("title"), "source": payload.get("source"), "metadata": payload.get("metadata") or {}}
        out = list(docs.values())
        if filters and "__access_scope__" in filters:
            out = [row for row in out if is_visible(row.get("metadata") or {}, filters["__access_scope__"])]
        return out

    def list_chunks(self, doc_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if self.get_document(doc_id, filters=filters) is None:
            return []
        points, _ = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=self.Filter(must=[self.FieldCondition(key="doc_id", match=self.MatchValue(value=doc_id))]),
            limit=1000,
            with_payload=True,
            with_vectors=False,
        )
        rows = []
        for p in points:
            payload = p.payload or {}
            rows.append({
                "chunk_id": payload.get("chunk_id"), "doc_id": payload.get("doc_id"), "title": payload.get("title"),
                "content": payload.get("content"), "source": payload.get("source"), "metadata": payload.get("metadata") or {},
            })
        return sorted(rows, key=lambda x: str(x.get("chunk_id") or ""))
