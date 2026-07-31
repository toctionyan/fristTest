import sqlite3
from pathlib import Path
from threading import Lock


class SQLiteBase:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = Lock()
        self.init_db()

    def init_db(self) -> None:
        pass

    def execute(self, sql: str, params: tuple = ()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query_all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.lock:
            cur = self.conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query_all(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self.lock:
            conn = getattr(self, "conn", None)
            if conn is not None:
                conn.close()
                self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
