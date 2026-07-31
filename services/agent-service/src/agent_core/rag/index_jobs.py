from __future__ import annotations

"""Lease-based document indexing queue.

Local development may use SQLite. Protected profiles must use the SQLAlchemy
repository against the shared Agent database. Claims carry a worker owner, unique claim token,
lease expiry and attempt counter so a crashed worker can be reclaimed instead
of leaving a job permanently in INDEXING.
"""

import json
import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from agent_core.persistence.sqlite_base import SQLiteBase
from agent_core.runtime.profile import RuntimeProfile, get_runtime_profile


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _now_dt().isoformat()


def _after(seconds: int) -> str:
    return (_now_dt() + timedelta(seconds=max(1, int(seconds)))).isoformat()


def desired_document_id(job_id: str, object_uri: str) -> str:
    """Stable RAG identity for every attempt of one indexing job."""
    digest = hashlib.sha256(f"{job_id}\0{object_uri}".encode("utf-8")).hexdigest()
    return f"doc_{digest[:24]}"


class DocumentIndexJobRepository(Protocol):
    def enqueue(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_for_scope(self, *, job_id: str, tenant_id: str, user_id: str) -> dict[str, Any] | None: ...
    def claim_next(self, *, worker_id: str, lease_seconds: int, limit: int = 1) -> list[dict[str, Any]]: ...
    def heartbeat(self, *, job_id: str, worker_id: str, claim_token: str, lease_seconds: int) -> bool: ...
    def complete(self, *, job_id: str, worker_id: str, claim_token: str, doc_id: str, chunks: int) -> bool: ...
    def fail(self, *, job_id: str, worker_id: str, claim_token: str, error: str, retryable: bool = True) -> dict[str, Any] | None: ...
    def recover_expired(self) -> dict[str, int]: ...


class DocumentIndexJobStore(SQLiteBase):
    """SQLite implementation for explicit local profile only."""

    def init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_index_jobs (
                job_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                visibility TEXT NOT NULL,
                object_uri TEXT,
                file_path TEXT,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                state TEXT NOT NULL,
                doc_id TEXT,
                chunks INTEGER,
                error TEXT,
                worker_id TEXT,
                claim_token TEXT,
                lease_until TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                next_attempt_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(document_index_jobs)").fetchall()}
        additions = {
            "object_uri": "TEXT",
            "worker_id": "TEXT",
            "claim_token": "TEXT",
            "lease_until": "TEXT",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "max_attempts": "INTEGER NOT NULL DEFAULT 3",
            "next_attempt_at": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE document_index_jobs ADD COLUMN {name} {declaration}")
        now = _now()
        self.conn.execute("UPDATE document_index_jobs SET object_uri=COALESCE(NULLIF(object_uri,''), file_path)")
        self.conn.execute("UPDATE document_index_jobs SET next_attempt_at=COALESCE(next_attempt_at, created_at, ?)", (now,))
        self.conn.execute("UPDATE document_index_jobs SET max_attempts=3 WHERE max_attempts IS NULL OR max_attempts<1")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_document_jobs_scope_state ON document_index_jobs(tenant_id,user_id,state,created_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_document_jobs_claim ON document_index_jobs(state,next_attempt_at,lease_until,created_at)")
        self.conn.commit()

    def enqueue(
        self,
        *,
        tenant_id: str,
        user_id: str,
        visibility: str,
        title: str,
        source: str,
        metadata: dict[str, Any],
        object_uri: str | None = None,
        file_path: str | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        job_id = f"ragjob_{uuid4().hex}"
        now = _now()
        uri = str(object_uri or file_path or "")
        if not uri:
            raise ValueError("document job requires object_uri")
        doc_id = desired_document_id(job_id, uri)
        self.execute(
            """INSERT INTO document_index_jobs(
                   job_id,tenant_id,user_id,visibility,object_uri,file_path,title,source,metadata_json,
                   state,doc_id,chunks,error,worker_id,claim_token,lease_until,attempt_count,max_attempts,next_attempt_at,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,'QUEUED',?,NULL,NULL,NULL,NULL,NULL,0,?,?,?,?)""",
            (
                job_id, tenant_id, user_id, visibility, uri, str(file_path or ""), title, source,
                json.dumps(metadata, ensure_ascii=False), doc_id, max(1, int(max_attempts)), now, now, now,
            ),
        )
        return self.get_for_scope(job_id=job_id, tenant_id=tenant_id, user_id=user_id) or {}

    @staticmethod
    def _decode(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except Exception:
            item["metadata"] = {}
        item["object_uri"] = str(item.get("object_uri") or item.get("file_path") or "")
        return item

    def get_for_scope(self, *, job_id: str, tenant_id: str, user_id: str) -> dict[str, Any] | None:
        return self._decode(self.query_one(
            "SELECT * FROM document_index_jobs WHERE job_id=? AND tenant_id=? AND user_id=?",
            (job_id, tenant_id, user_id),
        ))

    def recover_expired(self) -> dict[str, int]:
        now = _now()
        requeued = failed = 0
        with self.lock:
            rows = [dict(row) for row in self.conn.execute(
                "SELECT job_id,attempt_count,max_attempts FROM document_index_jobs WHERE state='INDEXING' AND lease_until IS NOT NULL AND lease_until<=?",
                (now,),
            ).fetchall()]
            for row in rows:
                if int(row.get("attempt_count") or 0) >= int(row.get("max_attempts") or 3):
                    self.conn.execute(
                        "UPDATE document_index_jobs SET state='FAILED',worker_id=NULL,claim_token=NULL,lease_until=NULL,error=COALESCE(error,'indexing lease expired'),updated_at=? WHERE job_id=? AND state='INDEXING'",
                        (now, row["job_id"]),
                    )
                    failed += 1
                else:
                    self.conn.execute(
                        "UPDATE document_index_jobs SET state='QUEUED',worker_id=NULL,claim_token=NULL,lease_until=NULL,next_attempt_at=?,error=COALESCE(error,'indexing lease expired; requeued'),updated_at=? WHERE job_id=? AND state='INDEXING'",
                        (now, now, row["job_id"]),
                    )
                    requeued += 1
            self.conn.commit()
        return {"requeued": requeued, "failed": failed}

    def claim_next(self, *, worker_id: str = "local-worker", lease_seconds: int = 120, limit: int = 1) -> list[dict[str, Any]]:
        self.recover_expired()
        now = _now()
        lease_until = _after(lease_seconds)
        claimed: list[dict[str, Any]] = []
        with self.lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                rows = [dict(row) for row in self.conn.execute(
                    """SELECT * FROM document_index_jobs
                       WHERE state='QUEUED' AND next_attempt_at<=? AND attempt_count<max_attempts
                       ORDER BY created_at ASC LIMIT ?""",
                    (now, max(1, min(int(limit), 100))),
                ).fetchall()]
                for row in rows:
                    claim_token = uuid4().hex
                    changed = self.conn.execute(
                        """UPDATE document_index_jobs
                           SET state='INDEXING',worker_id=?,claim_token=?,lease_until=?,attempt_count=attempt_count+1,updated_at=?
                           WHERE job_id=? AND state='QUEUED' AND next_attempt_at<=?""",
                        (worker_id, claim_token, lease_until, now, row["job_id"], now),
                    ).rowcount
                    if changed:
                        fresh = self.conn.execute("SELECT * FROM document_index_jobs WHERE job_id=?", (row["job_id"],)).fetchone()
                        claimed.append(self._decode(dict(fresh)) or {})
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return claimed

    def heartbeat(self, *, job_id: str, worker_id: str, claim_token: str, lease_seconds: int = 120) -> bool:
        now = _now()
        lease_until = _after(lease_seconds)
        with self.lock:
            changed = self.conn.execute(
                """UPDATE document_index_jobs SET lease_until=?,updated_at=?
                   WHERE job_id=? AND state='INDEXING' AND worker_id=? AND claim_token=? AND lease_until>?""",
                (lease_until, now, job_id, worker_id, claim_token, now),
            ).rowcount
            self.conn.commit()
        return int(changed or 0) == 1

    def complete(self, *, job_id: str, worker_id: str = "local-worker", claim_token: str, doc_id: str, chunks: int) -> bool:
        with self.lock:
            changed = self.conn.execute(
                """UPDATE document_index_jobs
                   SET state='READY',doc_id=?,chunks=?,error=NULL,worker_id=NULL,claim_token=NULL,lease_until=NULL,updated_at=?
                   WHERE job_id=? AND state='INDEXING' AND worker_id=? AND claim_token=? AND lease_until>? AND doc_id=?""",
                (doc_id, int(chunks), _now(), job_id, worker_id, claim_token, _now(), doc_id),
            ).rowcount
            self.conn.commit()
        return int(changed or 0) == 1

    def fail(self, *, job_id: str, worker_id: str = "local-worker", claim_token: str, error: str, retryable: bool = True) -> dict[str, Any] | None:
        now = _now_dt()
        with self.lock:
            row = self.conn.execute(
                "SELECT tenant_id,user_id,attempt_count,max_attempts FROM document_index_jobs WHERE job_id=? AND state='INDEXING' AND worker_id=? AND claim_token=? AND lease_until>?",
                (job_id, worker_id, claim_token, now.isoformat()),
            ).fetchone()
            if row is None:
                return None
            attempts = int(row["attempt_count"] or 0)
            max_attempts = int(row["max_attempts"] or 3)
            should_retry = bool(retryable and attempts < max_attempts)
            state = "QUEUED" if should_retry else "FAILED"
            delay = min(300, 2 ** max(0, attempts - 1)) if should_retry else 0
            next_attempt = (now + timedelta(seconds=delay)).isoformat()
            self.conn.execute(
                """UPDATE document_index_jobs
                   SET state=?,error=?,worker_id=NULL,claim_token=NULL,lease_until=NULL,next_attempt_at=?,updated_at=?
                   WHERE job_id=? AND state='INDEXING' AND worker_id=? AND claim_token=?""",
                (state, str(error)[:1000], next_attempt, now.isoformat(), job_id, worker_id, claim_token),
            )
            self.conn.commit()
            tenant_id, user_id = str(row["tenant_id"]), str(row["user_id"])
        return self.get_for_scope(job_id=job_id, tenant_id=tenant_id, user_id=user_id)

    def list_pending(self, *, limit: int = 10) -> list[dict[str, Any]]:
        return [self._decode(row) or {} for row in self.query_all(
            "SELECT * FROM document_index_jobs WHERE state IN ('QUEUED','INDEXING') ORDER BY created_at ASC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        )]


class SqlAlchemyDocumentIndexJobStore:
    """Shared-database queue implementation for PostgreSQL/SQLAlchemy."""

    def __init__(self, database_url: str, *, create_schema: bool = False) -> None:
        try:
            import sqlalchemy as sa  # type: ignore
        except Exception as exc:
            raise RuntimeError("shared document jobs require SQLAlchemy") from exc
        self.sa = sa
        if database_url.startswith("postgresql://"):
            database_url = "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
        self.engine = sa.create_engine(database_url, pool_pre_ping=True, future=True)
        metadata = sa.MetaData()
        self.table = sa.Table(
            "agent_document_index_jobs", metadata,
            sa.Column("job_id", sa.String(80), primary_key=True),
            sa.Column("tenant_id", sa.String(255), nullable=False, index=True),
            sa.Column("user_id", sa.String(255), nullable=False, index=True),
            sa.Column("visibility", sa.String(32), nullable=False),
            sa.Column("object_uri", sa.Text(), nullable=False),
            sa.Column("title", sa.String(512), nullable=False),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("state", sa.String(32), nullable=False, index=True),
            sa.Column("doc_id", sa.String(255), nullable=True),
            sa.Column("chunks", sa.Integer(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("worker_id", sa.String(255), nullable=True, index=True),
            sa.Column("claim_token", sa.String(64), nullable=True, index=True),
            sa.Column("lease_until", sa.String(64), nullable=True, index=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, default=0),
            sa.Column("max_attempts", sa.Integer(), nullable=False, default=3),
            sa.Column("next_attempt_at", sa.String(64), nullable=False, index=True),
            sa.Column("created_at", sa.String(64), nullable=False),
            sa.Column("updated_at", sa.String(64), nullable=False),
            sa.Index("idx_agent_document_jobs_claim", "state", "next_attempt_at", "lease_until", "created_at"),
        )
        if create_schema:
            metadata.create_all(self.engine)
        else:
            inspector = sa.inspect(self.engine)
            if not inspector.has_table(self.table.name):
                raise RuntimeError(
                    "agent_document_index_jobs is missing; run Agent Alembic migration 0005_production_integrity"
                )

    @staticmethod
    def _decode(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row._mapping if hasattr(row, "_mapping") else row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except Exception:
            item["metadata"] = {}
        return item

    def enqueue(self, *, tenant_id: str, user_id: str, visibility: str, title: str, source: str, metadata: dict[str, Any], object_uri: str | None = None, file_path: str | None = None, max_attempts: int = 3) -> dict[str, Any]:
        uri = str(object_uri or file_path or "")
        if not uri:
            raise ValueError("document job requires object_uri")
        job_id = f"ragjob_{uuid4().hex}"
        doc_id = desired_document_id(job_id, uri)
        now = _now()
        with self.engine.begin() as conn:
            conn.execute(self.table.insert().values(
                job_id=job_id, tenant_id=tenant_id, user_id=user_id, visibility=visibility,
                object_uri=uri, title=title, source=source,
                metadata_json=json.dumps(metadata, ensure_ascii=False), state="QUEUED",
                doc_id=doc_id, chunks=None, error=None, worker_id=None, claim_token=None, lease_until=None,
                attempt_count=0, max_attempts=max(1, int(max_attempts)), next_attempt_at=now,
                created_at=now, updated_at=now,
            ))
        return self.get_for_scope(job_id=job_id, tenant_id=tenant_id, user_id=user_id) or {}

    def get_for_scope(self, *, job_id: str, tenant_id: str, user_id: str) -> dict[str, Any] | None:
        stmt = self.sa.select(self.table).where(
            self.table.c.job_id == job_id,
            self.table.c.tenant_id == tenant_id,
            self.table.c.user_id == user_id,
        )
        with self.engine.begin() as conn:
            return self._decode(conn.execute(stmt).first())

    def recover_expired(self) -> dict[str, int]:
        now = _now()
        requeued = failed = 0
        with self.engine.begin() as conn:
            rows = conn.execute(self.sa.select(self.table).where(
                self.table.c.state == "INDEXING",
                self.table.c.lease_until.is_not(None),
                self.table.c.lease_until <= now,
            ).with_for_update()).fetchall()
            for row in rows:
                item = self._decode(row) or {}
                if int(item.get("attempt_count") or 0) >= int(item.get("max_attempts") or 3):
                    state, failed = "FAILED", failed + 1
                else:
                    state, requeued = "QUEUED", requeued + 1
                conn.execute(self.table.update().where(self.table.c.job_id == item["job_id"]).values(
                    state=state, worker_id=None, claim_token=None, lease_until=None, next_attempt_at=now,
                    error=(item.get("error") or "indexing lease expired"), updated_at=now,
                ))
        return {"requeued": requeued, "failed": failed}

    def claim_next(self, *, worker_id: str, lease_seconds: int = 120, limit: int = 1) -> list[dict[str, Any]]:
        self.recover_expired()
        now, lease_until = _now(), _after(lease_seconds)
        claimed: list[dict[str, Any]] = []
        with self.engine.begin() as conn:
            stmt = self.sa.select(self.table).where(
                self.table.c.state == "QUEUED",
                self.table.c.next_attempt_at <= now,
                self.table.c.attempt_count < self.table.c.max_attempts,
            ).order_by(self.table.c.created_at.asc()).limit(max(1, min(int(limit), 100)))
            if conn.dialect.name == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)
            else:
                stmt = stmt.with_for_update()
            for row in conn.execute(stmt).fetchall():
                item = self._decode(row) or {}
                claim_token = uuid4().hex
                result = conn.execute(self.table.update().where(
                    self.table.c.job_id == item["job_id"], self.table.c.state == "QUEUED"
                ).values(
                    state="INDEXING", worker_id=worker_id, claim_token=claim_token, lease_until=lease_until,
                    attempt_count=int(item.get("attempt_count") or 0) + 1, updated_at=now,
                ))
                if int(result.rowcount or 0) == 1:
                    fresh = conn.execute(self.sa.select(self.table).where(self.table.c.job_id == item["job_id"])).first()
                    claimed.append(self._decode(fresh) or {})
        return claimed

    def heartbeat(self, *, job_id: str, worker_id: str, claim_token: str, lease_seconds: int = 120) -> bool:
        now, lease_until = _now(), _after(lease_seconds)
        with self.engine.begin() as conn:
            result = conn.execute(self.table.update().where(
                self.table.c.job_id == job_id,
                self.table.c.state == "INDEXING",
                self.table.c.worker_id == worker_id,
                self.table.c.claim_token == claim_token,
                self.table.c.lease_until > now,
            ).values(lease_until=lease_until, updated_at=now))
        return int(result.rowcount or 0) == 1

    def complete(self, *, job_id: str, worker_id: str, claim_token: str, doc_id: str, chunks: int) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(self.table.update().where(
                self.table.c.job_id == job_id,
                self.table.c.state == "INDEXING",
                self.table.c.worker_id == worker_id,
                self.table.c.claim_token == claim_token,
                self.table.c.lease_until > _now(),
                self.table.c.doc_id == doc_id,
            ).values(state="READY", doc_id=doc_id, chunks=int(chunks), error=None, worker_id=None, claim_token=None, lease_until=None, updated_at=_now()))
        return int(result.rowcount or 0) == 1

    def fail(self, *, job_id: str, worker_id: str, claim_token: str, error: str, retryable: bool = True) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(self.sa.select(self.table).where(
                self.table.c.job_id == job_id,
                self.table.c.state == "INDEXING",
                self.table.c.worker_id == worker_id,
                self.table.c.claim_token == claim_token,
                self.table.c.lease_until > _now(),
            ).with_for_update()).first()
            item = self._decode(row)
            if item is None:
                return None
            attempts, maximum = int(item.get("attempt_count") or 0), int(item.get("max_attempts") or 3)
            retry = bool(retryable and attempts < maximum)
            delay = min(300, 2 ** max(0, attempts - 1)) if retry else 0
            next_attempt = (_now_dt() + timedelta(seconds=delay)).isoformat()
            conn.execute(self.table.update().where(self.table.c.job_id == job_id).values(
                state="QUEUED" if retry else "FAILED", error=str(error)[:1000],
                worker_id=None, claim_token=None, lease_until=None, next_attempt_at=next_attempt, updated_at=_now(),
            ))
            tenant_id, user_id = str(item["tenant_id"]), str(item["user_id"])
        return self.get_for_scope(job_id=job_id, tenant_id=tenant_id, user_id=user_id)

    def list_pending(self, *, limit: int = 10) -> list[dict[str, Any]]:
        stmt = self.sa.select(self.table).where(self.table.c.state.in_(["QUEUED", "INDEXING"])).order_by(self.table.c.created_at.asc()).limit(max(1, min(int(limit), 100)))
        with self.engine.begin() as conn:
            return [self._decode(row) or {} for row in conn.execute(stmt).fetchall()]

    def close(self) -> None:
        self.engine.dispose()


def build_document_job_store() -> DocumentIndexJobRepository:
    profile = get_runtime_profile(strict=True)
    backend = (os.getenv("DOCUMENT_JOB_BACKEND") or ("sqlite" if profile is RuntimeProfile.LOCAL else "sqlalchemy")).strip().lower()
    if profile in {RuntimeProfile.PREPROD, RuntimeProfile.PRODUCTION} and backend in {"sqlite", "local"}:
        raise RuntimeError("DOCUMENT_JOB_BACKEND must use the shared database in preprod/production")
    if backend in {"sqlite", "local"}:
        from agent_core.config import get_storage_paths
        return DocumentIndexJobStore(get_storage_paths()["sqlite_db"].with_name("document_index_jobs.db"))
    if backend in {"sqlalchemy", "postgres", "postgresql"}:
        database_url = (os.getenv("DOCUMENT_JOB_DATABASE_URL") or os.getenv("AGENT_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
        if not database_url:
            raise RuntimeError("DOCUMENT_JOB_BACKEND=sqlalchemy requires DOCUMENT_JOB_DATABASE_URL or AGENT_DATABASE_URL")
        if profile in {RuntimeProfile.PREPROD, RuntimeProfile.PRODUCTION} and not database_url.lower().startswith(
            ("postgresql://", "postgresql+")
        ):
            raise RuntimeError(
                "DOCUMENT_JOB_DATABASE_URL must use PostgreSQL in preprod/production"
            )
        create_schema = (os.getenv("DOCUMENT_JOB_CREATE_SCHEMA") or "false").strip().lower() in {"1", "true", "yes", "on"}
        return SqlAlchemyDocumentIndexJobStore(database_url, create_schema=create_schema)
    raise RuntimeError("DOCUMENT_JOB_BACKEND must be sqlite or sqlalchemy")
