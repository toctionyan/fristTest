import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_core.persistence.sqlite_base import SQLiteBase
from agent_core.observability.redaction import redact_for_persistence


class ActionAuditStore(SQLiteBase):
    def __init__(self, db_path: Path):
        super().__init__(db_path)

    def init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT,
                user_id TEXT,
                role TEXT,
                action_name TEXT,
                idempotency_key TEXT,
                status TEXT,
                input_json TEXT,
                output_json TEXT,
                created_at TEXT
            )
            """
        )
        self.conn.commit()

    def log_action(
        self,
        *,
        thread_id: str | None,
        user_id: str | None,
        role: str | None,
        action_name: str,
        idempotency_key: str | None,
        status: str,
        input_data: Any,
        output_data: Any,
    ) -> None:
        self.execute(
            """
            INSERT INTO action_audit_logs(
                thread_id, user_id, role, action_name, idempotency_key, status,
                input_json, output_json, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                thread_id,
                user_id,
                role,
                action_name,
                idempotency_key,
                status,
                json.dumps(redact_for_persistence(input_data), ensure_ascii=False, default=str),
                json.dumps(redact_for_persistence(output_data), ensure_ascii=False, default=str),
                datetime.now(UTC).isoformat(),
            ),
        )

    def list_recent(self, limit: int = 100) -> list[dict]:
        return self.query_all("SELECT * FROM action_audit_logs ORDER BY id DESC LIMIT ?", (limit,))

    def prune_older_than(self, cutoff_iso: str) -> int:
        cursor = self.execute("DELETE FROM action_audit_logs WHERE created_at < ?", (cutoff_iso,))
        return int(getattr(cursor, "rowcount", 0) or 0)
