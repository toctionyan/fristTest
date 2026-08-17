from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent_core.storage.repositories.base import ActiveDraftValidationCode, ActiveDraftValidationResult, TransactionScope
from agent_core.observability.correlation import get_correlation_id
from uuid import uuid4

from agent_core.persistence.sqlite_base import SQLiteBase
from agent_core.operations.draft import draft_persistence_update_decision


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


class IdempotencyStore(SQLiteBase):
    """Durable idempotency records for write actions."""

    def __init__(self, db_path: Path):
        super().__init__(db_path)

    def init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_records (
                idempotency_key TEXT PRIMARY KEY,
                action_name TEXT,
                request_hash TEXT,
                status TEXT,
                result_json TEXT,
                error TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        self.conn.commit()

    def get(self, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        row = self.query_one("SELECT * FROM idempotency_records WHERE idempotency_key=?", (key,))
        if row:
            row["result"] = _decode(row.get("result_json"))
        return row

    def start(self, *, key: str, action_name: str, request_hash: str) -> dict[str, Any]:
        existing = self.get(key)
        if existing:
            return {"created": False, "record": existing}
        now = _now()
        self.execute(
            """
            INSERT INTO idempotency_records(idempotency_key, action_name, request_hash, status, result_json, error, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (key, action_name, request_hash, "running", None, None, now, now),
        )
        return {"created": True, "record": self.get(key)}

    def finish(self, *, key: str, status: str, result: Any, error: str | None = None) -> None:
        self.execute(
            """
            UPDATE idempotency_records SET status=?, result_json=?, error=?, updated_at=? WHERE idempotency_key=?
            """,
            (status, _json(result), error, _now(), key),
        )


class ActionLockStore(SQLiteBase):
    """SQLite lease with renewal and monotonically increasing fencing tokens."""

    def __init__(self, db_path: Path):
        super().__init__(db_path)

    def init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_lock_tokens (
                token INTEGER PRIMARY KEY AUTOINCREMENT,
                lock_key TEXT NOT NULL,
                issued_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_locks (
                lock_key TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                fencing_token INTEGER NOT NULL DEFAULT 0,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                renewed_at TEXT NOT NULL
            )
            """
        )
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(action_locks)").fetchall()}
        if "fencing_token" not in columns:
            self.conn.execute("ALTER TABLE action_locks ADD COLUMN fencing_token INTEGER NOT NULL DEFAULT 0")
        if "renewed_at" not in columns:
            self.conn.execute("ALTER TABLE action_locks ADD COLUMN renewed_at TEXT")
            self.conn.execute("UPDATE action_locks SET renewed_at=COALESCE(renewed_at, created_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_action_locks_expires_at ON action_locks(expires_at)")
        self.conn.commit()

    def acquire(self, lock_key: str, *, owner: str | None = None, ttl_seconds: int = 120) -> dict[str, Any]:
        owner = owner or str(uuid4())
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat()
        with self.lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.execute("DELETE FROM action_locks WHERE lock_key=? AND expires_at<=?", (lock_key, now))
                row = self.conn.execute("SELECT owner,fencing_token,expires_at FROM action_locks WHERE lock_key=?", (lock_key,)).fetchone()
                if row is not None:
                    self.conn.commit()
                    return {
                        "acquired": False,
                        "owner": row["owner"],
                        "fencing_token": int(row["fencing_token"] or 0),
                        "expires_at": row["expires_at"],
                    }
                token_row = self.conn.execute(
                    "INSERT INTO action_lock_tokens(lock_key,issued_at) VALUES(?,?)",
                    (lock_key, now),
                )
                token = int(token_row.lastrowid)
                self.conn.execute(
                    "INSERT INTO action_locks(lock_key,owner,fencing_token,expires_at,created_at,renewed_at) VALUES(?,?,?,?,?,?)",
                    (lock_key, owner, token, expires, now, now),
                )
                self.conn.commit()
                return {
                    "acquired": True,
                    "owner": owner,
                    "fencing_token": token,
                    "expires_at": expires,
                }
            except Exception:
                self.conn.rollback()
                raise

    def renew(self, lock_key: str, *, owner: str, fencing_token: int, ttl_seconds: int = 120) -> dict[str, Any]:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat()
        with self.lock:
            changed = self.conn.execute(
                """UPDATE action_locks
                   SET expires_at=?, renewed_at=?
                   WHERE lock_key=? AND owner=? AND fencing_token=? AND expires_at>?""",
                (expires, now, lock_key, owner, int(fencing_token), now),
            ).rowcount
            self.conn.commit()
        return {"renewed": int(changed or 0) == 1, "expires_at": expires if changed else None}

    def validate(self, lock_key: str, *, owner: str, fencing_token: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        row = self.query_one(
            "SELECT 1 AS valid FROM action_locks WHERE lock_key=? AND owner=? AND fencing_token=? AND expires_at>?",
            (lock_key, owner, int(fencing_token), now),
        )
        return bool(row)

    def release(self, lock_key: str, owner: str | None = None, fencing_token: int | None = None) -> None:
        clauses = ["lock_key=?"]
        params: list[Any] = [lock_key]
        if owner is not None:
            clauses.append("owner=?")
            params.append(owner)
        if fencing_token is not None:
            clauses.append("fencing_token=?")
            params.append(int(fencing_token))
        self.execute(f"DELETE FROM action_locks WHERE {' AND '.join(clauses)}", tuple(params))


class OutboxStore(SQLiteBase):
    """Transactional outbox table for action side-effect events."""

    def __init__(self, db_path: Path):
        super().__init__(db_path)

    def init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                aggregate_key TEXT,
                payload_json TEXT,
                status TEXT,
                created_at TEXT,
                published_at TEXT
            )
            """
        )
        self.conn.commit()

    def enqueue(self, *, event_type: str, aggregate_key: str | None, payload: Any) -> int:
        cur = self.execute(
            """
            INSERT INTO outbox_events(event_type, aggregate_key, payload_json, status, created_at, published_at)
            VALUES(?,?,?,?,?,NULL)
            """,
            (event_type, aggregate_key, _json(payload), "pending", _now()),
        )
        return int(cur.lastrowid)

    def list_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query_all("SELECT * FROM outbox_events WHERE status='pending' ORDER BY id ASC LIMIT ?", (limit,))
        for row in rows:
            row["payload"] = _decode(row.get("payload_json"))
        return rows

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query_all("SELECT * FROM outbox_events ORDER BY id DESC LIMIT ?", (limit,))
        for row in rows:
            row["payload"] = _decode(row.get("payload_json"))
        return rows


class ActionRunStore(SQLiteBase):
    """Write action lifecycle state machine transitions."""

    TERMINAL = {"succeeded", "failed", "cancelled", "idempotent_replay"}

    def __init__(self, db_path: Path):
        super().__init__(db_path)

    def init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_runs (
                run_id TEXT PRIMARY KEY,
                idempotency_key TEXT,
                action_name TEXT,
                status TEXT,
                transitions_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        self.conn.commit()

    def start(self, *, idempotency_key: str | None, action_name: str) -> str:
        run_id = str(uuid4())
        now = _now()
        transitions = [{"status": "created", "at": now}, {"status": "running", "at": now}]
        self.execute(
            """
            INSERT INTO action_runs(run_id, idempotency_key, action_name, status, transitions_json, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (run_id, idempotency_key, action_name, "running", _json(transitions), now, now),
        )
        return run_id

    def transition(self, run_id: str, status: str, *, detail: Any = None) -> None:
        row = self.query_one("SELECT * FROM action_runs WHERE run_id=?", (run_id,))
        if not row:
            return
        transitions = _decode(row.get("transitions_json")) or []
        transitions.append({"status": status, "at": _now(), "detail": detail})
        self.execute(
            "UPDATE action_runs SET status=?, transitions_json=?, updated_at=? WHERE run_id=?",
            (status, _json(transitions), _now(), run_id),
        )

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM action_runs WHERE run_id=?", (run_id,))
        if row:
            row["transitions"] = _decode(row.get("transitions_json")) or []
        return row

    def find_by_idempotency_key(self, idempotency_key: str | None) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        row = self.query_one(
            "SELECT * FROM action_runs WHERE idempotency_key=? ORDER BY created_at DESC LIMIT 1",
            (idempotency_key,),
        )
        if row:
            row["transitions"] = _decode(row.get("transitions_json")) or []
        return row

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query_all("SELECT * FROM action_runs ORDER BY created_at DESC LIMIT ?", (limit,))
        for row in rows:
            row["transitions"] = _decode(row.get("transitions_json")) or []
        return rows


class TransactionLifecycleStore(SQLiteBase):
    """Durable Agent-side transaction lifecycle store.

    This store deliberately records only Agent protocol objects (grants and
    commit attempts).  Business Service remains the source of truth for the
    actual refund/after-sales/invoice/order facts.
    """

    def __init__(self, db_path: Path):
        super().__init__(db_path)

    def init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_grants (
                grant_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                draft_id TEXT NOT NULL,
                draft_revision INTEGER NOT NULL,
                command_digest TEXT NOT NULL,
                confirmation_id TEXT NOT NULL,
                client_request_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_role TEXT,
                state TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                reserved_at TEXT,
                consumed_at TEXT,
                revoked_at TEXT,
                expires_at TEXT,
                attempt_id TEXT,
                receipt_handle TEXT,
                reason TEXT,
                correlation_id TEXT,
                UNIQUE(tenant_id, user_id, thread_id, draft_id, draft_revision, command_digest, confirmation_id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_attempts (
                attempt_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                draft_id TEXT NOT NULL,
                draft_revision INTEGER NOT NULL,
                grant_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                command_digest TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                canonical_payload_json TEXT NOT NULL,
                business_command_envelope_json TEXT,
                state TEXT NOT NULL,
                business_result_json TEXT,
                receipt_handle TEXT,
                error_code TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reconciled_at TEXT,
                correlation_id TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_drafts (
                draft_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                draft_revision INTEGER NOT NULL,
                draft_state TEXT NOT NULL,
                action_id TEXT NOT NULL,
                command_digest TEXT NOT NULL,
                command_envelope_json TEXT,
                projection_json TEXT,
                active_grant_id TEXT,
                current_attempt_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                correlation_id TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_receipts (
                receipt_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                draft_id TEXT NOT NULL,
                attempt_id TEXT UNIQUE,
                receipt_handle TEXT,
                receipt_state TEXT NOT NULL,
                business_result_json TEXT NOT NULL,
                business_resource_id TEXT,
                created_at TEXT NOT NULL,
                correlation_id TEXT
            )
            """
        )
        existing = {str(row[1]) for row in self.conn.execute("PRAGMA table_info(transaction_attempts)").fetchall()}
        if "business_command_envelope_json" not in existing:
            self.conn.execute("ALTER TABLE transaction_attempts ADD COLUMN business_command_envelope_json TEXT")
        # Additive local migration for records created before correlation became
        # part of the end-to-end transaction diagnostic chain.
        for table in ("transaction_grants", "transaction_attempts", "transaction_drafts", "transaction_receipts"):
            columns = {str(row[1]) for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "correlation_id" not in columns:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN correlation_id TEXT")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_transaction_attempts_scope_state ON transaction_attempts(tenant_id, user_id, thread_id, state)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_transaction_attempts_correlation ON transaction_attempts(correlation_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_transaction_drafts_scope_state ON transaction_drafts(tenant_id, user_id, thread_id, draft_state)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_transaction_receipts_draft ON transaction_receipts(draft_id)")
        self.conn.commit()

    @staticmethod
    def _decode_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        result = dict(row)
        for name in ("business_result_json", "canonical_payload_json", "business_command_envelope_json", "command_envelope_json", "projection_json"):
            if name in result:
                decoded = _decode(result.get(name))
                result[name.replace("_json", "")] = decoded
        return result

    def create_draft(self, *, draft_id: str, tenant_id: str, user_id: str, thread_id: str, draft_revision: int, draft_state: str, action_id: str, command_digest: str, command_envelope: dict[str, Any] | None, projection: dict[str, Any] | None, active_grant_id: str | None = None, current_attempt_id: str | None = None, correlation_id: str | None = None) -> dict[str, Any]:
        now = _now()
        correlation_id = correlation_id or get_correlation_id()
        with self.lock:
            existing = self.conn.execute("SELECT * FROM transaction_drafts WHERE draft_id=?", (draft_id,)).fetchone()
            existing_payload = self._decode_row(dict(existing)) if existing else None
            incoming_payload = {
                "draft_id": draft_id, "tenant_id": tenant_id, "user_id": user_id, "thread_id": thread_id,
                "draft_revision": int(draft_revision), "draft_state": draft_state, "action_id": action_id,
                "command_digest": command_digest, "command_envelope": command_envelope, "projection": projection,
            }
            allowed, _reason = draft_persistence_update_decision(existing_payload, incoming_payload)
            if existing_payload and not allowed:
                return existing_payload
            data = (
                tenant_id, user_id, thread_id, int(draft_revision), draft_state, action_id, command_digest,
                _json(command_envelope) if command_envelope is not None else None,
                _json(projection) if projection is not None else None,
                active_grant_id, current_attempt_id, correlation_id, now,
            )
            if existing:
                self.conn.execute(
                    """UPDATE transaction_drafts SET tenant_id=?,user_id=?,thread_id=?,draft_revision=?,draft_state=?,action_id=?,command_digest=?,command_envelope_json=?,projection_json=?,active_grant_id=?,current_attempt_id=?,correlation_id=?,updated_at=? WHERE draft_id=?""",
                    (*data, draft_id),
                )
            else:
                self.conn.execute(
                    """INSERT INTO transaction_drafts(draft_id,tenant_id,user_id,thread_id,draft_revision,draft_state,action_id,command_digest,command_envelope_json,projection_json,active_grant_id,current_attempt_id,correlation_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (draft_id, *data, now),
                )
            self.conn.commit()
        return self.get_draft(draft_id) or {}

    def get_draft(self, draft_id: str | None) -> dict[str, Any] | None:
        if not draft_id:
            return None
        return self._decode_row(self.query_one("SELECT * FROM transaction_drafts WHERE draft_id=?", (draft_id,)))

    def advance_draft(self, draft_id: str, *, draft_state: str, draft_revision: int | None = None, command_digest: str | None = None, command_envelope: dict[str, Any] | None = None, projection: dict[str, Any] | None = None, active_grant_id: str | None = None, current_attempt_id: str | None = None) -> None:
        with self.lock:
            existing = self.conn.execute("SELECT * FROM transaction_drafts WHERE draft_id=?", (draft_id,)).fetchone()
            if not existing:
                return
            current = self._decode_row(dict(existing)) or {}
            incoming = dict(current)
            incoming["draft_state"] = draft_state
            if draft_revision is not None: incoming["draft_revision"] = int(draft_revision)
            if command_digest is not None: incoming["command_digest"] = command_digest
            if command_envelope is not None: incoming["command_envelope"] = command_envelope
            if projection is not None: incoming["projection"] = projection
            allowed, _reason = draft_persistence_update_decision(current, incoming)
            if not allowed:
                return
            cols = ["draft_state=?", "updated_at=?"]
            vals: list[Any] = [draft_state, _now()]
            for name, value in (
                ("draft_revision", draft_revision),
                ("command_digest", command_digest),
                ("command_envelope_json", _json(command_envelope) if command_envelope is not None else None),
                ("projection_json", _json(projection) if projection is not None else None),
                ("active_grant_id", active_grant_id),
                ("current_attempt_id", current_attempt_id),
            ):
                if value is not None:
                    cols.append(f"{name}=?")
                    vals.append(value)
            vals.append(draft_id)
            self.conn.execute(f"UPDATE transaction_drafts SET {', '.join(cols)} WHERE draft_id=?", tuple(vals))
            self.conn.commit()

    def list_drafts_by_thread(self, *, tenant_id: str, user_id: str, thread_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows=self.query_all("SELECT * FROM transaction_drafts WHERE tenant_id=? AND user_id=? AND thread_id=? ORDER BY updated_at DESC LIMIT ?", (tenant_id,user_id,thread_id,int(limit)))
        return [self._decode_row(row) or {} for row in rows]

    def record_receipt(self, *, receipt_id: str, tenant_id: str, user_id: str, thread_id: str, draft_id: str, attempt_id: str | None, receipt_handle: str | None, receipt_state: str, business_result: dict[str, Any], business_resource_id: str | None = None, correlation_id: str | None = None) -> dict[str, Any]:
        now=_now()
        correlation_id = correlation_id or get_correlation_id()
        with self.lock:
            if attempt_id:
                existing=self.conn.execute("SELECT * FROM transaction_receipts WHERE attempt_id=?", (attempt_id,)).fetchone()
                if existing:
                    return self._decode_row(dict(existing)) or {}
            self.conn.execute("""INSERT INTO transaction_receipts(receipt_id,tenant_id,user_id,thread_id,draft_id,attempt_id,receipt_handle,receipt_state,business_result_json,business_resource_id,created_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (receipt_id,tenant_id,user_id,thread_id,draft_id,attempt_id,receipt_handle,receipt_state,_json(business_result),business_resource_id,now,correlation_id))
            self.conn.commit()
        return self.get_receipt(receipt_id) or {}

    def get_receipt(self, receipt_id: str | None) -> dict[str, Any] | None:
        return self._decode_row(self.query_one("SELECT * FROM transaction_receipts WHERE receipt_id=?", (receipt_id,))) if receipt_id else None

    def get_receipt_by_attempt(self, attempt_id: str | None) -> dict[str, Any] | None:
        return self._decode_row(self.query_one("SELECT * FROM transaction_receipts WHERE attempt_id=?", (attempt_id,))) if attempt_id else None

    def get_draft_for_scope(self, *, scope: TransactionScope, draft_id: str) -> dict[str, Any] | None:
        clauses = ["draft_id=?", "tenant_id=?", "user_id=?"]
        values: list[Any] = [draft_id, scope.tenant_id, scope.user_id]
        if scope.thread_id:
            clauses.append("thread_id=?")
            values.append(scope.thread_id)
        return self._decode_row(self.query_one(f"SELECT * FROM transaction_drafts WHERE {' AND '.join(clauses)}", tuple(values)))

    def list_drafts_for_scope(self, *, scope: TransactionScope, states: set[str] | None = None, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]:
        clauses = ["tenant_id=?", "user_id=?"]
        values: list[Any] = [scope.tenant_id, scope.user_id]
        if scope.thread_id:
            clauses.append("thread_id=?")
            values.append(scope.thread_id)
        if states:
            normalized = sorted({str(value).upper() for value in states})
            clauses.append(f"draft_state IN ({','.join('?' for _ in normalized)})")
            values.extend(normalized)
        if cursor:
            clauses.append("updated_at<?")
            values.append(cursor)
        values.append(max(1, min(int(limit), 200)))
        rows = self.query_all(
            f"SELECT * FROM transaction_drafts WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?",
            tuple(values),
        )
        return [self._decode_row(row) or {} for row in rows]

    def validate_active_draft(self, *, scope: TransactionScope, draft_id: str, expected_revision: int | None = None, allowed_states: set[str] | None = None) -> ActiveDraftValidationResult:
        scoped = self.get_draft_for_scope(scope=scope, draft_id=draft_id)
        if scoped is None:
            raw = self.get_draft(draft_id)
            return ActiveDraftValidationResult(
                ActiveDraftValidationCode.SCOPE_MISMATCH if raw is not None else ActiveDraftValidationCode.NOT_FOUND,
                None,
            )
        state = str(scoped.get("draft_state") or "").upper()
        if state == "EXPIRED":
            return ActiveDraftValidationResult(ActiveDraftValidationCode.EXPIRED, scoped)
        if state == "REVOKED":
            return ActiveDraftValidationResult(ActiveDraftValidationCode.REVOKED, scoped)
        if expected_revision is not None and int(scoped.get("draft_revision") or 0) != int(expected_revision):
            return ActiveDraftValidationResult(ActiveDraftValidationCode.REVISION_MISMATCH, scoped)
        if allowed_states and state not in {str(item).upper() for item in allowed_states}:
            return ActiveDraftValidationResult(ActiveDraftValidationCode.STATE_NOT_ALLOWED, scoped)
        return ActiveDraftValidationResult(ActiveDraftValidationCode.OK, scoped)

    def get_attempt_for_scope(self, *, scope: TransactionScope, attempt_id: str) -> dict[str, Any] | None:
        clauses = ["attempt_id=?", "tenant_id=?", "user_id=?"]
        values: list[Any] = [attempt_id, scope.tenant_id, scope.user_id]
        if scope.thread_id:
            clauses.append("thread_id=?")
            values.append(scope.thread_id)
        return self._decode_row(self.query_one(f"SELECT * FROM transaction_attempts WHERE {' AND '.join(clauses)}", tuple(values)))

    def list_attempts_for_draft(self, *, scope: TransactionScope, draft_id: str) -> list[dict[str, Any]]:
        clauses = ["draft_id=?", "tenant_id=?", "user_id=?"]
        values: list[Any] = [draft_id, scope.tenant_id, scope.user_id]
        if scope.thread_id:
            clauses.append("thread_id=?")
            values.append(scope.thread_id)
        rows = self.query_all(f"SELECT * FROM transaction_attempts WHERE {' AND '.join(clauses)} ORDER BY created_at DESC", tuple(values))
        return [self._decode_row(row) or {} for row in rows]

    def get_receipt_for_attempt(self, *, scope: TransactionScope, attempt_id: str) -> dict[str, Any] | None:
        clauses = ["attempt_id=?", "tenant_id=?", "user_id=?"]
        values: list[Any] = [attempt_id, scope.tenant_id, scope.user_id]
        if scope.thread_id:
            clauses.append("thread_id=?")
            values.append(scope.thread_id)
        return self._decode_row(self.query_one(f"SELECT * FROM transaction_receipts WHERE {' AND '.join(clauses)}", tuple(values)))

    def get_latest_receipt_for_draft(self, *, scope: TransactionScope, draft_id: str) -> dict[str, Any] | None:
        clauses = ["draft_id=?", "tenant_id=?", "user_id=?"]
        values: list[Any] = [draft_id, scope.tenant_id, scope.user_id]
        if scope.thread_id:
            clauses.append("thread_id=?")
            values.append(scope.thread_id)
        return self._decode_row(self.query_one(f"SELECT * FROM transaction_receipts WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT 1", tuple(values)))

    def issue_grant(
        self,
        *,
        grant_id: str,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        draft_id: str,
        draft_revision: int,
        command_digest: str,
        confirmation_id: str,
        client_request_id: str,
        actor_id: str,
        actor_role: str,
        expires_at: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        correlation_id = correlation_id or get_correlation_id()
        with self.lock:
            existing = self.conn.execute(
                """SELECT * FROM transaction_grants WHERE tenant_id=? AND user_id=? AND thread_id=?
                   AND draft_id=? AND draft_revision=? AND command_digest=? AND confirmation_id=?""",
                (tenant_id, user_id, thread_id, draft_id, int(draft_revision), command_digest, confirmation_id),
            ).fetchone()
            if existing:
                return self._decode_row(dict(existing)) or {}
            self.conn.execute(
                """INSERT INTO transaction_grants(
                    grant_id, tenant_id, user_id, thread_id, draft_id, draft_revision, command_digest,
                    confirmation_id, client_request_id, actor_id, actor_role, state, issued_at, expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    grant_id, tenant_id, user_id, thread_id, draft_id, int(draft_revision), command_digest,
                    confirmation_id, client_request_id, actor_id, actor_role, "ISSUED", now, expires_at,
                ),
            )
            self.conn.commit()
        return self.get_grant(grant_id) or {}

    def get_grant(self, grant_id: str | None) -> dict[str, Any] | None:
        if not grant_id:
            return None
        return self._decode_row(self.query_one("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)))

    def list_grants_by_thread(self, *, tenant_id: str, user_id: str, thread_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query_all(
            "SELECT * FROM transaction_grants WHERE tenant_id=? AND user_id=? AND thread_id=? ORDER BY issued_at DESC LIMIT ?",
            (tenant_id, user_id, thread_id, int(limit)),
        )
        return [self._decode_row(row) or {} for row in rows]

    def reserve_grant(self, grant_id: str, *, attempt_id: str | None = None) -> dict[str, Any]:
        """Atomically reserve one unexpired grant without creating an attempt."""
        now = _now()
        with self.lock:
            # Promote a stale unconsumed grant to an explicit terminal state.
            self.conn.execute(
                "UPDATE transaction_grants SET state='EXPIRED', revoked_at=?, reason='grant_expired' "
                "WHERE grant_id=? AND state='ISSUED' AND expires_at IS NOT NULL AND expires_at<=?",
                (now, grant_id, now),
            )
            cur = self.conn.execute(
                "UPDATE transaction_grants SET state='RESERVED', reserved_at=?, attempt_id=COALESCE(?, attempt_id) "
                "WHERE grant_id=? AND state='ISSUED' AND (expires_at IS NULL OR expires_at>?)",
                (now, attempt_id, grant_id, now),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()
        payload = self._decode_row(dict(row) if row else None) or {}
        return {"reserved": int(cur.rowcount or 0) == 1, "grant": payload}

    def reserve_grant_and_start_attempt(
        self,
        *,
        grant_id: str,
        attempt_id: str,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        draft_id: str,
        draft_revision: int,
        action_id: str,
        command_digest: str,
        idempotency_key: str,
        canonical_payload: dict[str, Any],
        business_command_envelope: dict[str, Any] | None = None,
        draft_projection: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Reserve one Grant and its first Attempt in the same transaction.

        If a canonical Draft exists, its terminal state/revision/digest outranks
        every stale Workflow copy. A compatibility-only store probe without a
        Draft is still supported, but production authority flow always persists
        the Draft before Grant issuance.
        """
        now = _now()
        correlation_id = correlation_id or get_correlation_id()
        with self.lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                existing = self.conn.execute(
                    "SELECT * FROM transaction_attempts WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if existing:
                    grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()
                    self.conn.commit()
                    return {
                        "reserved": False,
                        "grant": self._decode_row(dict(grant) if grant else None) or {},
                        "created": False,
                        "attempt": self._decode_row(dict(existing)) or {},
                    }

                canonical = self.conn.execute(
                    "SELECT * FROM transaction_drafts WHERE draft_id=? AND tenant_id=? AND user_id=? AND thread_id=?",
                    (draft_id, tenant_id, user_id, thread_id),
                ).fetchone()
                canonical_payload = self._decode_row(dict(canonical)) if canonical else None
                if canonical_payload:
                    committing_projection = dict(canonical_payload)
                    committing_projection.update({
                        "draft_state": "COMMITTING",
                        "draft_revision": int(draft_revision),
                        "command_digest": command_digest,
                    })
                    if draft_projection is not None:
                        committing_projection["projection"] = draft_projection
                    allowed, reason = draft_persistence_update_decision(canonical_payload, committing_projection)
                    if not allowed:
                        self.conn.execute(
                            "UPDATE transaction_grants SET state='REVOKED', revoked_at=?, reason=? WHERE grant_id=? AND state='ISSUED'",
                            (now, "draft_update_rejected:" + reason, grant_id),
                        )
                        grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()
                        self.conn.commit()
                        return {
                            "reserved": False,
                            "grant": self._decode_row(dict(grant) if grant else None) or {},
                            "created": False,
                            "attempt": {},
                        }

                self.conn.execute(
                    "UPDATE transaction_grants SET state='EXPIRED', revoked_at=?, reason='grant_expired' "
                    "WHERE grant_id=? AND state='ISSUED' AND expires_at IS NOT NULL AND expires_at<=?",
                    (now, grant_id, now),
                )
                reserved = self.conn.execute(
                    "UPDATE transaction_grants SET state='RESERVED', reserved_at=?, attempt_id=? "
                    "WHERE grant_id=? AND state='ISSUED' AND (expires_at IS NULL OR expires_at>?)",
                    (now, attempt_id, grant_id, now),
                )
                grant = self.conn.execute("SELECT * FROM transaction_grants WHERE grant_id=?", (grant_id,)).fetchone()
                if int(reserved.rowcount or 0) != 1:
                    self.conn.commit()
                    return {
                        "reserved": False,
                        "grant": self._decode_row(dict(grant) if grant else None) or {},
                        "created": False,
                        "attempt": {},
                    }
                self.conn.execute(
                    """INSERT INTO transaction_attempts(
                        attempt_id, tenant_id, user_id, thread_id, draft_id, draft_revision, grant_id, action_id,
                        command_digest, idempotency_key, canonical_payload_json, business_command_envelope_json, state, created_at, updated_at, correlation_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        attempt_id, tenant_id, user_id, thread_id, draft_id, int(draft_revision), grant_id, action_id,
                        command_digest, idempotency_key, _json(canonical_payload),
                        _json(business_command_envelope) if business_command_envelope is not None else None,
                        "STARTED", now, now, correlation_id,
                    ),
                )
                self.conn.execute(
                    """INSERT INTO transaction_drafts(draft_id,tenant_id,user_id,thread_id,draft_revision,draft_state,action_id,command_digest,command_envelope_json,projection_json,active_grant_id,current_attempt_id,created_at,updated_at,correlation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(draft_id) DO UPDATE SET draft_revision=excluded.draft_revision,draft_state=excluded.draft_state,action_id=excluded.action_id,command_digest=excluded.command_digest,command_envelope_json=excluded.command_envelope_json,projection_json=excluded.projection_json,active_grant_id=excluded.active_grant_id,current_attempt_id=excluded.current_attempt_id,updated_at=excluded.updated_at,correlation_id=excluded.correlation_id""",
                    (
                        draft_id, tenant_id, user_id, thread_id, int(draft_revision), "COMMITTING", action_id, command_digest,
                        _json(business_command_envelope) if business_command_envelope is not None else None,
                        _json(draft_projection) if draft_projection is not None else None,
                        grant_id, attempt_id, now, now, correlation_id,
                    ),
                )
                attempt = self.conn.execute("SELECT * FROM transaction_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
                self.conn.commit()
                return {
                    "reserved": True,
                    "grant": self._decode_row(dict(grant) if grant else None) or {},
                    "created": True,
                    "attempt": self._decode_row(dict(attempt) if attempt else None) or {},
                }
            except Exception:
                self.conn.rollback()
                raise

    def consume_grant(self, grant_id: str, *, attempt_id: str | None = None, receipt_handle: str | None = None) -> None:
        self.execute(
            """UPDATE transaction_grants SET state='CONSUMED', consumed_at=?,
               attempt_id=COALESCE(?, attempt_id), receipt_handle=COALESCE(?, receipt_handle)
               WHERE grant_id=?""",
            (_now(), attempt_id, receipt_handle, grant_id),
        )

    def revoke_grant(self, grant_id: str, *, reason: str) -> None:
        self.execute(
            "UPDATE transaction_grants SET state='REVOKED', revoked_at=?, reason=? WHERE grant_id=? AND state IN ('ISSUED','RESERVED')",
            (_now(), reason, grant_id),
        )

    def start_attempt(
        self,
        *,
        attempt_id: str,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        draft_id: str,
        draft_revision: int,
        grant_id: str,
        action_id: str,
        command_digest: str,
        idempotency_key: str,
        canonical_payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = _now()
        with self.lock:
            existing = self.conn.execute("SELECT * FROM transaction_attempts WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                return {"created": False, "attempt": self._decode_row(dict(existing)) or {}}
            self.conn.execute(
                """INSERT INTO transaction_attempts(
                    attempt_id, tenant_id, user_id, thread_id, draft_id, draft_revision, grant_id, action_id,
                    command_digest, idempotency_key, canonical_payload_json, state, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    attempt_id, tenant_id, user_id, thread_id, draft_id, int(draft_revision), grant_id, action_id,
                    command_digest, idempotency_key, _json(canonical_payload), "STARTED", now, now,
                ),
            )
            self.conn.commit()
        return {"created": True, "attempt": self.get_attempt(attempt_id) or {}}

    def get_attempt(self, attempt_id: str | None) -> dict[str, Any] | None:
        if not attempt_id:
            return None
        return self._decode_row(self.query_one("SELECT * FROM transaction_attempts WHERE attempt_id=?", (attempt_id,)))

    def get_attempt_by_idempotency_key(self, idempotency_key: str | None) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        return self._decode_row(self.query_one("SELECT * FROM transaction_attempts WHERE idempotency_key=?", (idempotency_key,)))

    def transition_attempt(
        self,
        attempt_id: str,
        *,
        state: str,
        business_result: dict[str, Any] | None = None,
        receipt_handle: str | None = None,
        error_code: str | None = None,
        error: str | None = None,
        reconciled: bool = False,
    ) -> None:
        self.execute(
            """UPDATE transaction_attempts SET state=?, business_result_json=COALESCE(?, business_result_json),
               receipt_handle=COALESCE(?, receipt_handle), error_code=?, error=?, updated_at=?,
               reconciled_at=CASE WHEN ? THEN ? ELSE reconciled_at END WHERE attempt_id=?""",
            (
                state,
                _json(business_result) if business_result is not None else None,
                receipt_handle,
                error_code,
                error,
                _now(),
                1 if reconciled else 0,
                _now(),
                attempt_id,
            ),
        )

    def list_reconcilable_attempts(self, *, scope: TransactionScope | None = None, tenant_id: str | None = None, user_id: str | None = None, thread_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if scope is not None:
            tenant_id, user_id, thread_id = scope.tenant_id, scope.user_id, scope.thread_id
        clauses = ["state IN ('STARTED','SUBMISSION_UNKNOWN')"]
        values: list[Any] = []
        if tenant_id:
            clauses.append("tenant_id=?")
            values.append(tenant_id)
        if user_id:
            clauses.append("user_id=?")
            values.append(user_id)
        if thread_id:
            clauses.append("thread_id=?")
            values.append(thread_id)
        values.append(max(1, min(int(limit), 200)))
        rows = self.query_all(
            f"SELECT * FROM transaction_attempts WHERE {' AND '.join(clauses)} ORDER BY updated_at ASC LIMIT ?",
            tuple(values),
        )
        return [self._decode_row(row) or {} for row in rows]

    def list_by_thread(self, *, tenant_id: str, user_id: str, thread_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query_all(
            "SELECT * FROM transaction_attempts WHERE tenant_id=? AND user_id=? AND thread_id=? ORDER BY created_at DESC LIMIT ?",
            (tenant_id, user_id, thread_id, int(limit)),
        )
        return [self._decode_row(row) or {} for row in rows]
