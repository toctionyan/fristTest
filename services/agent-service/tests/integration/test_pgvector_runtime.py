"""Real pgvector contract used by the controlled integration gate.

The unit suite verifies query construction separately.  This test deliberately
talks to the pgvector service that CI starts, so an unavailable extension,
invalid vector dimension, or lost tenant scope cannot be hidden by a mock.
"""
from __future__ import annotations

import os
import uuid

import pytest

from agent_core.rag.providers.pgvector_provider import PgVectorRagProvider


class _DeterministicEmbeddings:
    """Small fixed vectors keep this database contract offline and repeatable."""

    def embed_documents(self, texts: list[str]) -> list[str]:
        return ["[1,0,0]" for _ in texts]

    def embed_query(self, query: str) -> str:
        return "[1,0,0]"


@pytest.mark.integration
def test_pgvector_ingest_search_and_scope_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ["AGENT_TEST_POSTGRES_URL"]
    collection = f"quality_loop_{uuid.uuid4().hex}"
    monkeypatch.setenv("EMBEDDING_DIM", "3")
    monkeypatch.setenv("RAG_CREATE_SCHEMA", "true")

    provider = PgVectorRagProvider(
        database_url=database_url,
        collection=collection,
        embedding_provider=_DeterministicEmbeddings(),
    )
    provider.upsert_document(
        "tenant-a-order-policy",
        "Order policy",
        "quality-loop-test",
        ["Only the owning tenant may read this policy."],
        metadata={"tenant_id": "tenant-a", "visibility": "tenant"},
    )

    visible = provider.search(
        "order policy",
        filters={"__access_scope__": {"tenant_id": "tenant-a", "owner_id": "customer-1"}},
    )
    hidden = provider.search(
        "order policy",
        filters={"__access_scope__": {"tenant_id": "tenant-b", "owner_id": "customer-2"}},
    )

    assert [item["doc_id"] for item in visible] == ["tenant-a-order-policy"]
    assert hidden == []
