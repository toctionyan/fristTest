from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_core.persistence.sqlite_base import SQLiteBase


class MessageStore(SQLiteBase):
    """Persist customer-visible conversation envelopes.

    ``content`` remains the human-readable fallback for clients and debug
    tools.  ``presentation_json`` and ``interaction_json`` preserve the
    client-neutral view model that was actually delivered, so a reopened
    conversation does not degrade from a compact primary view back into raw
    model prose.  They intentionally store *public display snapshots* only;
    live authority controls continue to be recovered from the authoritative
    graph state through ``pending-interaction``.
    """

    _OPTIONAL_COLUMNS: dict[str, str] = {
        "message_type": "TEXT",
        "presentation_json": "TEXT",
        "interaction_json": "TEXT",
    }

    def __init__(self, db_path: Path):
        super().__init__(db_path)

    def init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT,
                role TEXT,
                content TEXT,
                message_type TEXT,
                presentation_json TEXT,
                interaction_json TEXT,
                created_at TEXT
            )
            """
        )
        # Ensure the current local schema exposes all current envelope columns.
        columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        for name, sql_type in self._OPTIONAL_COLUMNS.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE messages ADD COLUMN {name} {sql_type}")
        self.conn.commit()

    @staticmethod
    def _encode(value: Any | None) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _decode(value: Any | None) -> Any | None:
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except Exception:
            return None

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
        self.execute(
            """
            INSERT INTO messages(
                thread_id, role, content, message_type, presentation_json,
                interaction_json, created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                thread_id,
                role,
                content,
                message_type,
                self._encode(presentation),
                self._encode(interaction),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def list_messages(self, thread_id: str, limit: int = 50) -> list[dict]:
        rows = self.query_all(
            "SELECT * FROM messages WHERE thread_id=? ORDER BY id DESC LIMIT ?",
            (thread_id, limit),
        )[::-1]
        for row in rows:
            row["presentation"] = self._decode(row.pop("presentation_json", None)) or []
            row["interaction"] = self._decode(row.pop("interaction_json", None))
            row["message_type"] = row.get("message_type") or ("chat" if row.get("role") == "user" else "answer")
        return rows
