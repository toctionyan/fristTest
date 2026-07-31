from __future__ import annotations

import os
from typing import Any


class HttpEmbeddingProvider:
    """Generic embedding provider for local model services.

    Expected response shapes:
    - {"embedding": [...]} for a single input
    - {"embeddings": [[...], [...]]} for batch input
    """

    name = "http"

    def __init__(self, base_url: str | None = None, timeout: float | None = None, dimension: int | None = None):
        self.base_url = (base_url or os.getenv("EMBEDDING_BASE_URL") or "").rstrip("/")
        self.timeout = timeout or float(os.getenv("EMBEDDING_TIMEOUT", "15"))
        self.dimension = dimension or (int(os.getenv("EMBEDDING_DIM")) if os.getenv("EMBEDDING_DIM") else None)
        if not self.base_url:
            raise RuntimeError("EMBEDDING_BASE_URL is required for EMBEDDING_PROVIDER=http")

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx
        resp = httpx.post(f"{self.base_url}/embed", json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def embed_query(self, text: str) -> list[float]:
        data = self._post({"input": text})
        return list(data.get("embedding") or (data.get("embeddings") or [[]])[0])

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        data = self._post({"input": texts})
        embeddings = data.get("embeddings")
        if embeddings is None and data.get("embedding") is not None:
            embeddings = [data.get("embedding")]
        return [list(e) for e in embeddings or []]
