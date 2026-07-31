from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from agent_core.persistence.action_lifecycle_store import ActionLockStore
from agent_core.rag.index_jobs import (
    DocumentIndexJobStore,
    SqlAlchemyDocumentIndexJobStore,
    build_document_job_store,
)
from agent_core.rag.object_store import FilesystemDocumentObjectStore, build_document_object_store
from agent_core.runtime.turn_fencing import (
    AtomicallyFencedPostgresSaver,
    FencedCheckpointer,
    TurnFence,
    activate_turn_fence,
)
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from app.services.document_service import DocumentService
from app.services.turn_lock import ConversationBusyError, ConversationLease


def _enqueue(store, *, max_attempts: int = 3):
    return store.enqueue(
        tenant_id="tenant-a",
        user_id="u001",
        visibility="tenant",
        object_uri="file:///shared/a.txt",
        title="a.txt",
        source="file:///shared/a.txt",
        metadata={"tenant_id": "tenant-a", "owner_id": "u001", "visibility": "tenant"},
        max_attempts=max_attempts,
    )


@pytest.mark.parametrize("backend", ["sqlite", "sqlalchemy"])
def test_document_job_expired_worker_is_reclaimed_and_fenced(
    tmp_path: Path, backend: str, request: pytest.FixtureRequest
) -> None:
    store = (
        DocumentIndexJobStore(tmp_path / "jobs.db")
        if backend == "sqlite"
        else SqlAlchemyDocumentIndexJobStore(f"sqlite:///{tmp_path / 'jobs-sa.db'}", create_schema=True)
    )
    request.addfinalizer(store.close)
    job = _enqueue(store)
    first = store.claim_next(worker_id="worker-a", lease_seconds=60, limit=1)[0]
    assert first["attempt_count"] == 1

    table = getattr(store, "table", None)
    if table is None:
        store.execute(
            "UPDATE document_index_jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE job_id=?",
            (job["job_id"],),
        )
    else:
        with store.engine.begin() as conn:
            conn.execute(table.update().where(table.c.job_id == job["job_id"]).values(lease_until="2000-01-01T00:00:00+00:00"))

    recovered = store.recover_expired()
    assert recovered["requeued"] == 1
    second = store.claim_next(worker_id="worker-b", lease_seconds=60, limit=1)[0]
    assert second["attempt_count"] == 2
    assert store.complete(
        job_id=job["job_id"], worker_id="worker-a", claim_token=str(first["claim_token"]),
        doc_id="stale", chunks=1,
    ) is False
    assert store.complete(
        job_id=job["job_id"], worker_id="worker-b", claim_token=str(second["claim_token"]),
        doc_id=str(job["doc_id"]), chunks=2,
    ) is True


@pytest.mark.parametrize("backend", ["sqlite", "sqlalchemy"])
def test_document_job_same_worker_id_cannot_reuse_stale_claim_token(
    tmp_path: Path, backend: str, request: pytest.FixtureRequest
) -> None:
    store = (
        DocumentIndexJobStore(tmp_path / "same-worker.db")
        if backend == "sqlite"
        else SqlAlchemyDocumentIndexJobStore(f"sqlite:///{tmp_path / 'same-worker-sa.db'}", create_schema=True)
    )
    request.addfinalizer(store.close)
    job = _enqueue(store)
    first = store.claim_next(worker_id="stable-worker-name", lease_seconds=60, limit=1)[0]
    table = getattr(store, "table", None)
    if table is None:
        store.execute(
            "UPDATE document_index_jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE job_id=?",
            (job["job_id"],),
        )
    else:
        with store.engine.begin() as conn:
            conn.execute(
                table.update().where(table.c.job_id == job["job_id"]).values(
                    lease_until="2000-01-01T00:00:00+00:00"
                )
            )
    store.recover_expired()
    second = store.claim_next(worker_id="stable-worker-name", lease_seconds=60, limit=1)[0]
    assert second["claim_token"] != first["claim_token"]
    assert store.complete(
        job_id=job["job_id"], worker_id="stable-worker-name", claim_token=str(first["claim_token"]),
        doc_id="stale", chunks=1,
    ) is False
    assert store.complete(
        job_id=job["job_id"], worker_id="stable-worker-name", claim_token=str(second["claim_token"]),
        doc_id=str(job["doc_id"]), chunks=1,
    ) is True


