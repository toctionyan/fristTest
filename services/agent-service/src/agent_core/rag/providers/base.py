from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class RagDocument:
    doc_id: str
    title: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RagChunk:
    chunk_id: str
    doc_id: str
    title: str
    content: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | dict[str, float] | None = None


@dataclass
class RagSearchResult:
    chunk_id: str
    doc_id: str
    title: str
    content: str
    source: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "score": self.score,
            "metadata": self.metadata,
        }


@runtime_checkable
class RagProvider(Protocol):
    backend_name: str

    def ingest_file(self, path: str | Path, title: str | None = None, source: str | None = None, metadata: dict[str, Any] | None = None, *, doc_id: str | None = None) -> dict[str, Any]: ...
    def upsert_document(self, doc_id: str, title: str, source: str, chunks: list[str], metadata: dict[str, Any] | None = None) -> int: ...
    def search(self, query: str, top_k: int = 5, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...
    def get_document(self, doc_id: str) -> dict[str, Any] | None: ...
    def list_documents(self) -> list[dict[str, Any]]: ...
    def list_chunks(self, doc_id: str) -> list[dict[str, Any]]: ...
