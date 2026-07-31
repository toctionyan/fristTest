from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_core.persistence.sqlite_base import SQLiteBase
from agent_core.kernel.profile import RuntimeProfile, get_runtime_profile


class ThreadOwnershipError(Exception):
    def __init__(self, thread_id: str, owner_user_id: str | None, actor_user_id: str | None):
        super().__init__(f"thread {thread_id!r} belongs to {owner_user_id!r}, not {actor_user_id!r}")
        self.thread_id = thread_id
        self.owner_user_id = owner_user_id
        self.actor_user_id = actor_user_id


class ThreadTenantMismatchError(Exception):
    def __init__(self, thread_id: str, owner_tenant_id: str | None, actor_tenant_id: str | None):
        super().__init__(f"thread {thread_id!r} belongs to tenant {owner_tenant_id!r}, not {actor_tenant_id!r}")
        self.thread_id = thread_id
        self.owner_tenant_id = owner_tenant_id
        self.actor_tenant_id = actor_tenant_id


class UnboundThreadTenantError(Exception):
    """Raised when an existing thread lacks a tenant binding in a protected profile."""

    def __init__(self, thread_id: str):
        super().__init__(f"thread {thread_id!r} has no tenant binding and requires administrator migration")
        self.thread_id = thread_id


def _protected_profile() -> bool:
    return get_runtime_profile(strict=False) in {RuntimeProfile.PREPROD, RuntimeProfile.PRODUCTION}


class ThreadStore(SQLiteBase):
    def __init__(self, db_path: Path):
        super().__init__(db_path)

    def init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS threads (
                thread_id TEXT PRIMARY KEY,
                user_id TEXT,
                tenant_id TEXT,
                summary TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        self._ensure_column("threads", "tenant_id", "TEXT")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        cols = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _unbound_tenant_is_blocked(self, owner_tenant_id: str | None, actor_tenant_id: str | None) -> bool:
        return bool(_protected_profile() and actor_tenant_id and not owner_tenant_id)

    def _tenant_matches(self, owner_tenant_id: str | None, actor_tenant_id: str | None) -> bool:
        if not owner_tenant_id:
            return not self._unbound_tenant_is_blocked(owner_tenant_id, actor_tenant_id)
        return bool(actor_tenant_id and owner_tenant_id == actor_tenant_id)

    def _validate_existing(self, thread_id: str, row: dict, user_id: str, tenant_id: str | None) -> None:
        owner = row.get("user_id")
        owner_tenant = row.get("tenant_id")
        if owner and owner != user_id:
            raise ThreadOwnershipError(thread_id, owner, user_id)
        if self._unbound_tenant_is_blocked(owner_tenant, tenant_id):
            raise UnboundThreadTenantError(thread_id)
        if not self._tenant_matches(owner_tenant, tenant_id):
            raise ThreadTenantMismatchError(thread_id, owner_tenant, tenant_id)

    def claim_or_validate_thread(self, thread_id: str, user_id: str, tenant_id: str | None = None) -> dict:
        """Create a new immutable binding or validate an existing one.

        A tenant-bound request for an unbound existing thread fails closed in a
        protected profile so an ordinary caller can never decide ownership.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            existing = self.conn.execute("SELECT * FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
            if existing:
                row = dict(existing)
                self._validate_existing(thread_id, row, user_id, tenant_id)
                self.conn.execute(
                    "UPDATE threads SET user_id=COALESCE(user_id, ?), tenant_id=COALESCE(tenant_id, ?), updated_at=? WHERE thread_id=?",
                    (user_id, tenant_id, now, thread_id),
                )
                self.conn.commit()
                refreshed = self.conn.execute("SELECT * FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
                return dict(refreshed) if refreshed else row
            self.conn.execute(
                "INSERT INTO threads(thread_id, user_id, tenant_id, summary, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (thread_id, user_id, tenant_id, None, now, now),
            )
            self.conn.commit()
            refreshed = self.conn.execute("SELECT * FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
            return dict(refreshed) if refreshed else {"thread_id": thread_id, "user_id": user_id, "tenant_id": tenant_id}

    def upsert_thread(self, thread_id: str, user_id: str | None = None, summary: str | None = None, tenant_id: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.query_one("SELECT * FROM threads WHERE thread_id=?", (thread_id,))
        if existing:
            if user_id:
                self._validate_existing(thread_id, existing, user_id, tenant_id)
            self.execute(
                "UPDATE threads SET user_id=COALESCE(user_id, ?), tenant_id=COALESCE(tenant_id, ?), summary=COALESCE(?, summary), updated_at=? WHERE thread_id=?",
                (user_id, tenant_id, summary, now, thread_id),
            )
        else:
            self.execute(
                "INSERT INTO threads(thread_id, user_id, tenant_id, summary, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (thread_id, user_id, tenant_id, summary, now, now),
            )

    def get_thread(self, thread_id: str) -> dict | None:
        return self.query_one("SELECT * FROM threads WHERE thread_id=?", (thread_id,))

    def assert_thread_owner(self, thread_id: str, user_id: str, tenant_id: str | None = None) -> dict:
        thread = self.get_thread(thread_id)
        if not thread:
            raise KeyError(thread_id)
        self._validate_existing(thread_id, thread, user_id, tenant_id)
        return thread

    def list_threads(self, user_id: str | None = None, limit: int = 100, tenant_id: str | None = None) -> list[dict]:
        """List threads with an optional tenant scope for both user and admin views.

        A tenant-scoped caller must never enumerate another tenant's threads.
        An unbound row is tolerated only in explicitly local development;
        protected profiles reject it before this query is executed.  Apply the
        same tenant filter whether or not a user filter is present.
        """
        clauses: list[str] = []
        params: list[object] = []
        if user_id:
            clauses.append("user_id=?")
            params.append(user_id)
        if tenant_id:
            if _protected_profile():
                clauses.append("tenant_id=?")
                params.append(tenant_id)
            else:
                clauses.append("(tenant_id=? OR tenant_id IS NULL)")
                params.append(tenant_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return self.query_all(f"SELECT * FROM threads{where} ORDER BY updated_at DESC LIMIT ?", tuple(params))
