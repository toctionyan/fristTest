from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    dimension: int | None

    def embed_query(self, text: str): ...
    def embed_documents(self, texts: list[str]): ...
