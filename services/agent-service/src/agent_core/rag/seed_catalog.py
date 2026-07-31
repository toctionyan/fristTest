from __future__ import annotations

"""Domain-neutral installed-module knowledge catalog."""

from typing import Any, Iterable

_documents: tuple[dict[str, Any], ...] = ()


def configure_builtin_knowledge_documents(documents: Iterable[dict[str, Any]]) -> None:
    global _documents
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in documents:
        row = dict(raw or {})
        doc_id = str(row.get("doc_id") or "").strip()
        title = str(row.get("title") or "").strip()
        content = str(row.get("content") or "").strip()
        source = str(row.get("source") or "").strip()
        if not doc_id or not title or not content or not source:
            raise ValueError("installed knowledge document requires doc_id/title/content/source")
        if doc_id in seen:
            raise ValueError(f"duplicate installed knowledge doc_id: {doc_id}")
        seen.add(doc_id)
        metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), dict) else {}
        normalized.append({"doc_id": doc_id, "title": title, "content": content, "source": source, "metadata": metadata})
    _documents = tuple(normalized)


def builtin_knowledge_documents() -> tuple[dict[str, Any], ...]:
    return tuple(dict(item) for item in _documents)


def clear_builtin_knowledge_documents() -> None:
    global _documents
    _documents = ()
