import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from agent_core.persistence.sqlite_base import SQLiteBase
from agent_core.observability.redaction import redact_for_persistence
from agent_core.observability.correlation import get_correlation_id


class TraceLogger(SQLiteBase):
    def __init__(self, db_path: Path):
        super().__init__(db_path)

    def init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT,
                correlation_id TEXT,
                thread_id TEXT,
                user_id TEXT,
                event_type TEXT,
                node TEXT,
                input_json TEXT,
                output_json TEXT,
                latency_ms INTEGER,
                created_at TEXT
            )
            """
        )
        columns = {str(row[1]) for row in self.conn.execute("PRAGMA table_info(trace_logs)").fetchall()}
        if "correlation_id" not in columns:
            self.conn.execute("ALTER TABLE trace_logs ADD COLUMN correlation_id TEXT")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_logs_correlation_id ON trace_logs(correlation_id)")
        self.conn.commit()

    def log_event(
        self,
        thread_id: str,
        user_id: str | None,
        event_type: str,
        node: str | None = None,
        input_data: Any | None = None,
        output_data: Any | None = None,
        latency_ms: int | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        trace_id = trace_id or str(uuid.uuid4())
        correlation_id = correlation_id or get_correlation_id()
        self.execute(
            """
            INSERT INTO trace_logs(trace_id, correlation_id, thread_id, user_id, event_type, node, input_json, output_json, latency_ms, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trace_id,
                correlation_id,
                thread_id,
                user_id,
                event_type,
                node,
                json.dumps(redact_for_persistence(input_data), ensure_ascii=False, default=str) if input_data is not None else None,
                json.dumps(redact_for_persistence(output_data), ensure_ascii=False, default=str) if output_data is not None else None,
                latency_ms,
                datetime.now(UTC).isoformat(),
            ),
        )
        return trace_id

    def list_recent(self, limit: int = 100) -> list[dict]:
        return self.query_all("SELECT * FROM trace_logs ORDER BY id DESC LIMIT ?", (limit,))

    def list_recent_by_event_type(self, event_type: str, limit: int = 100) -> list[dict]:
        return self.query_all(
            "SELECT * FROM trace_logs WHERE event_type=? ORDER BY id DESC LIMIT ?",
            (str(event_type), max(1, int(limit))),
        )

    def list_by_thread(self, thread_id: str, limit: int = 200) -> list[dict]:
        return self.query_all(
            "SELECT * FROM trace_logs WHERE thread_id=? ORDER BY id ASC LIMIT ?",
            (thread_id, limit),
        )

    def list_by_correlation(self, correlation_id: str, limit: int = 500) -> list[dict]:
        return self.query_all(
            "SELECT * FROM trace_logs WHERE correlation_id=? ORDER BY id ASC LIMIT ?",
            (correlation_id, limit),
        )

    def get_trace(self, trace_log_id: int) -> dict | None:
        return self.query_one("SELECT * FROM trace_logs WHERE id=?", (trace_log_id,))

    def prune_older_than(self, cutoff_iso: str) -> int:
        cursor = self.execute("DELETE FROM trace_logs WHERE created_at < ?", (cutoff_iso,))
        return int(getattr(cursor, "rowcount", 0) or 0)


class TraceTimer:
    def __init__(self):
        self.start = time.time()

    def ms(self) -> int:
        return int((time.time() - self.start) * 1000)
