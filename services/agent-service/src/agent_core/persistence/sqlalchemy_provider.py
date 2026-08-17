from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from agent_core.storage.repositories.base import TransactionScope, ActiveDraftValidationCode, ActiveDraftValidationResult
from agent_core.operations.draft import draft_persistence_update_decision
from agent_core.storage.transaction_policy import attempt_persistence_update_decision, draft_terminal_observation_decision, existing_attempt_matches_request, grant_consumption_decision, grant_issue_decision, grant_reservation_decision, validate_receipt_binding

from agent_core.persistence.thread_store import ThreadOwnershipError, ThreadTenantMismatchError
from agent_core.persistence.database_settings import DatabaseSettings
from agent_core.observability.redaction import redact_for_persistence
from agent_core.observability.correlation import get_correlation_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _decode(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


@dataclass
class SqlAlchemyStoreProvider:
    """SQLAlchemy-backed Agent store provider.

    This provider is intentionally hidden behind AGENT_DB_BACKEND so the project
    can keep SQLite/local defaults for unit tests while production can switch to
    PostgreSQL/MySQL by config:

        AGENT_DB_BACKEND=postgres
        AGENT_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/agent_db

    SQLAlchemy/Alembic are optional dependencies; this module imports them lazily
    only when the provider is selected.
    """

    settings: DatabaseSettings
    engine: Any
    sa: Any
    tables: dict[str, Any]

    def __post_init__(self) -> None:
        self.threads = _SqlAlchemyThreadRepository(self)
        self.messages = _SqlAlchemyMessageRepository(self)
        self.traces = _SqlAlchemyTraceRepository(self)
        self.action_audits = _SqlAlchemyActionAuditRepository(self)
        self.idempotency = _SqlAlchemyIdempotencyRepository(self)
        self.locks = _SqlAlchemyActionLockRepository(self)
        self.outbox = _SqlAlchemyOutboxRepository(self)
        self.action_runs = _SqlAlchemyActionRunRepository(self)
        self.transactions = _SqlAlchemyTransactionLifecycleRepository(self)

    def close(self) -> None:
        dispose = getattr(self.engine, "dispose", None)
        if callable(dispose):
            dispose()

    def conn(self):
        return self.engine.begin()


def build_sqlalchemy_store_provider(settings: DatabaseSettings) -> SqlAlchemyStoreProvider:
    try:
        import sqlalchemy as sa  # type: ignore
    except Exception as e:  # pragma: no cover - exercised only when optional dep missing
        raise RuntimeError(
            "AGENT_DB_BACKEND requires SQLAlchemy. Install optional database dependencies, "
            "for example: pip install 'sqlalchemy>=2.0' psycopg[binary] pymysql alembic"
        ) from e

    database_url = _normalize_database_url(settings.database_url)
    engine = sa.create_engine(database_url, pool_pre_ping=True, future=True)
    try:
        metadata = sa.MetaData()
        tables = _define_tables(sa, metadata)
        if settings.create_schema:
            metadata.create_all(engine)
            _ensure_message_presentation_columns(engine, sa)
            _ensure_transaction_protocol_columns(engine, sa)
        else:
            _assert_schema_present(engine, sa, tables)
        return SqlAlchemyStoreProvider(
            settings=settings, engine=engine, sa=sa, tables=tables
        )
    except Exception:
        # Provider construction owns the engine until a StoreProvider is
        # returned.  Dispose it on schema/migration failures so a failed
        # startup cannot leak pooled SQLite/PostgreSQL connections.
        engine.dispose()
        raise


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    return database_url


def _define_tables(sa: Any, metadata: Any) -> dict[str, Any]:
    return {
        "threads": sa.Table(
            "agent_threads", metadata,
            sa.Column("thread_id", sa.String(255), primary_key=True),
            sa.Column("user_id", sa.String(255), nullable=True),
            sa.Column("tenant_id", sa.String(255), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.String(64)),
            sa.Column("updated_at", sa.String(64)),
            sa.Index("idx_agent_threads_owner", "tenant_id", "user_id", "updated_at"),
        ),
        "messages": sa.Table(
            "agent_messages", metadata,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("thread_id", sa.String(255), index=True),
            sa.Column("role", sa.String(64)),
            sa.Column("content", sa.Text()),
            sa.Column("message_type", sa.String(64), nullable=True),
            sa.Column("presentation_json", sa.Text(), nullable=True),
            sa.Column("interaction_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.String(64)),
            sa.Index("idx_agent_messages_thread_id", "thread_id", "id"),
        ),
        "trace_logs": sa.Table(
            "agent_trace_logs", metadata,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("trace_id", sa.String(64), index=True),
            sa.Column("correlation_id", sa.String(64), index=True, nullable=True),
            sa.Column("thread_id", sa.String(255), index=True),
            sa.Column("user_id", sa.String(255), nullable=True),
            sa.Column("event_type", sa.String(128)),
            sa.Column("node", sa.String(128), nullable=True),
            sa.Column("input_json", sa.Text(), nullable=True),
            sa.Column("output_json", sa.Text(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.String(64)),
        ),
        "action_audit_logs": sa.Table(
            "agent_action_audit_logs", metadata,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("thread_id", sa.String(255)),
            sa.Column("user_id", sa.String(255), nullable=True),
            sa.Column("role", sa.String(64), nullable=True),
            sa.Column("action_name", sa.String(128)),
            sa.Column("idempotency_key", sa.String(255), nullable=True),
            sa.Column("status", sa.String(64)),
            sa.Column("input_json", sa.Text()),
            sa.Column("output_json", sa.Text()),
            sa.Column("created_at", sa.String(64)),
        ),
        "idempotency_records": sa.Table(
            "agent_idempotency_records", metadata,
            sa.Column("idempotency_key", sa.String(255), primary_key=True),
            sa.Column("action_name", sa.String(128)),
            sa.Column("request_hash", sa.String(128)),
            sa.Column("status", sa.String(64)),
            sa.Column("result_json", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.String(64)),
            sa.Column("updated_at", sa.String(64)),
        ),
        "action_lock_tokens": sa.Table(
            "agent_action_lock_tokens", metadata,
            sa.Column("token", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("lock_key", sa.String(255), nullable=False, index=True),
            sa.Column("issued_at", sa.String(64), nullable=False),
        ),
        "action_locks": sa.Table(
            "agent_action_locks", metadata,
            sa.Column("lock_key", sa.String(255), primary_key=True),
            sa.Column("owner", sa.String(255), nullable=False),
            sa.Column("fencing_token", sa.Integer(), nullable=False),
            sa.Column("expires_at", sa.String(64), nullable=False),
            sa.Column("created_at", sa.String(64), nullable=False),
            sa.Column("renewed_at", sa.String(64), nullable=False),
            sa.Index("idx_agent_action_locks_expires_at", "expires_at"),
        ),
        "outbox_events": sa.Table(
            "agent_outbox_events", metadata,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("event_type", sa.String(128)),
            sa.Column("aggregate_key", sa.String(255), nullable=True),
            sa.Column("payload_json", sa.Text()),
            sa.Column("status", sa.String(64), index=True),
            sa.Column("created_at", sa.String(64), index=True),
            sa.Column("published_at", sa.String(64), nullable=True),
        ),
        "action_runs": sa.Table(
            "agent_action_runs", metadata,
            sa.Column("run_id", sa.String(64), primary_key=True),
            sa.Column("idempotency_key", sa.String(255), nullable=True),
            sa.Column("action_name", sa.String(128)),
            sa.Column("status", sa.String(64), index=True),
            sa.Column("transitions_json", sa.Text()),
            sa.Column("created_at", sa.String(64)),
            sa.Column("updated_at", sa.String(64)),
        ),
        "transaction_grants": sa.Table(
            "agent_transaction_grants", metadata,
            sa.Column("grant_id", sa.String(128), primary_key=True),
            sa.Column("tenant_id", sa.String(255), index=True),
            sa.Column("user_id", sa.String(255), index=True),
            sa.Column("thread_id", sa.String(255), index=True),
            sa.Column("draft_id", sa.String(255), index=True),
            sa.Column("draft_revision", sa.Integer()),
            sa.Column("command_digest", sa.String(128)),
            sa.Column("confirmation_id", sa.String(255)),
            sa.Column("client_request_id", sa.String(255)),
            sa.Column("actor_id", sa.String(255)),
            sa.Column("actor_role", sa.String(128), nullable=True),
            sa.Column("state", sa.String(64), index=True),
            sa.Column("issued_at", sa.String(64)),
            sa.Column("reserved_at", sa.String(64), nullable=True),
            sa.Column("consumed_at", sa.String(64), nullable=True),
            sa.Column("revoked_at", sa.String(64), nullable=True),
            sa.Column("expires_at", sa.String(64), nullable=True),
            sa.Column("attempt_id", sa.String(128), nullable=True),
            sa.Column("receipt_handle", sa.String(255), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("correlation_id", sa.String(64), nullable=True, index=True),
            sa.UniqueConstraint("tenant_id", "user_id", "thread_id", "draft_id", "draft_revision", "command_digest", "confirmation_id", name="uq_agent_transaction_grant_snapshot"),
        ),
        "transaction_attempts": sa.Table(
            "agent_transaction_attempts", metadata,
            sa.Column("attempt_id", sa.String(128), primary_key=True),
            sa.Column("tenant_id", sa.String(255), index=True),
            sa.Column("user_id", sa.String(255), index=True),
            sa.Column("thread_id", sa.String(255), index=True),
            sa.Column("draft_id", sa.String(255), index=True),
            sa.Column("draft_revision", sa.Integer()),
            sa.Column("grant_id", sa.String(128)),
            sa.Column("action_id", sa.String(128)),
            sa.Column("command_digest", sa.String(128)),
            sa.Column("idempotency_key", sa.String(255), unique=True),
            sa.Column("canonical_payload_json", sa.Text()),
            sa.Column("business_command_envelope_json", sa.Text(), nullable=True),
            sa.Column("state", sa.String(64), index=True),
            sa.Column("business_result_json", sa.Text(), nullable=True),
            sa.Column("receipt_handle", sa.String(255), nullable=True),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.String(64)),
            sa.Column("updated_at", sa.String(64)),
            sa.Column("reconciled_at", sa.String(64), nullable=True),
            sa.Column("correlation_id", sa.String(64), nullable=True, index=True),
        ),
        "transaction_drafts": sa.Table(
            "agent_transaction_drafts", metadata,
            sa.Column("draft_id", sa.String(255), primary_key=True),
            sa.Column("tenant_id", sa.String(255), index=True),
            sa.Column("user_id", sa.String(255), index=True),
            sa.Column("thread_id", sa.String(255), index=True),
            sa.Column("draft_revision", sa.Integer()),
            sa.Column("draft_state", sa.String(64), index=True),
            sa.Column("action_id", sa.String(128)),
            sa.Column("command_digest", sa.String(128)),
            sa.Column("command_envelope_json", sa.Text(), nullable=True),
            sa.Column("projection_json", sa.Text(), nullable=True),
            sa.Column("active_grant_id", sa.String(128), nullable=True),
            sa.Column("current_attempt_id", sa.String(128), nullable=True),
            sa.Column("created_at", sa.String(64)),
            sa.Column("updated_at", sa.String(64)),
            sa.Column("correlation_id", sa.String(64), nullable=True, index=True),
            sa.Index("idx_agent_transaction_drafts_scope", "tenant_id", "user_id", "thread_id", "draft_state"),
        ),
        "transaction_receipts": sa.Table(
            "agent_transaction_receipts", metadata,
            sa.Column("receipt_id", sa.String(128), primary_key=True),
            sa.Column("tenant_id", sa.String(255), index=True),
            sa.Column("user_id", sa.String(255), index=True),
            sa.Column("thread_id", sa.String(255), index=True),
            sa.Column("draft_id", sa.String(255), index=True),
            sa.Column("attempt_id", sa.String(128), unique=True),
            sa.Column("receipt_handle", sa.String(255), nullable=True),
            sa.Column("receipt_state", sa.String(64), index=True),
            sa.Column("business_result_json", sa.Text(), nullable=True),
            sa.Column("business_resource_id", sa.String(255), nullable=True),
            sa.Column("created_at", sa.String(64)),
            sa.Column("correlation_id", sa.String(64), nullable=True, index=True),
        ),
    }


def _ensure_message_presentation_columns(engine: Any, sa: Any) -> None:
    """Ensure current message-envelope columns exist.

    SQLAlchemy ``create_all`` deliberately does not alter existing tables.  The
    interaction/presentation envelope is a backwards-compatible nullable
    extension, so we can safely add it for supported SQL backends at startup.
    Deployment accounts without ALTER permission receive a clear failure
    instead of silently losing product presentation state on reload.
    """
    inspector = sa.inspect(engine)
    if not inspector.has_table("agent_messages"):
        return
    existing = {str(item.get("name")) for item in inspector.get_columns("agent_messages")}
    missing = [name for name in ("message_type", "presentation_json", "interaction_json") if name not in existing]
    if not missing:
        return
    with engine.begin() as conn:
        for name in missing:
            conn.execute(sa.text(f"ALTER TABLE agent_messages ADD COLUMN {name} TEXT"))


def _ensure_transaction_protocol_columns(engine: Any, sa: Any) -> None:
    """Additive migration for deployments created before durable command envelopes."""
    inspector = sa.inspect(engine)
    table_name = "agent_transaction_attempts"
    if not inspector.has_table(table_name):
        return
    existing = {str(item.get("name")) for item in inspector.get_columns(table_name)}
    if "business_command_envelope_json" not in existing:
        with engine.begin() as conn:
            conn.execute(sa.text(f"ALTER TABLE {table_name} ADD COLUMN business_command_envelope_json TEXT"))


def _assert_schema_present(engine: Any, sa: Any, tables: dict[str, Any]) -> None:
    inspector = sa.inspect(engine)
    missing = [
        str(table.name)
        for table in tables.values()
        if not inspector.has_table(str(table.name))
    ]
    if missing:
        raise RuntimeError(
            "Agent database schema is missing tables: "
            + ", ".join(sorted(missing))
            + ". Run Alembic migrations or set AGENT_DB_CREATE_SCHEMA=true only for local development."
        )


def _row(row: Any) -> dict | None:
    return dict(row._mapping) if row is not None else None


class _Repo:
    def __init__(self, p: SqlAlchemyStoreProvider):
        self.p = p
        self.sa = p.sa
        self.t = p.tables


class _SqlAlchemyThreadRepository(_Repo):
    def _tenant_matches(self, owner_tenant_id: str | None, actor_tenant_id: str | None) -> bool:
        return not owner_tenant_id or owner_tenant_id == actor_tenant_id

    def claim_or_validate_thread(self, thread_id: str, user_id: str, tenant_id: str | None = None) -> dict:
        table = self.t["threads"]
        now = _now()
        with self.p.conn() as conn:
            existing = conn.execute(self.sa.select(table).where(table.c.thread_id == thread_id)).first()
            if existing:
                row = _row(existing) or {}
                owner = row.get("user_id")
                owner_tenant = row.get("tenant_id")
                if owner and owner != user_id:
                    raise ThreadOwnershipError(thread_id, owner, user_id)
                if not self._tenant_matches(owner_tenant, tenant_id):
                    raise ThreadTenantMismatchError(thread_id, owner_tenant, tenant_id)
                conn.execute(
                    table.update().where(table.c.thread_id == thread_id).values(
                        user_id=owner or user_id,
                        tenant_id=owner_tenant or tenant_id,
                        updated_at=now,
                    )
                )
                refreshed = conn.execute(self.sa.select(table).where(table.c.thread_id == thread_id)).first()
                return _row(refreshed) or row
            conn.execute(table.insert().values(thread_id=thread_id, user_id=user_id, tenant_id=tenant_id, summary=None, created_at=now, updated_at=now))
            return {"thread_id": thread_id, "user_id": user_id, "tenant_id": tenant_id, "summary": None, "created_at": now, "updated_at": now}

    def upsert_thread(self, thread_id: str, user_id: str | None = None, summary: str | None = None, tenant_id: str | None = None) -> None:
        table = self.t["threads"]
        now = _now()
        with self.p.conn() as conn:
            row = _row(conn.execute(self.sa.select(table).where(table.c.thread_id == thread_id)).first())
            if row:
                if user_id and row.get("user_id") and row.get("user_id") != user_id:
                    raise ThreadOwnershipError(thread_id, row.get("user_id"), user_id)
                if tenant_id and row.get("tenant_id") and row.get("tenant_id") != tenant_id:
                    raise ThreadTenantMismatchError(thread_id, row.get("tenant_id"), tenant_id)
                conn.execute(table.update().where(table.c.thread_id == thread_id).values(
                    user_id=row.get("user_id") or user_id,
                    tenant_id=row.get("tenant_id") or tenant_id,
                    summary=summary if summary is not None else row.get("summary"),
                    updated_at=now,
                ))
            else:
                conn.execute(table.insert().values(thread_id=thread_id, user_id=user_id, tenant_id=tenant_id, summary=summary, created_at=now, updated_at=now))

    def get_thread(self, thread_id: str) -> dict | None:
        table = self.t["threads"]
        with self.p.conn() as conn:
            return _row(conn.execute(self.sa.select(table).where(table.c.thread_id == thread_id)).first())

    def assert_thread_owner(self, thread_id: str, user_id: str, tenant_id: str | None = None) -> dict:
        row = self.get_thread(thread_id)
        if not row:
            raise KeyError(thread_id)
        if row.get("user_id") and row.get("user_id") != user_id:
            raise ThreadOwnershipError(thread_id, row.get("user_id"), user_id)
        if not self._tenant_matches(row.get("tenant_id"), tenant_id):
            raise ThreadTenantMismatchError(thread_id, row.get("tenant_id"), tenant_id)
        return row

    def list_threads(self, user_id: str | None = None, limit: int = 100, tenant_id: str | None = None) -> list[dict]:
        table = self.t["threads"]
        stmt = self.sa.select(table)
        if user_id:
            stmt = stmt.where(table.c.user_id == user_id)
        if tenant_id:
            stmt = stmt.where((table.c.tenant_id == tenant_id) | (table.c.tenant_id.is_(None)))
        stmt = stmt.order_by(table.c.updated_at.desc()).limit(limit)
        with self.p.conn() as conn:
            return [_row(row) or {} for row in conn.execute(stmt).fetchall()]


class _SqlAlchemyMessageRepository(_Repo):
    def add_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        *,
        message_type: str | None = None,
        presentation: list[dict[str, Any]] | None = None,
        interaction: dict[str, Any] | None = None,
    ) -> None:
        table = self.t["messages"]
        with self.p.conn() as conn:
            conn.execute(table.insert().values(
                thread_id=thread_id,
                role=role,
                content=content,
                message_type=message_type,
                presentation_json=_json(presentation) if presentation is not None else None,
                interaction_json=_json(interaction) if interaction is not None else None,
                created_at=_now(),
            ))

    def list_messages(self, thread_id: str, limit: int = 50) -> list[dict]:
        table = self.t["messages"]
        stmt = self.sa.select(table).where(table.c.thread_id == thread_id).order_by(table.c.id.desc()).limit(limit)
        with self.p.conn() as conn:
            rows = list(reversed([_row(row) or {} for row in conn.execute(stmt).fetchall()]))
        for row in rows:
            row["presentation"] = _decode(row.pop("presentation_json", None)) or []
            row["interaction"] = _decode(row.pop("interaction_json", None))
            row["message_type"] = row.get("message_type") or ("chat" if row.get("role") == "user" else "answer")
        return rows


class _SqlAlchemyTraceRepository(_Repo):
    def log_event(self, thread_id: str, user_id: str | None, event_type: str, node: str | None = None, input_data: Any | None = None, output_data: Any | None = None, latency_ms: int | None = None, trace_id: str | None = None, correlation_id: str | None = None) -> str:
        table = self.t["trace_logs"]
        trace_id = trace_id or str(uuid.uuid4())
        correlation_id = correlation_id or get_correlation_id()
        with self.p.conn() as conn:
            conn.execute(table.insert().values(
                trace_id=trace_id, correlation_id=correlation_id, thread_id=thread_id, user_id=user_id, event_type=event_type, node=node,
                input_json=_json(redact_for_persistence(input_data)) if input_data is not None else None,
                output_json=_json(redact_for_persistence(output_data)) if output_data is not None else None,
                latency_ms=latency_ms, created_at=_now(),
            ))
        return trace_id

    def list_recent(self, limit: int = 100) -> list[dict]:
        table = self.t["trace_logs"]
        with self.p.conn() as conn:
            return [_row(row) or {} for row in conn.execute(self.sa.select(table).order_by(table.c.id.desc()).limit(limit)).fetchall()]

    def list_recent_by_event_type(self, event_type: str, limit: int = 100) -> list[dict]:
        table = self.t["trace_logs"]
        stmt = self.sa.select(table).where(table.c.event_type == str(event_type)).order_by(table.c.id.desc()).limit(max(1, int(limit)))
        with self.p.conn() as conn:
            return [_row(row) or {} for row in conn.execute(stmt).fetchall()]

    def list_by_thread(self, thread_id: str, limit: int = 200) -> list[dict]:
        table = self.t["trace_logs"]
        with self.p.conn() as conn:
            return [_row(row) or {} for row in conn.execute(self.sa.select(table).where(table.c.thread_id == thread_id).order_by(table.c.id.asc()).limit(limit)).fetchall()]

    def list_by_correlation(self, correlation_id: str, limit: int = 500) -> list[dict]:
        table = self.t["trace_logs"]
        with self.p.conn() as conn:
            stmt = self.sa.select(table).where(table.c.correlation_id == correlation_id).order_by(table.c.id.asc()).limit(limit)
            return [_row(row) or {} for row in conn.execute(stmt).fetchall()]

    def get_trace(self, trace_log_id: int) -> dict | None:
        table = self.t["trace_logs"]
        with self.p.conn() as conn:
            return _row(conn.execute(self.sa.select(table).where(table.c.id == trace_log_id)).first())

    def prune_older_than(self, cutoff_iso: str) -> int:
        table = self.t["trace_logs"]
        with self.p.conn() as conn:
            result = conn.execute(table.delete().where(table.c.created_at < cutoff_iso))
            return int(result.rowcount or 0)


class _SqlAlchemyActionAuditRepository(_Repo):
    def log_action(self, *, thread_id: str | None, user_id: str | None, role: str | None, action_name: str, idempotency_key: str | None, status: str, input_data: Any, output_data: Any) -> None:
        table = self.t["action_audit_logs"]
        with self.p.conn() as conn:
            conn.execute(table.insert().values(
                thread_id=thread_id, user_id=user_id, role=role, action_name=action_name,
                idempotency_key=idempotency_key, status=status, input_json=_json(redact_for_persistence(input_data)), output_json=_json(redact_for_persistence(output_data)), created_at=_now(),
            ))

    def list_recent(self, limit: int = 100) -> list[dict]:
        table = self.t["action_audit_logs"]
        with self.p.conn() as conn:
            return [_row(row) or {} for row in conn.execute(self.sa.select(table).order_by(table.c.id.desc()).limit(limit)).fetchall()]

    def prune_older_than(self, cutoff_iso: str) -> int:
        table = self.t["action_audit_logs"]
        with self.p.conn() as conn:
            result = conn.execute(table.delete().where(table.c.created_at < cutoff_iso))
            return int(result.rowcount or 0)


class _SqlAlchemyIdempotencyRepository(_Repo):
    def get(self, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        table = self.t["idempotency_records"]
        with self.p.conn() as conn:
            row = _row(conn.execute(self.sa.select(table).where(table.c.idempotency_key == key)).first())
        if row:
            row["result"] = _decode(row.get("result_json"))
        return row

    def start(self, *, key: str, action_name: str, request_hash: str) -> dict[str, Any]:
        existing = self.get(key)
        if existing:
            return {"created": False, "record": existing}
        table = self.t["idempotency_records"]
        now = _now()
        with self.p.conn() as conn:
            conn.execute(table.insert().values(idempotency_key=key, action_name=action_name, request_hash=request_hash, status="running", result_json=None, error=None, created_at=now, updated_at=now))
        return {"created": True, "record": self.get(key)}

    def finish(self, *, key: str, status: str, result: Any, error: str | None = None) -> None:
        table = self.t["idempotency_records"]
        with self.p.conn() as conn:
            conn.execute(table.update().where(table.c.idempotency_key == key).values(status=status, result_json=_json(result), error=error, updated_at=_now()))


class _SqlAlchemyActionLockRepository(_Repo):
    def acquire(self, lock_key: str, *, owner: str | None = None, ttl_seconds: int = 120) -> dict[str, Any]:
        lock_table = self.t["action_locks"]
        token_table = self.t["action_lock_tokens"]
        owner = owner or str(uuid.uuid4())
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat()
        try:
            with self.p.conn() as conn:
                conn.execute(lock_table.delete().where(lock_table.c.lock_key == lock_key).where(lock_table.c.expires_at <= now))
                token_result = conn.execute(token_table.insert().values(lock_key=lock_key, issued_at=now))
                token = int(token_result.inserted_primary_key[0])
                conn.execute(
                    lock_table.insert().values(
                        lock_key=lock_key, owner=owner, fencing_token=token,
                        expires_at=expires, created_at=now, renewed_at=now,
                    )
                )
        except self.sa.exc.IntegrityError:
            with self.p.conn() as conn:
                row = _row(conn.execute(self.sa.select(lock_table).where(lock_table.c.lock_key == lock_key)).first())
            return {
                "acquired": False,
                "owner": (row or {}).get("owner"),
                "fencing_token": int((row or {}).get("fencing_token") or 0),
                "expires_at": (row or {}).get("expires_at"),
            }
        return {"acquired": True, "owner": owner, "fencing_token": token, "expires_at": expires}

    def renew(self, lock_key: str, *, owner: str, fencing_token: int, ttl_seconds: int = 120) -> dict[str, Any]:
        table = self.t["action_locks"]
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat()
        with self.p.conn() as conn:
            result = conn.execute(
                table.update()
                .where(table.c.lock_key == lock_key)
                .where(table.c.owner == owner)
                .where(table.c.fencing_token == int(fencing_token))
                .where(table.c.expires_at > now)
                .values(expires_at=expires, renewed_at=now)
            )
        return {"renewed": int(result.rowcount or 0) == 1, "expires_at": expires if result.rowcount else None}

    def validate(self, lock_key: str, *, owner: str, fencing_token: int) -> bool:
        table = self.t["action_locks"]
        now = datetime.now(timezone.utc).isoformat()
        stmt = (
            self.sa.select(table.c.lock_key)
            .where(table.c.lock_key == lock_key)
            .where(table.c.owner == owner)
            .where(table.c.fencing_token == int(fencing_token))
            .where(table.c.expires_at > now)
        )
        with self.p.conn() as conn:
            return conn.execute(stmt).first() is not None

    def release(self, lock_key: str, owner: str | None = None, fencing_token: int | None = None) -> None:
        table = self.t["action_locks"]
        stmt = table.delete().where(table.c.lock_key == lock_key)
        if owner is not None:
            stmt = stmt.where(table.c.owner == owner)
        if fencing_token is not None:
            stmt = stmt.where(table.c.fencing_token == int(fencing_token))
        with self.p.conn() as conn:
            conn.execute(stmt)


class _SqlAlchemyOutboxRepository(_Repo):
    def enqueue(self, *, event_type: str, aggregate_key: str | None, payload: Any) -> int:
        table = self.t["outbox_events"]
        with self.p.conn() as conn:
            result = conn.execute(table.insert().values(event_type=event_type, aggregate_key=aggregate_key, payload_json=_json(payload), status="pending", created_at=_now(), published_at=None))
            return int(result.inserted_primary_key[0])

    def list_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        table = self.t["outbox_events"]
        stmt = self.sa.select(table).where(table.c.status == "pending").order_by(table.c.id.asc()).limit(limit)
        with self.p.conn() as conn:
            rows = [_row(row) or {} for row in conn.execute(stmt).fetchall()]
        for row in rows:
            row["payload"] = _decode(row.get("payload_json"))
        return rows

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        table = self.t["outbox_events"]
        with self.p.conn() as conn:
            rows = [_row(row) or {} for row in conn.execute(self.sa.select(table).order_by(table.c.id.desc()).limit(limit)).fetchall()]
        for row in rows:
            row["payload"] = _decode(row.get("payload_json"))
        return rows


class _SqlAlchemyActionRunRepository(_Repo):
    def start(self, *, idempotency_key: str | None, action_name: str) -> str:
        table = self.t["action_runs"]
        run_id = str(uuid.uuid4())
        now = _now()
        transitions = [{"status": "created", "at": now}, {"status": "running", "at": now}]
        with self.p.conn() as conn:
            conn.execute(table.insert().values(run_id=run_id, idempotency_key=idempotency_key, action_name=action_name, status="running", transitions_json=_json(transitions), created_at=now, updated_at=now))
        return run_id

    def transition(self, run_id: str, status: str, *, detail: Any = None) -> None:
        table = self.t["action_runs"]
        row = self.get(run_id)
        if not row:
            return
        transitions = row.get("transitions") or []
        transitions.append({"status": status, "at": _now(), "detail": detail})
        with self.p.conn() as conn:
            conn.execute(table.update().where(table.c.run_id == run_id).values(status=status, transitions_json=_json(transitions), updated_at=_now()))

    def get(self, run_id: str) -> dict[str, Any] | None:
        table = self.t["action_runs"]
        with self.p.conn() as conn:
            row = _row(conn.execute(self.sa.select(table).where(table.c.run_id == run_id)).first())
        if row:
            row["transitions"] = _decode(row.get("transitions_json")) or []
        return row

    def find_by_idempotency_key(self, idempotency_key: str | None) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        table = self.t["action_runs"]
        stmt = self.sa.select(table).where(table.c.idempotency_key == idempotency_key).order_by(table.c.created_at.desc()).limit(1)
        with self.p.conn() as conn:
            row = _row(conn.execute(stmt).first())
        if row:
            row["transitions"] = _decode(row.get("transitions_json")) or []
        return row

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        table = self.t["action_runs"]
        with self.p.conn() as conn:
            rows = [_row(row) or {} for row in conn.execute(self.sa.select(table).order_by(table.c.created_at.desc()).limit(limit)).fetchall()]
        for row in rows:
            row["transitions"] = _decode(row.get("transitions_json")) or []
        return rows



class _SqlAlchemyTransactionLifecycleRepository(_Repo):
    """SQLAlchemy implementation of the same Agent transaction protocol store."""

    @staticmethod
    def _decode_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        item["business_result"] = _decode(item.get("business_result_json"))
        item["canonical_payload"] = _decode(item.get("canonical_payload_json"))
        item["business_command_envelope"] = _decode(item.get("business_command_envelope_json"))
        item["command_envelope"] = _decode(item.get("command_envelope_json"))
        item["projection"] = _decode(item.get("projection_json"))
        return item

    def issue_grant(self, **kwargs: Any) -> dict[str, Any]:
        table = self.t["transaction_grants"]
        drafts = self.t["transaction_drafts"]
        kwargs.setdefault("correlation_id", get_correlation_id())
        condition = self.sa.and_(
            table.c.tenant_id == kwargs["tenant_id"], table.c.user_id == kwargs["user_id"],
            table.c.thread_id == kwargs["thread_id"], table.c.draft_id == kwargs["draft_id"],
            table.c.draft_revision == int(kwargs["draft_revision"]), table.c.command_digest == kwargs["command_digest"],
            table.c.confirmation_id == kwargs["confirmation_id"],
        )
        with self.p.conn() as conn:
            draft_row = _row(conn.execute(self.sa.select(drafts).where(self.sa.and_(
                drafts.c.draft_id == kwargs["draft_id"], drafts.c.tenant_id == kwargs["tenant_id"],
                drafts.c.user_id == kwargs["user_id"], drafts.c.thread_id == kwargs["thread_id"],
            ))).first())
            draft = self._decode_row(draft_row) if draft_row else None
            allowed, reason = grant_issue_decision(
                draft, tenant_id=kwargs["tenant_id"], user_id=kwargs["user_id"], thread_id=kwargs["thread_id"],
                draft_id=kwargs["draft_id"], draft_revision=int(kwargs["draft_revision"]),
                command_digest=kwargs["command_digest"], confirmation_id=kwargs["confirmation_id"],
            )
            if not allowed:
                raise ValueError(f"canonical Draft rejected Grant issuance: {reason}")
            row = _row(conn.execute(self.sa.select(table).where(condition)).first())
            if row:
                return self._decode_row(row) or {}
            values = {**kwargs, "state": "ISSUED", "issued_at": _now(), "reserved_at": None, "consumed_at": None, "revoked_at": None, "attempt_id": None, "receipt_handle": None, "reason": None}
            conn.execute(table.insert().values(**values))
        return self.get_grant(kwargs["grant_id"]) or {}

    def get_grant(self, grant_id: str | None) -> dict[str, Any] | None:
        if not grant_id:
            return None
        table = self.t["transaction_grants"]
        with self.p.conn() as conn:
            return self._decode_row(_row(conn.execute(self.sa.select(table).where(table.c.grant_id == grant_id)).first()))

    def list_grants_by_thread(self, *, tenant_id: str, user_id: str, thread_id: str, limit: int = 100) -> list[dict[str, Any]]:
        table = self.t["transaction_grants"]
        stmt = self.sa.select(table).where(self.sa.and_(table.c.tenant_id == tenant_id, table.c.user_id == user_id, table.c.thread_id == thread_id)).order_by(table.c.issued_at.desc()).limit(limit)
        with self.p.conn() as conn:
            return [self._decode_row(_row(row)) or {} for row in conn.execute(stmt).fetchall()]

    def reserve_grant(self, grant_id: str, *, attempt_id: str | None = None) -> dict[str, Any]:
        """Compatibility surface; authority reservation is atomic with Attempt creation."""
        return {"reserved": False, "grant": self.get_grant(grant_id) or {}, "reason": "atomic_attempt_required"}

    def reserve_grant_and_start_attempt(self, **kwargs: Any) -> dict[str, Any]:
        grants = self.t["transaction_grants"]
        attempts = self.t["transaction_attempts"]
        drafts = self.t["transaction_drafts"]
        kwargs.setdefault("correlation_id", get_correlation_id())
        now = _now()
        key = str(kwargs["idempotency_key"])
        grant_id = str(kwargs["grant_id"])
        attempt_id = str(kwargs["attempt_id"])
        try:
            with self.p.conn() as conn:
                existing = _row(conn.execute(self.sa.select(attempts).where(attempts.c.idempotency_key == key)).first())
                if existing:
                    existing_payload = self._decode_row(existing) or {}
                    grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())
                    if not existing_attempt_matches_request(
                        existing_payload, grant_id=grant_id, tenant_id=kwargs["tenant_id"], user_id=kwargs["user_id"],
                        thread_id=kwargs["thread_id"], draft_id=kwargs["draft_id"], draft_revision=int(kwargs["draft_revision"]),
                        action_id=kwargs["action_id"], command_digest=kwargs["command_digest"],
                    ):
                        return {"reserved": False, "grant": {}, "created": False, "attempt": {}}
                    return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": existing_payload}

                grant_row = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())
                grant_payload = self._decode_row(grant_row) if grant_row else None
                canonical = _row(conn.execute(self.sa.select(drafts).where(self.sa.and_(
                    drafts.c.draft_id == kwargs["draft_id"],
                    drafts.c.tenant_id == kwargs["tenant_id"],
                    drafts.c.user_id == kwargs["user_id"],
                    drafts.c.thread_id == kwargs["thread_id"],
                ))).first())
                canonical_payload = self._decode_row(canonical) if canonical else None
                reservation_ok, reservation_reason = grant_reservation_decision(
                    grant_payload, canonical_payload, tenant_id=kwargs["tenant_id"], user_id=kwargs["user_id"],
                    thread_id=kwargs["thread_id"], draft_id=kwargs["draft_id"], draft_revision=int(kwargs["draft_revision"]),
                    command_digest=kwargs["command_digest"],
                )
                if not reservation_ok:
                    if grant_payload and str(grant_payload.get("state") or "").upper() == "ISSUED" and reservation_reason.startswith("reservation_canonical_Draft_"):
                        conn.execute(
                            grants.update().where(self.sa.and_(grants.c.grant_id == grant_id, grants.c.state == "ISSUED"))
                            .values(state="REVOKED", revoked_at=now, reason="draft_missing")
                        )
                    return {"reserved": False, "grant": grant_payload or {}, "created": False, "attempt": {}}
                if canonical_payload:
                    committing_projection = dict(canonical_payload)
                    committing_projection.update({
                        "draft_state": "COMMITTING",
                        "draft_revision": int(kwargs["draft_revision"]),
                        "command_digest": kwargs["command_digest"],
                    })
                    if kwargs.get("draft_projection") is not None:
                        committing_projection["projection"] = kwargs.get("draft_projection")
                    allowed, reason = draft_persistence_update_decision(canonical_payload, committing_projection)
                    if not allowed:
                        conn.execute(
                            grants.update().where(self.sa.and_(grants.c.grant_id == grant_id, grants.c.state == "ISSUED"))
                            .values(state="REVOKED", revoked_at=now, reason="draft_update_rejected:" + reason)
                        )
                        grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())
                        return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": {}}

                conn.execute(
                    grants.update().where(self.sa.and_(
                        grants.c.grant_id == grant_id, grants.c.state == "ISSUED",
                        grants.c.expires_at.is_not(None), grants.c.expires_at <= now,
                    )).values(state="EXPIRED", revoked_at=now, reason="grant_expired")
                )
                reserved = conn.execute(
                    grants.update().where(self.sa.and_(
                        grants.c.grant_id == grant_id, grants.c.state == "ISSUED",
                        self.sa.or_(grants.c.expires_at.is_(None), grants.c.expires_at > now),
                    )).values(state="RESERVED", reserved_at=now, attempt_id=attempt_id)
                )
                grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())
                if int(reserved.rowcount or 0) != 1:
                    return {"reserved": False, "grant": self._decode_row(grant) or {}, "created": False, "attempt": {}}
                payload = kwargs["canonical_payload"]
                values = {
                    "attempt_id": attempt_id,
                    "tenant_id": kwargs["tenant_id"], "user_id": kwargs["user_id"], "thread_id": kwargs["thread_id"],
                    "draft_id": kwargs["draft_id"], "draft_revision": int(kwargs["draft_revision"]),
                    "grant_id": grant_id, "action_id": kwargs["action_id"], "command_digest": kwargs["command_digest"],
                    "idempotency_key": key, "canonical_payload_json": _json(payload),
                    "business_command_envelope_json": _json(kwargs.get("business_command_envelope")) if kwargs.get("business_command_envelope") is not None else None,
                    "state": "STARTED", "business_result_json": None, "receipt_handle": None,
                    "error_code": None, "error": None, "created_at": now, "updated_at": now,
                    "reconciled_at": None, "correlation_id": kwargs.get("correlation_id"),
                }
                conn.execute(attempts.insert().values(**values))
                projection = kwargs.get("draft_projection") or {}
                draft_values = {
                    "draft_id": kwargs["draft_id"], "tenant_id": kwargs["tenant_id"], "user_id": kwargs["user_id"],
                    "thread_id": kwargs["thread_id"], "draft_revision": int(kwargs["draft_revision"]), "draft_state": "COMMITTING",
                    "action_id": kwargs["action_id"], "command_digest": kwargs["command_digest"],
                    "command_envelope_json": _json(kwargs.get("business_command_envelope")) if kwargs.get("business_command_envelope") is not None else None,
                    "projection_json": _json(projection), "active_grant_id": grant_id, "current_attempt_id": attempt_id,
                    "created_at": now, "updated_at": now, "correlation_id": kwargs.get("correlation_id"),
                }
                existing_draft = _row(conn.execute(self.sa.select(drafts).where(drafts.c.draft_id == kwargs["draft_id"])).first())
                if existing_draft:
                    conn.execute(drafts.update().where(drafts.c.draft_id == kwargs["draft_id"]).values(**{k:v for k,v in draft_values.items() if k not in {"draft_id", "created_at"}}))
                else:
                    conn.execute(drafts.insert().values(**draft_values))
                attempt = _row(conn.execute(self.sa.select(attempts).where(attempts.c.attempt_id == attempt_id)).first())
                return {"reserved": True, "grant": self._decode_row(grant) or {}, "created": True, "attempt": self._decode_row(attempt) or {}}
        except self.sa.exc.IntegrityError:
            existing = self.get_attempt_by_idempotency_key(key) or {}
            return {"reserved": False, "grant": self.get_grant(grant_id) or {}, "created": False, "attempt": existing}

    def create_draft(self, **kwargs: Any) -> dict[str, Any]:
        table = self.t["transaction_drafts"]
        kwargs.setdefault("correlation_id", get_correlation_id())
        now = _now()
        command_envelope = kwargs.pop("command_envelope", None)
        projection = kwargs.pop("projection", None)
        values = {
            **kwargs,
            "command_envelope_json": _json(command_envelope),
            "projection_json": _json(projection),
            "created_at": now,
            "updated_at": now,
        }
        with self.p.conn() as conn:
            existing = _row(conn.execute(self.sa.select(table).where(table.c.draft_id == values["draft_id"])).first())
            if existing:
                existing_payload = self._decode_row(existing) or {}
                incoming_payload = {
                    **kwargs,
                    "command_envelope": command_envelope,
                    "projection": projection,
                }
                allowed, _reason = draft_persistence_update_decision(existing_payload, incoming_payload)
                if not allowed:
                    return existing_payload
                conn.execute(table.update().where(table.c.draft_id == values["draft_id"]).values(**{k:v for k,v in values.items() if k not in {"draft_id", "created_at"}}))
            else:
                conn.execute(table.insert().values(**values))
        return self.get_draft(values["draft_id"]) or {}

    def get_draft_for_scope(self, *, scope: TransactionScope, draft_id: str) -> dict[str, Any] | None:
        table = self.t["transaction_drafts"]
        clauses = [table.c.draft_id == draft_id, table.c.tenant_id == scope.tenant_id, table.c.user_id == scope.user_id]
        if scope.thread_id:
            clauses.append(table.c.thread_id == scope.thread_id)
        with self.p.conn() as conn:
            return self._decode_row(_row(conn.execute(self.sa.select(table).where(self.sa.and_(*clauses))).first()))

    def list_drafts_for_scope(self, *, scope: TransactionScope, states: set[str] | None = None, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]:
        table = self.t["transaction_drafts"]
        clauses = [table.c.tenant_id == scope.tenant_id, table.c.user_id == scope.user_id]
        if scope.thread_id:
            clauses.append(table.c.thread_id == scope.thread_id)
        if states:
            clauses.append(table.c.draft_state.in_(sorted({str(item).upper() for item in states})))
        if cursor:
            clauses.append(table.c.updated_at < cursor)
        stmt = self.sa.select(table).where(self.sa.and_(*clauses)).order_by(table.c.updated_at.desc()).limit(max(1, min(int(limit), 200)))
        with self.p.conn() as conn:
            return [self._decode_row(_row(row)) or {} for row in conn.execute(stmt).fetchall()]

    def validate_active_draft(self, *, scope: TransactionScope, draft_id: str, expected_revision: int | None = None, allowed_states: set[str] | None = None) -> ActiveDraftValidationResult:
        scoped = self.get_draft_for_scope(scope=scope, draft_id=draft_id)
        if scoped is None:
            raw = self.get_draft(draft_id)
            return ActiveDraftValidationResult(ActiveDraftValidationCode.SCOPE_MISMATCH if raw is not None else ActiveDraftValidationCode.NOT_FOUND, None)
        state = str(scoped.get("draft_state") or "").upper()
        if state == "EXPIRED": return ActiveDraftValidationResult(ActiveDraftValidationCode.EXPIRED, scoped)
        if state == "REVOKED": return ActiveDraftValidationResult(ActiveDraftValidationCode.REVOKED, scoped)
        if expected_revision is not None and int(scoped.get("draft_revision") or 0) != int(expected_revision):
            return ActiveDraftValidationResult(ActiveDraftValidationCode.REVISION_MISMATCH, scoped)
        if allowed_states and state not in {str(item).upper() for item in allowed_states}:
            return ActiveDraftValidationResult(ActiveDraftValidationCode.STATE_NOT_ALLOWED, scoped)
        return ActiveDraftValidationResult(ActiveDraftValidationCode.OK, scoped)

    def get_attempt_for_scope(self, *, scope: TransactionScope, attempt_id: str) -> dict[str, Any] | None:
        table = self.t["transaction_attempts"]
        clauses = [table.c.attempt_id == attempt_id, table.c.tenant_id == scope.tenant_id, table.c.user_id == scope.user_id]
        if scope.thread_id: clauses.append(table.c.thread_id == scope.thread_id)
        with self.p.conn() as conn:
            return self._decode_row(_row(conn.execute(self.sa.select(table).where(self.sa.and_(*clauses))).first()))

    def list_attempts_for_draft(self, *, scope: TransactionScope, draft_id: str) -> list[dict[str, Any]]:
        table = self.t["transaction_attempts"]
        clauses = [table.c.draft_id == draft_id, table.c.tenant_id == scope.tenant_id, table.c.user_id == scope.user_id]
        if scope.thread_id: clauses.append(table.c.thread_id == scope.thread_id)
        stmt = self.sa.select(table).where(self.sa.and_(*clauses)).order_by(table.c.created_at.desc())
        with self.p.conn() as conn:
            return [self._decode_row(_row(row)) or {} for row in conn.execute(stmt).fetchall()]

    def get_receipt_for_attempt(self, *, scope: TransactionScope, attempt_id: str) -> dict[str, Any] | None:
        table = self.t["transaction_receipts"]
        clauses = [table.c.attempt_id == attempt_id, table.c.tenant_id == scope.tenant_id, table.c.user_id == scope.user_id]
        if scope.thread_id: clauses.append(table.c.thread_id == scope.thread_id)
        with self.p.conn() as conn:
            return self._decode_row(_row(conn.execute(self.sa.select(table).where(self.sa.and_(*clauses))).first()))

    def get_latest_receipt_for_draft(self, *, scope: TransactionScope, draft_id: str) -> dict[str, Any] | None:
        table = self.t["transaction_receipts"]
        clauses = [table.c.draft_id == draft_id, table.c.tenant_id == scope.tenant_id, table.c.user_id == scope.user_id]
        if scope.thread_id: clauses.append(table.c.thread_id == scope.thread_id)
        stmt = self.sa.select(table).where(self.sa.and_(*clauses)).order_by(table.c.created_at.desc()).limit(1)
        with self.p.conn() as conn:
            return self._decode_row(_row(conn.execute(stmt).first()))

    def get_draft(self, draft_id: str | None) -> dict[str, Any] | None:
        if not draft_id:
            return None
        table = self.t["transaction_drafts"]
        with self.p.conn() as conn:
            return self._decode_row(_row(conn.execute(self.sa.select(table).where(table.c.draft_id == draft_id)).first()))

    def advance_draft(self, draft_id: str, *, draft_state: str, draft_revision: int | None = None, command_digest: str | None = None, command_envelope: dict[str, Any] | None = None, active_grant_id: str | None = None, current_attempt_id: str | None = None, projection: dict[str, Any] | None = None) -> None:
        table = self.t["transaction_drafts"]
        values: dict[str, Any] = {"draft_state": draft_state, "updated_at": _now()}
        if draft_revision is not None: values["draft_revision"] = int(draft_revision)
        if command_digest is not None: values["command_digest"] = command_digest
        if command_envelope is not None: values["command_envelope_json"] = _json(command_envelope)
        if active_grant_id is not None: values["active_grant_id"] = active_grant_id
        if current_attempt_id is not None: values["current_attempt_id"] = current_attempt_id
        if projection is not None: values["projection_json"] = _json(projection)
        with self.p.conn() as conn:
            existing = _row(conn.execute(self.sa.select(table).where(table.c.draft_id == draft_id)).first())
            if not existing:
                return
            current = self._decode_row(existing) or {}
            incoming = dict(current)
            incoming["draft_state"] = draft_state
            if draft_revision is not None: incoming["draft_revision"] = int(draft_revision)
            if command_digest is not None: incoming["command_digest"] = command_digest
            if command_envelope is not None: incoming["command_envelope"] = command_envelope
            if projection is not None: incoming["projection"] = projection
            allowed, _reason = draft_persistence_update_decision(current, incoming)
            if not allowed:
                return
            attempt_id_for_terminal = str(incoming.get("current_attempt_id") or current.get("current_attempt_id") or "")
            attempts = self.t["transaction_attempts"]
            receipts = self.t["transaction_receipts"]
            attempt_row = _row(conn.execute(self.sa.select(attempts).where(attempts.c.attempt_id == attempt_id_for_terminal)).first()) if attempt_id_for_terminal else None
            attempt_payload = self._decode_row(attempt_row) if attempt_row else None
            receipt_row = _row(conn.execute(self.sa.select(receipts).where(receipts.c.attempt_id == attempt_id_for_terminal)).first()) if attempt_id_for_terminal else None
            receipt_payload = self._decode_row(receipt_row) if receipt_row else None
            terminal_ok, _terminal_reason = draft_terminal_observation_decision(
                {**current, "current_attempt_id": attempt_id_for_terminal},
                target_state=draft_state, attempt=attempt_payload, receipt=receipt_payload,
            )
            if not terminal_ok:
                return
            conn.execute(table.update().where(table.c.draft_id == draft_id).values(**values))

    def list_drafts_by_thread(self, *, tenant_id: str, user_id: str, thread_id: str, limit: int = 100) -> list[dict[str, Any]]:
        table = self.t["transaction_drafts"]
        stmt = self.sa.select(table).where(self.sa.and_(table.c.tenant_id == tenant_id, table.c.user_id == user_id, table.c.thread_id == thread_id)).order_by(table.c.updated_at.desc()).limit(limit)
        with self.p.conn() as conn:
            return [self._decode_row(_row(row)) or {} for row in conn.execute(stmt).fetchall()]

    def record_receipt(self, **kwargs: Any) -> dict[str, Any]:
        table = self.t["transaction_receipts"]
        attempts = self.t["transaction_attempts"]
        grants = self.t["transaction_grants"]
        kwargs.setdefault("correlation_id", get_correlation_id())
        business_result = kwargs.pop("business_result", None)
        values = {
            **kwargs,
            "receipt_state": str(kwargs.get("receipt_state") or "").upper(),
            "business_result_json": _json(business_result),
            "created_at": _now(),
        }
        attempt_id = str(values.get("attempt_id") or "")
        with self.p.conn() as conn:
            attempt = _row(conn.execute(self.sa.select(attempts).where(attempts.c.attempt_id == attempt_id)).first())
            attempt_payload = self._decode_row(attempt) if attempt else None
            grant_id = str((attempt_payload or {}).get("grant_id") or "")
            grant = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first()) if grant_id else None
            grant_payload = self._decode_row(grant) if grant else None
            valid, reason = validate_receipt_binding(
                attempt=attempt_payload,
                grant=grant_payload,
                tenant_id=str(values.get("tenant_id") or ""),
                user_id=str(values.get("user_id") or ""),
                thread_id=str(values.get("thread_id") or ""),
                draft_id=str(values.get("draft_id") or ""),
                attempt_id=attempt_id,
                receipt_state=str(values.get("receipt_state") or ""),
                business_result=business_result,
            )
            if not valid:
                raise ValueError(f"transaction receipt attempt binding rejected: {reason}")
            existing = _row(conn.execute(self.sa.select(table).where(table.c.attempt_id == attempt_id)).first())
            if existing:
                return self._decode_row(existing) or {}
            receipt_id_conflict = _row(conn.execute(self.sa.select(table).where(table.c.receipt_id == values["receipt_id"])).first())
            if receipt_id_conflict:
                raise ValueError("transaction receipt id already belongs to another attempt")
            conn.execute(table.insert().values(**values))
        return self.get_receipt_by_attempt(attempt_id) or {}

    def get_receipt_by_attempt(self, attempt_id: str | None) -> dict[str, Any] | None:
        if not attempt_id: return None
        table = self.t["transaction_receipts"]
        with self.p.conn() as conn:
            return self._decode_row(_row(conn.execute(self.sa.select(table).where(table.c.attempt_id == attempt_id)).first()))

    def consume_grant(self, grant_id: str, *, attempt_id: str | None = None, receipt_handle: str | None = None) -> None:
        grants = self.t["transaction_grants"]
        attempts = self.t["transaction_attempts"]
        receipts = self.t["transaction_receipts"]
        requested_attempt = str(attempt_id or "")
        with self.p.conn() as conn:
            grant_row = _row(conn.execute(self.sa.select(grants).where(grants.c.grant_id == grant_id)).first())
            grant = self._decode_row(grant_row) if grant_row else None
            attempt_row = _row(conn.execute(self.sa.select(attempts).where(attempts.c.attempt_id == requested_attempt)).first()) if requested_attempt else None
            attempt = self._decode_row(attempt_row) if attempt_row else None
            receipt_row = _row(conn.execute(self.sa.select(receipts).where(receipts.c.attempt_id == requested_attempt)).first()) if requested_attempt else None
            receipt = self._decode_row(receipt_row) if receipt_row else None
            allowed, _reason = grant_consumption_decision(
                grant,
                attempt=attempt,
                receipt=receipt,
                attempt_id=attempt_id,
                receipt_handle=receipt_handle,
            )
            if not allowed:
                return
            if str((grant or {}).get("state") or "").upper() == "CONSUMED":
                return
            conn.execute(
                grants.update().where(self.sa.and_(
                    grants.c.grant_id == grant_id,
                    grants.c.state == "RESERVED",
                    grants.c.attempt_id == requested_attempt,
                )).values(
                    state="CONSUMED",
                    consumed_at=_now(),
                    attempt_id=requested_attempt,
                    receipt_handle=str(receipt_handle or ""),
                )
            )

    def revoke_grant(self, grant_id: str, *, reason: str) -> None:
        table = self.t["transaction_grants"]
        with self.p.conn() as conn:
            conn.execute(table.update().where(self.sa.and_(table.c.grant_id == grant_id, table.c.state.in_(["ISSUED", "RESERVED"]))).values(state="REVOKED", revoked_at=_now(), reason=reason))

    def start_attempt(self, **kwargs: Any) -> dict[str, Any]:
        table = self.t["transaction_attempts"]
        kwargs.setdefault("correlation_id", get_correlation_id())
        with self.p.conn() as conn:
            existing = _row(conn.execute(self.sa.select(table).where(table.c.idempotency_key == kwargs["idempotency_key"])).first())
            if existing:
                return {"created": False, "attempt": self._decode_row(existing) or {}}
            now = _now()
            payload = kwargs.pop("canonical_payload")
            attempt_id = kwargs["attempt_id"]
            values = {**kwargs, "canonical_payload_json": _json(payload), "state": "STARTED", "business_result_json": None, "receipt_handle": None, "error_code": None, "error": None, "created_at": now, "updated_at": now, "reconciled_at": None}
            conn.execute(table.insert().values(**values))
        return {"created": True, "attempt": self.get_attempt(attempt_id) or {}}

    def get_attempt(self, attempt_id: str | None) -> dict[str, Any] | None:
        if not attempt_id:
            return None
        table = self.t["transaction_attempts"]
        with self.p.conn() as conn:
            return self._decode_row(_row(conn.execute(self.sa.select(table).where(table.c.attempt_id == attempt_id)).first()))

    def get_attempt_by_idempotency_key(self, idempotency_key: str | None) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        table = self.t["transaction_attempts"]
        with self.p.conn() as conn:
            return self._decode_row(_row(conn.execute(self.sa.select(table).where(table.c.idempotency_key == idempotency_key)).first()))

    def transition_attempt(self, attempt_id: str, *, state: str, business_result: dict[str, Any] | None = None, receipt_handle: str | None = None, error_code: str | None = None, error: str | None = None, reconciled: bool = False) -> None:
        table = self.t["transaction_attempts"]
        receipts = self.t["transaction_receipts"]
        values: dict[str, Any] = {"state": state, "updated_at": _now(), "error_code": error_code, "error": error}
        if business_result is not None:
            values["business_result_json"] = _json(business_result)
        if receipt_handle is not None:
            values["receipt_handle"] = receipt_handle
        if reconciled:
            values["reconciled_at"] = _now()
        with self.p.conn() as conn:
            existing = _row(conn.execute(self.sa.select(table).where(table.c.attempt_id == attempt_id)).first())
            if not existing:
                raise ValueError("transaction attempt missing")
            current = self._decode_row(existing) or {}
            receipt = _row(conn.execute(self.sa.select(receipts).where(receipts.c.attempt_id == attempt_id)).first())
            receipt_payload = self._decode_row(receipt) if receipt else None
            allowed, _reason = attempt_persistence_update_decision(
                current,
                target_state=state,
                business_result=business_result,
                receipt_handle=receipt_handle,
                receipt=receipt_payload,
            )
            if not allowed:
                return
            conn.execute(table.update().where(table.c.attempt_id == attempt_id).values(**values))

    def list_reconcilable_attempts(self, *, scope: TransactionScope | None = None, tenant_id: str | None = None, user_id: str | None = None, thread_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if scope is not None:
            tenant_id, user_id, thread_id = scope.tenant_id, scope.user_id, scope.thread_id
        table = self.t["transaction_attempts"]
        clauses = [table.c.state.in_(["STARTED", "SUBMISSION_UNKNOWN"])]
        if tenant_id: clauses.append(table.c.tenant_id == tenant_id)
        if user_id: clauses.append(table.c.user_id == user_id)
        if thread_id: clauses.append(table.c.thread_id == thread_id)
        stmt = self.sa.select(table).where(self.sa.and_(*clauses)).order_by(table.c.updated_at.asc()).limit(max(1, min(int(limit), 200)))
        with self.p.conn() as conn:
            return [self._decode_row(_row(row)) or {} for row in conn.execute(stmt).fetchall()]

    def list_by_thread(self, *, tenant_id: str, user_id: str, thread_id: str, limit: int = 100) -> list[dict[str, Any]]:
        table = self.t["transaction_attempts"]
        stmt = self.sa.select(table).where(self.sa.and_(table.c.tenant_id == tenant_id, table.c.user_id == user_id, table.c.thread_id == thread_id)).order_by(table.c.created_at.desc()).limit(limit)
        with self.p.conn() as conn:
            return [self._decode_row(_row(row)) or {} for row in conn.execute(stmt).fetchall()]
