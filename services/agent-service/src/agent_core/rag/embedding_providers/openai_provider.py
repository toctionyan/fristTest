from __future__ import annotations

import os
from typing import Any


class OpenAIEmbeddingProvider:
    """OpenAI-compatible embedding provider using the openai Python package.

    This is optional and imported lazily so local tests do not need the package.
    Embedding credentials can be configured separately from chat credentials with
    EMBEDDING_API_KEY and EMBEDDING_API_BASE.  If they are not set, the provider
    falls back to OPENAI_API_KEY and OPENAI_API_BASE for backward compatibility.
    """

    name = "openai"

    def __init__(self, model: str | None = None, dimension: int | None = None):
        self.model = model or os.getenv("EMBEDDING_MODEL") or "text-embedding-v4"
        self.dimension = dimension or (int(os.getenv("EMBEDDING_DIM")) if os.getenv("EMBEDDING_DIM") else None)
        self.batch_size = max(1, int(os.getenv("EMBEDDING_BATCH_SIZE", "10")))
        if "EMBEDDING_API_KEY" in os.environ:
            api_key = os.getenv("EMBEDDING_API_KEY") or None
        else:
            api_key = os.getenv("OPENAI_API_KEY")
        if "EMBEDDING_API_BASE" in os.environ:
            base_url = os.getenv("EMBEDDING_API_BASE") or None
        else:
            base_url = os.getenv("OPENAI_API_BASE") or None
        if not api_key:
            raise RuntimeError(
                "EMBEDDING_PROVIDER=openai requires EMBEDDING_API_KEY or OPENAI_API_KEY. "
                "Set EMBEDDING_API_KEY when chat uses a provider that does not support embeddings."
            )
        try:
            from openai import OpenAI  # type: ignore
        except Exception as e:
            raise RuntimeError("EMBEDDING_PROVIDER=openai requires the openai package. Install openai>=1.0.") from e
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        kwargs: dict[str, Any] = {"model": self.model}
        if self.dimension:
            kwargs["dimensions"] = self.dimension
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            response = self.client.embeddings.create(input=texts[start:start + self.batch_size], **kwargs)
            embeddings.extend(list(item.embedding) for item in response.data)
        return embeddings