def test_document_job_retries_are_bounded(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    store = DocumentIndexJobStore(tmp_path / "jobs.db")
    request.addfinalizer(store.close)
    job = _enqueue(store, max_attempts=2)
    first_claim = store.claim_next(worker_id="worker", lease_seconds=60, limit=1)[0]
    first_failure = store.fail(
        job_id=job["job_id"], worker_id="worker", claim_token=str(first_claim["claim_token"]), error="boom"
    )
    assert first_failure and first_failure["state"] == "QUEUED"
    store.execute(
        "UPDATE document_index_jobs SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",
        (job["job_id"],),
    )
    second_claim = store.claim_next(worker_id="worker", lease_seconds=60, limit=1)[0]
    final_failure = store.fail(
        job_id=job["job_id"], worker_id="worker", claim_token=str(second_claim["claim_token"]), error="boom again"
    )
    assert final_failure and final_failure["state"] == "FAILED"
    assert final_failure["attempt_count"] == 2


def test_action_lock_old_fencing_token_cannot_release_new_owner(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    store = ActionLockStore(tmp_path / "locks.db")
    request.addfinalizer(store.close)
    first = store.acquire("thread", owner="a", ttl_seconds=60)
    store.release("thread", owner="a", fencing_token=int(first["fencing_token"]))
    second = store.acquire("thread", owner="b", ttl_seconds=60)
    store.release("thread", owner="a", fencing_token=int(first["fencing_token"]))
    assert store.validate("thread", owner="b", fencing_token=int(second["fencing_token"])) is True
    assert int(second["fencing_token"]) > int(first["fencing_token"])


def test_fenced_checkpointer_is_a_real_langgraph_saver() -> None:
    wrapped = FencedCheckpointer(InMemorySaver())
    assert isinstance(wrapped, BaseCheckpointSaver)
    assert wrapped.config_specs == wrapped._inner.config_specs


def test_fenced_checkpointer_rejects_before_inner_write() -> None:
    class Inner(InMemorySaver):
        calls = 0

        def put(self, *_args, **_kwargs):
            self.calls += 1

    inner = Inner()
    wrapped = FencedCheckpointer(inner)

    def reject() -> None:
        raise ConversationBusyError("stale")

    fence = TurnFence("lock", "owner", 7, reject)
    with activate_turn_fence(fence):
        with pytest.raises(ConversationBusyError):
            wrapped.put({}, {}, {}, {})
    assert inner.calls == 0


def test_fenced_checkpointer_atomic_storage_guard_rejects_before_inner_write() -> None:
    class Inner(InMemorySaver):
        calls = 0

        @contextmanager
        def atomic_fence_write(self):
            expires_during_atomic_guard()
            yield

        def put(self, *_args, **_kwargs):
            self.calls += 1
            return {"configurable": {"checkpoint_id": "written"}}

    validations = 0

    def expires_during_atomic_guard() -> None:
        nonlocal validations
        validations += 1
        if validations > 1:
            raise ConversationBusyError("storage rejected stale token")

    inner = Inner()
    wrapped = FencedCheckpointer(inner)
    fence = TurnFence("lock", "owner", 8, expires_during_atomic_guard)
    with activate_turn_fence(fence):
        with pytest.raises(ConversationBusyError, match="storage rejected stale token"):
            wrapped.put({}, {}, {}, {})
    assert inner.calls == 0
    assert validations == 2


def test_postgres_fence_guard_is_transactional_and_locks_owner_row() -> None:
    sql = AtomicallyFencedPostgresSaver.FENCE_GUARD_SQL.lower()
    assert "agent_action_locks" in sql
    assert "fencing_token = %s" in sql
    assert "owner = %s" in sql
    assert "clock_timestamp()" in sql
    assert "for update" in sql


@pytest.mark.asyncio
async def test_async_fenced_checkpointer_fails_closed_without_atomic_async_storage() -> None:
    class Inner(InMemorySaver):
        calls = 0

        async def aput(self, *_args, **_kwargs):
            self.calls += 1
            return {"configurable": {"checkpoint_id": "unsafe"}}

    inner = Inner()
    wrapped = FencedCheckpointer(inner)
    fence = TurnFence("lock", "owner", 9, lambda: None)
    with activate_turn_fence(fence):
        with pytest.raises(RuntimeError, match="aatomic_fence_write"):
            await wrapped.aput({}, {}, {}, {})
    assert inner.calls == 0


@pytest.mark.asyncio
async def test_async_fenced_checkpointer_uses_atomic_async_storage_contract() -> None:
    class Inner(InMemorySaver):
        calls = 0
        atomic_entries = 0

        @asynccontextmanager
        async def aatomic_fence_write(self):
            self.atomic_entries += 1
            yield

        async def aput(self, *_args, **_kwargs):
            self.calls += 1
            return {"configurable": {"checkpoint_id": "safe"}}

    inner = Inner()
    wrapped = FencedCheckpointer(inner)
    fence = TurnFence("lock", "owner", 10, lambda: None)
    with activate_turn_fence(fence):
        result = await wrapped.aput({}, {}, {}, {})
    assert result["configurable"]["checkpoint_id"] == "safe"
    assert inner.atomic_entries == 1
    assert inner.calls == 1


def test_conversation_lease_fails_closed_when_store_rejects_token() -> None:
    class Store:
        def validate(self, *_args, **_kwargs):
            return False

        def release(self, *_args, **_kwargs):
            return None

    lease = ConversationLease(Store(), lock_key="thread", owner="old", fencing_token=1, ttl_seconds=30)
    with pytest.raises(ConversationBusyError):
        lease.assert_valid()


def test_document_service_upload_uses_object_uri_and_finishes_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    jobs = DocumentIndexJobStore(tmp_path / "jobs.db")
    request.addfinalizer(jobs.close)
    objects = FilesystemDocumentObjectStore(tmp_path / "objects", shared=False)

    class Rag:
        def ingest_file(self, path, *, title, source, metadata, doc_id=None):
            assert Path(path).read_text(encoding="utf-8") == "hello"
            assert source.startswith("file://")
            return {"doc_id": doc_id, "chunks": 1}

    monkeypatch.setenv("APP_PROFILE", "local")
    monkeypatch.setenv("RAG_INDEX_INLINE_LOCAL", "true")
    monkeypatch.setattr("app.services.document_service.get_rag_provider", lambda: Rag())
    monkeypatch.setattr(
        "app.services.document_service.RagBootstrapService.verify_readiness",
        lambda self, seed=False: {"ready": True},
    )
    service = DocumentService(jobs=jobs, object_store=objects)
    result = service.upload(
        UploadFile(filename="a.txt", file=BytesIO(b"hello")),
        tenant_id="tenant-a",
        user_id="u001",
    )
    assert result["status"] == "READY"
    assert result["doc_id"].startswith("doc_")
    assert result["source"].startswith("file://")


def test_document_retry_upserts_same_doc_id_instead_of_duplicating_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    jobs = DocumentIndexJobStore(tmp_path / "retry-jobs.db")
    request.addfinalizer(jobs.close)
    objects = FilesystemDocumentObjectStore(tmp_path / "retry-objects", shared=False)
    indexed: dict[str, str] = {}
    calls: list[str] = []

    class Rag:
        def ingest_file(self, path, *, title, source, metadata, doc_id=None):
            assert doc_id
            calls.append(doc_id)
            indexed[doc_id] = Path(path).read_text(encoding="utf-8")
            return {"doc_id": doc_id, "chunks": 1}

    monkeypatch.setenv("APP_PROFILE", "local")
    monkeypatch.setenv("RAG_INDEX_INLINE_LOCAL", "false")
    monkeypatch.setattr("app.services.document_service.get_rag_provider", lambda: Rag())
    monkeypatch.setattr(
        "app.services.document_service.RagBootstrapService.verify_readiness",
        lambda self, seed=False: {"ready": True},
    )
    service = DocumentService(jobs=jobs, object_store=objects)
    queued = service.upload(
        UploadFile(filename="retry.txt", file=BytesIO(b"same payload")),
        tenant_id="tenant-a",
        user_id="u001",
    )
    original_complete = jobs.complete
    completions = 0

    def reject_first_completion(**kwargs):
        nonlocal completions
        completions += 1
        if completions == 1:
            return False
        return original_complete(**kwargs)

    monkeypatch.setattr(jobs, "complete", reject_first_completion)
    service.process_pending_jobs(limit=1)
    jobs.execute(
        "UPDATE document_index_jobs SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",
        (queued["job_id"],),
    )
    service.process_pending_jobs(limit=1)

    final = jobs.get_for_scope(
        job_id=queued["job_id"], tenant_id="tenant-a", user_id="u001"
    )
    assert final and final["state"] == "READY"
    assert calls == [queued["doc_id"], queued["doc_id"]]
    assert list(indexed) == [queued["doc_id"]]


def test_protected_profile_rejects_local_document_object_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "production")
    monkeypatch.setenv("DOCUMENT_OBJECT_STORE_BACKEND", "local_filesystem")
    with pytest.raises(RuntimeError, match="must be shared"):
        build_document_object_store()


def test_protected_profile_rejects_sqlalchemy_queue_using_local_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_PROFILE", "production")
    monkeypatch.setenv("DOCUMENT_JOB_BACKEND", "sqlalchemy")
    monkeypatch.setenv("DOCUMENT_JOB_DATABASE_URL", "sqlite:////tmp/document-jobs.db")
    with pytest.raises(RuntimeError, match="must use PostgreSQL"):
        build_document_job_store()
