from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


RESOURCE_TABLES: tuple[str, ...] = (
    "refunds",
    "after_sales_tickets",
    "invoices",
    "complaints",
    "human_handoffs",
    "delivery_urges",
)


class BusinessDatabase:
    """SQLite persistence for the demo business service.

    The schema intentionally stores the durable business invariants needed by
    any normal channel (web, mobile, operations console, Agent): tenant,
    subject/owner, creating actor, resource version, idempotency scope and
    append-only audit facts.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }

    @staticmethod
    def _add_column_if_missing(
        conn: sqlite3.Connection, table: str, column: str, ddl: str
    ) -> None:
        if column not in BusinessDatabase._columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _migrate_idempotency(self, conn: sqlite3.Connection) -> None:
        """Move v6.2 idempotency records to tenant-scoped v6.3 records.

        SQLite cannot alter a composite primary key in place.  A new table is
        created, historical rows are conservatively assigned to ``default``
        (the only tenant used by v6.2 seed data), and then atomically swapped.
        """
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='idempotency_records'"
        ).fetchone()
        if not exists:
            return
        columns = self._columns(conn, "idempotency_records")
        if "tenant_id" in columns and "command_name" in columns:
            return
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_records_v63 (
                tenant_id TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                command_name TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(tenant_id, actor_user_id, command_name, idempotency_key)
            )
            """
        )
        legacy_rows = conn.execute(
            "SELECT actor_user_id, action, idempotency_key, request_hash, response_json, created_at FROM idempotency_records"
        ).fetchall()
        for row in legacy_rows:
            conn.execute(
                """
                INSERT OR IGNORE INTO idempotency_records_v63
                (tenant_id,actor_user_id,command_name,idempotency_key,request_hash,response_json,created_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    "default",
                    row["actor_user_id"],
                    row["action"],
                    row["idempotency_key"],
                    row["request_hash"],
                    row["response_json"],
                    row["created_at"],
                ),
            )
        conn.execute("DROP TABLE idempotency_records")
        conn.execute(
            "ALTER TABLE idempotency_records_v63 RENAME TO idempotency_records"
        )

    def initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS accounts (
            user_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            role TEXT NOT NULL DEFAULT 'customer',
            display_name TEXT NOT NULL,
            permissions_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
            product_id TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            category TEXT,
            price REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            warranty_months INTEGER NOT NULL DEFAULT 0,
            returnable INTEGER NOT NULL DEFAULT 1,
            support_after_sales INTEGER NOT NULL DEFAULT 1,
            description TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            product_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            status TEXT NOT NULL,
            amount REAL NOT NULL,
            paid INTEGER NOT NULL DEFAULT 1,
            signed_at TEXT,
            shipping_address TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES accounts(user_id),
            FOREIGN KEY(product_id) REFERENCES products(product_id)
        );
        CREATE TABLE IF NOT EXISTS logistics (
            order_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            latest TEXT NOT NULL,
            eta TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(order_id)
        );
        CREATE TABLE IF NOT EXISTS coupons (
            coupon_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            discount_desc TEXT,
            expires_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS refunds (
            refund_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            operator_note TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(order_id)
        );
        CREATE TABLE IF NOT EXISTS after_sales_tickets (
            ticket_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            operator_note TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(order_id)
        );
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            invoice_title TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            operator_note TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            issued_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(order_id, invoice_title),
            FOREIGN KEY(order_id) REFERENCES orders(order_id)
        );
        CREATE TABLE IF NOT EXISTS complaints (
            complaint_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            operator_note TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS delivery_urges (
            urge_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(order_id)
        );
        CREATE TABLE IF NOT EXISTS human_handoffs (
            handoff_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            operator_note TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS idempotency_records (
            tenant_id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL,
            command_name TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(tenant_id, actor_user_id, command_name, idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS actor_nonces (
            nonce TEXT PRIMARY KEY,
            expires_at_epoch INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            actor_user_id TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            subject_user_id TEXT,
            action TEXT NOT NULL,
            command_name TEXT,
            resource_type TEXT,
            resource_id TEXT,
            from_status TEXT,
            to_status TEXT,
            details_json TEXT NOT NULL,
            request_id TEXT,
            idempotency_key TEXT,
            created_at TEXT NOT NULL
        );
        """
        with self.transaction() as conn:
            conn.executescript(schema)
            self._migrate_idempotency(conn)
            self._add_column_if_missing(
                conn, "products", "support_after_sales", "INTEGER NOT NULL DEFAULT 1"
            )
            self._add_column_if_missing(
                conn, "orders", "version", "INTEGER NOT NULL DEFAULT 1"
            )
            # Business reason code is a normal domain field, distinct from raw
            # customer wording. It avoids using Agent-side keyword logic for
            # policy-sensitive product exceptions.
            self._add_column_if_missing(conn, "refunds", "reason_code", "TEXT")
            self._add_column_if_missing(conn, "after_sales_tickets", "reason_code", "TEXT")
            for table in RESOURCE_TABLES:
                # user_id is retained as the normal business owner field for
                # compatibility; these fields make actor vs subject explicit.
                self._add_column_if_missing(conn, table, "subject_user_id", "TEXT")
                self._add_column_if_missing(conn, table, "created_by_actor_id", "TEXT")
                self._add_column_if_missing(
                    conn, table, "version", "INTEGER NOT NULL DEFAULT 1"
                )
                self._add_column_if_missing(conn, table, "reviewed_by_actor_id", "TEXT")
            for column, ddl in (
                ("tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
                ("subject_user_id", "TEXT"),
                ("command_name", "TEXT"),
                ("from_status", "TEXT"),
                ("to_status", "TEXT"),
                ("request_id", "TEXT"),
                ("idempotency_key", "TEXT"),
            ):
                self._add_column_if_missing(conn, "audit_events", column, ddl)
            # Backfill legacy rows once. The source user_id is the domain
            # subject; created-by was unavailable in old data and is marked
            # conservatively as legacy migration.
            for table in RESOURCE_TABLES:
                conn.execute(
                    f"UPDATE {table} SET subject_user_id=COALESCE(subject_user_id,user_id), "
                    "created_by_actor_id=COALESCE(created_by_actor_id,'legacy_migration'), "
                    "version=COALESCE(version,1)"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_orders_user_tenant_created ON orders(user_id, tenant_id, created_at)"
            )
            for table in (
                "refunds",
                "after_sales_tickets",
                "invoices",
                "complaints",
                "human_handoffs",
            ):
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_tenant_subject ON {table}(tenant_id, subject_user_id, created_at)"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_events(tenant_id, resource_type, resource_id, event_id)"
            )


class _PostgresConnection:
    """Compatibility adapter for the service's parameterized SQL port."""

    def __init__(self, connection: Any):
        self.connection = connection

    @staticmethod
    def _sql(statement: str) -> str:
        return statement.replace("?", "%s")

    def execute(self, statement: str, params: tuple[Any, ...] | list[Any] = ()) -> Any:
        return self.connection.execute(self._sql(statement), params)

    def executemany(self, statement: str, rows: Any) -> Any:
        with self.connection.cursor() as cursor:
            return cursor.executemany(self._sql(statement), rows)

    def executescript(self, script: str) -> None:
        postgres = script.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
        )
        for statement in postgres.split(";"):
            if statement.strip():
                self.connection.execute(statement)


