from __future__ import annotations

import os
import socket
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from fastapi import UploadFile

from agent_core.rag.access import document_metadata_for_scope, normalize_scope, scope_filter
from agent_core.rag.factory import get_rag_provider
from agent_core.rag.bootstrap import RagBootstrapService
from agent_core.rag.index_jobs import DocumentIndexJobRepository, build_document_job_store
from agent_core.rag.object_store import DocumentObjectStore, build_document_object_store
from agent_core.runtime.profile import is_local_profile


class _DocumentJobLease:
    def __init__(self, jobs: DocumentIndexJobRepository, *, job_id: str, worker_id: str, claim_token: str, lease_seconds: int) -> None:
        self.jobs = jobs
        self.job_id = job_id
        self.worker_id = worker_id
        self.claim_token = claim_token
        self.lease_seconds = max(3, int(lease_seconds))
        self.interval = max(1.0, min(self.lease_seconds / 3.0, 30.0))
        self.stop = Event()
        self.lost = Event()
        self.thread = Thread(target=self._run, name=f"document-job-{job_id}", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                renewed = self.jobs.heartbeat(
                    job_id=self.job_id,
                    worker_id=self.worker_id,
                    claim_token=self.claim_token,
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                renewed = False
            if not renewed:
                self.lost.set()
                self.stop.set()
                return

    def assert_valid(self) -> None:
        if self.lost.is_set():
            raise RuntimeError("document indexing lease was lost")

    def close(self) -> None:
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=min(2.0, self.interval + 0.5))


class DocumentService:
    """Scoped document service backed by a shared lease queue and object store."""

    def __init__(
        self,
        *,
        jobs: DocumentIndexJobRepository | None = None,
        object_store: DocumentObjectStore | None = None,
    ) -> None:
        self.jobs = jobs or build_document_job_store()
        self.object_store = object_store or build_document_object_store()
        self.worker_id = (
            os.getenv("DOCUMENT_WORKER_ID")
            or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        )
        readiness = RagBootstrapService().verify_readiness(seed=False)
        self.bootstrap_error = None if readiness.get("ready") else str(readiness.get("error") or "RAG unavailable")

    def close(self) -> None:
        """Close resources constructed and owned by this service."""
        seen: set[int] = set()
        for resource in (getattr(self, "jobs", None), getattr(self, "object_store", None)):
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _scope(*, tenant_id: str | None, user_id: str | None, role: str | None = None) -> dict[str, str]:
        return normalize_scope(tenant_id=tenant_id, user_id=user_id, role=role)

    @staticmethod
    def _allowed_filename(name: str) -> str:
        safe_name = Path(name or "upload.txt").name
        allowed = {".txt", ".md", ".pdf", ".docx", ".csv", ".json"}
        if Path(safe_name).suffix.lower() not in allowed:
            raise ValueError("unsupported document type")
        return safe_name

    @staticmethod
    def _max_bytes() -> int:
        return max(1, int(os.getenv("RAG_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))))

    @staticmethod
    def _lease_seconds() -> int:
        return max(30, min(int(os.getenv("DOCUMENT_JOB_LEASE_SECONDS", "300")), 3600))

    @staticmethod
    def _max_attempts() -> int:
        return max(1, min(int(os.getenv("DOCUMENT_JOB_MAX_ATTEMPTS", "3")), 20))

    def _process_job(self, job: dict) -> dict:
        job_id = str(job["job_id"])
        claim_token = str(job.get("claim_token") or "")
        if not claim_token:
            raise RuntimeError("document job claim did not issue a claim token")
        lease = _DocumentJobLease(
            self.jobs,
            job_id=job_id,
            worker_id=self.worker_id,
            claim_token=claim_token,
            lease_seconds=self._lease_seconds(),
        )
        lease.start()
        try:
            with self.object_store.materialize(str(job["object_uri"])) as local_path:
                result = get_rag_provider().ingest_file(
                    str(local_path),
                    title=job["title"],
                    source=job["source"],
                    metadata=dict(job.get("metadata") or {}),
                    doc_id=str(job["doc_id"]),
                )
            if str(result.get("doc_id") or "") != str(job["doc_id"]):
                raise RuntimeError("RAG provider returned a different document identity")
            lease.assert_valid()
            completed = self.jobs.complete(
                job_id=job_id,
                worker_id=self.worker_id,
                claim_token=claim_token,
                doc_id=str(result["doc_id"]),
                chunks=int(result.get("chunks") or 0),
            )
            if not completed:
                raise RuntimeError("document indexing completion rejected stale worker")
        finally:
            lease.close()
        return self.jobs.get_for_scope(
            job_id=job_id,
            tenant_id=str(job["tenant_id"]),
            user_id=str(job["user_id"]),
        ) or {}

    def process_pending_jobs(self, *, limit: int = 10) -> list[dict]:
        self.jobs.recover_expired()
        rows: list[dict] = []
        for job in self.jobs.claim_next(
            worker_id=self.worker_id,
            lease_seconds=self._lease_seconds(),
            limit=limit,
        ):
            try:
                rows.append(self._process_job(job))
            except Exception as exc:
                failed = self.jobs.fail(
                    job_id=str(job["job_id"]),
                    worker_id=self.worker_id,
                    claim_token=str(job.get("claim_token") or ""),
                    error=f"{exc.__class__.__name__}: {exc}",
                    retryable=True,
                )
                rows.append(failed or self.jobs.get_for_scope(
                    job_id=str(job["job_id"]),
                    tenant_id=str(job["tenant_id"]),
                    user_id=str(job["user_id"]),
                ) or {})
        return rows

    def upload(
        self,
        file: UploadFile,
        *,
        tenant_id: str | None,
        user_id: str | None,
        role: str | None = None,
        visibility: str = "tenant",
    ) -> dict:
        scope = self._scope(tenant_id=tenant_id, user_id=user_id, role=role)
        if not scope["tenant_id"] or not scope["owner_id"]:
            raise ValueError("tenant_id and authenticated user_id are required for document upload")
        safe_name = self._allowed_filename(file.filename or "upload.txt")
        object_key = f"{scope['tenant_id']}/{scope['owner_id']}/{uuid4().hex}_{safe_name}"
        object_uri = self.object_store.put(
            file.file,
            object_key=object_key,
            max_bytes=self._max_bytes(),
        )
        metadata = document_metadata_for_scope(scope, visibility=visibility, builtin=False)
        job = self.jobs.enqueue(
            tenant_id=scope["tenant_id"],
            user_id=scope["owner_id"],
            visibility=metadata["visibility"],
            object_uri=object_uri,
            title=safe_name,
            source=object_uri,
            metadata=metadata,
            max_attempts=self._max_attempts(),
        )
        inline = os.getenv(
            "RAG_INDEX_INLINE_LOCAL", "true" if is_local_profile() else "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if inline:
            self.process_pending_jobs(limit=10)
            job = self.jobs.get_for_scope(
                job_id=str(job["job_id"]),
                tenant_id=scope["tenant_id"],
                user_id=scope["owner_id"],
            ) or job
        return {
            "doc_id": str(job.get("doc_id") or ""),
            "title": safe_name,
            "chunks": int(job.get("chunks") or 0),
            "source": object_uri,
            "job_id": str(job["job_id"]),
            "status": str(job.get("state") or "QUEUED"),
            "visibility": metadata["visibility"],
        }

    def get_job(self, job_id: str, *, tenant_id: str | None, user_id: str | None, role: str | None = None) -> dict | None:
        scope = self._scope(tenant_id=tenant_id, user_id=user_id, role=role)
        return self.jobs.get_for_scope(job_id=job_id, tenant_id=scope["tenant_id"], user_id=scope["owner_id"])

    def list_documents(self, *, tenant_id: str | None, user_id: str | None, role: str | None = None) -> list[dict]:
        scope = self._scope(tenant_id=tenant_id, user_id=user_id, role=role)
        return get_rag_provider().list_documents(filters=scope_filter(scope))

    def get_document(self, doc_id: str, *, tenant_id: str | None, user_id: str | None, role: str | None = None) -> dict | None:
        scope = self._scope(tenant_id=tenant_id, user_id=user_id, role=role)
        return get_rag_provider().get_document(doc_id, filters=scope_filter(scope))

    def list_chunks(self, doc_id: str, *, tenant_id: str | None, user_id: str | None, role: str | None = None) -> list[dict]:
        scope = self._scope(tenant_id=tenant_id, user_id=user_id, role=role)
        return get_rag_provider().list_chunks(doc_id, filters=scope_filter(scope))

    def search(self, query: str, *, tenant_id: str | None, user_id: str | None, role: str | None = None, top_k: int = 5) -> list[dict]:
        scope = self._scope(tenant_id=tenant_id, user_id=user_id, role=role)
        return get_rag_provider().search(query, top_k=top_k, filters=scope_filter(scope))
