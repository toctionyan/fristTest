import json
from pathlib import Path
from typing import Any
from agent_core.persistence.sqlite_base import SQLiteBase
from agent_core.rag.embeddings import sparse_vector, cosine
from agent_core.rag.access import is_visible


class LocalVectorStore(SQLiteBase):
    def __init__(self, db_path: Path):
        super().__init__(db_path)

    def init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                title TEXT,
                source TEXT,
                metadata_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT,
                title TEXT,
                content TEXT,
                source TEXT,
                vector_json TEXT,
                metadata_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.commit()

    def add_document(self, doc_id: str, title: str, source: str, chunks: list[str], metadata: dict[str, Any] | None = None) -> int:
        metadata = metadata or {}
        self.execute(
            "INSERT OR REPLACE INTO documents(doc_id, title, source, metadata_json) VALUES(?,?,?,?)",
            (doc_id, title, source, json.dumps(metadata, ensure_ascii=False)),
        )
        self.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        for idx, content in enumerate(chunks):
            chunk_id = f"{doc_id}::chunk_{idx:04d}"
            vec = sparse_vector(content)
            self.execute(
                """
                INSERT OR REPLACE INTO chunks(chunk_id, doc_id, title, content, source, vector_json, metadata_json)
                VALUES(?,?,?,?,?,?,?)
                """,
                (chunk_id, doc_id, title, content, source, json.dumps(vec, ensure_ascii=False), json.dumps(metadata, ensure_ascii=False)),
            )
        return len(chunks)

    def search(self, query: str, top_k: int = 5, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        qvec = sparse_vector(query)
        rows = self.query_all("SELECT * FROM chunks")
        scored: list[dict[str, Any]] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            if filters and "__access_scope__" in filters and not is_visible(metadata, filters["__access_scope__"]):
                continue
            # Apply ordinary metadata filters before score/rank too.
            if filters:
                mismatch = False
                for key, expected in filters.items():
                    if key == "__access_scope__" or expected is None:
                        continue
                    value = metadata.get(key)
                    if isinstance(expected, (list, tuple, set)):
                        if value not in expected and not (isinstance(value, list) and any(v in expected for v in value)):
                            mismatch = True; break
                    elif value != expected:
                        mismatch = True; break
                if mismatch:
                    continue
            vec = json.loads(row["vector_json"] or "{}")
            score = cosine(qvec, vec)
            # 关键词直接命中加一点权重
            if query and query in row["content"]:
                score += 0.2
            scored.append({
                "chunk_id": row["chunk_id"],
                "doc_id": row["doc_id"],
                "title": row["title"],
                "content": row["content"],
                "source": row["source"],
                "score": round(float(score), 4),
                "metadata": metadata,
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]


    def get_document(self, doc_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM documents WHERE doc_id=?", (doc_id,))
        if not row:
            return None
        metadata = json.loads(row.get("metadata_json") or "{}")
        if filters and "__access_scope__" in filters and not is_visible(metadata, filters["__access_scope__"]):
            return None
        row["metadata"] = metadata
        row.pop("metadata_json", None)
        return row

    def list_chunks(self, doc_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        doc = self.get_document(doc_id, filters=filters)
        if not doc:
            return []
        rows = self.query_all("SELECT chunk_id, doc_id, title, content, source, metadata_json, created_at FROM chunks WHERE doc_id=? ORDER BY chunk_id", (doc_id,))
        for row in rows:
            try:
                row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
            except Exception:
                row["metadata"] = {}
        return rows

    def list_documents(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        docs = self.query_all("SELECT * FROM documents ORDER BY created_at DESC")
        visible_docs = []
        for doc in docs:
            metadata = json.loads(doc.get("metadata_json") or "{}")
            if filters and "__access_scope__" in filters and not is_visible(metadata, filters["__access_scope__"]):
                continue
            count = self.query_one("SELECT COUNT(*) AS c FROM chunks WHERE doc_id=?", (doc["doc_id"],))
            doc["chunk_count"] = (count or {}).get("c", 0)
            doc["metadata"] = metadata
            doc.pop("metadata_json", None)
            visible_docs.append(doc)
        return visible_docs