class PostgresBusinessDatabase(BusinessDatabase):
    """Shared PostgreSQL persistence for protected Business deployments."""

    def __init__(self, database_url: str):
        url = str(database_url or "").strip()
        if url.startswith("postgresql+psycopg://"):
            url = "postgresql://" + url.removeprefix("postgresql+psycopg://")
        if not url.startswith("postgresql://"):
            raise ValueError("PostgresBusinessDatabase requires a PostgreSQL URL")
        self.database_url = url
        self.path = Path("<postgresql>")

    def connect(self) -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except Exception as exc:  # pragma: no cover - deployment dependency failure
            raise RuntimeError(
                "BUSINESS_DB_BACKEND=postgres requires psycopg[binary]"
            ) from exc
        return psycopg.connect(self.database_url, row_factory=dict_row)

    @contextmanager
    def transaction(self) -> Iterator[_PostgresConnection]:
        conn = self.connect()
        try:
            yield _PostgresConnection(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[_PostgresConnection]:
        conn = self.connect()
        try:
            yield _PostgresConnection(conn)
        finally:
            conn.close()

    @staticmethod
    def _columns(conn: _PostgresConnection, table: str) -> set[str]:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=?",
            (table,),
        ).fetchall()
        return {str(row["column_name"]) for row in rows}

    @staticmethod
    def _add_column_if_missing(
        conn: _PostgresConnection, table: str, column: str, ddl: str
    ) -> None:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")

    def _migrate_idempotency(self, conn: _PostgresConnection) -> None:
        # Protected databases are migration-managed/current-schema stores.
        # The SQLite v6.2 table swap is intentionally local-only.
        return None


def build_business_database(settings: Any) -> BusinessDatabase:
    backend = str(settings.database_backend or "sqlite").strip().lower()
    if backend in {"postgres", "postgresql"}:
        if not settings.database_url:
            raise RuntimeError(
                "BUSINESS_DB_BACKEND=postgres requires BUSINESS_DATABASE_URL"
            )
        return PostgresBusinessDatabase(settings.database_url)
    if backend in {"sqlite", "local"}:
        return BusinessDatabase(settings.database_path)
    raise RuntimeError("BUSINESS_DB_BACKEND must be sqlite or postgres")


def as_dict(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_as_dicts(rows: list[sqlite3.Row | Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def json_dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def json_load(text: str) -> Any:
    return json.loads(text)
