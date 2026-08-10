from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SNAPSHOT_SCHEMA_VERSION = 1


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_key(source: str, ref: str, path: str) -> str:
    raw = json.dumps(
        {"source": str(source), "ref": str(ref), "path": str(path)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(raw)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class SnapshotEntry:
    source: str
    ref: str
    path: str
    content_sha256: str
    size_bytes: int
    cache_path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "ref": self.ref,
            "path": self.path,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
            "cache_path": self.cache_path,
        }


class SnapshotCache:
    """Content-verified local cache for a frozen remote working set.

    Cache identity includes remote source, immutable/resolved ref and resource
    path. Reads never silently fall back to another ref. Corrupted or missing
    local content is a cache miss, forcing the caller to perform one explicit
    remote refresh through its anti-stall budget.

    Remote reads may run concurrently, but index read-modify-write transactions
    are serialized in-process so bounded parallel acquisition cannot lose cache
    entries through last-writer-wins races.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.objects_dir = self.root / "objects"
        self.index_path = self.root / "index.json"
        self._lock = threading.RLock()

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            return {"schema_version": SNAPSHOT_SCHEMA_VERSION, "entries": {}}
        raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or int(raw.get("schema_version") or 0) != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("snapshot cache index schema mismatch")
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            raise ValueError("snapshot cache index entries must be an object")
        return raw

    def _write_index(self, index: dict[str, Any]) -> None:
        payload = (json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _atomic_write(self.index_path, payload)

    def put(self, *, source: str, ref: str, path: str, content: str | bytes) -> SnapshotEntry:
        payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        content_sha256 = _sha256_bytes(payload)
        with self._lock:
            object_path = self.objects_dir / content_sha256
            if not object_path.is_file():
                _atomic_write(object_path, payload)
            elif _sha256_bytes(object_path.read_bytes()) != content_sha256:
                raise ValueError("snapshot cache object digest mismatch")

            entry = SnapshotEntry(
                source=str(source),
                ref=str(ref),
                path=str(path),
                content_sha256=content_sha256,
                size_bytes=len(payload),
                cache_path=str(object_path.relative_to(self.root)),
            )
            index = self._load_index()
            index.setdefault("entries", {})[_canonical_key(source, ref, path)] = entry.as_dict()
            self._write_index(index)
            return entry

    def get(self, *, source: str, ref: str, path: str) -> bytes | None:
        with self._lock:
            index = self._load_index()
            raw = index.get("entries", {}).get(_canonical_key(source, ref, path))
            if not isinstance(raw, dict):
                return None
            if (
                str(raw.get("source")) != str(source)
                or str(raw.get("ref")) != str(ref)
                or str(raw.get("path")) != str(path)
            ):
                return None
            relative = Path(str(raw.get("cache_path") or ""))
            if relative.is_absolute() or ".." in relative.parts:
                return None
            object_path = self.root / relative
            if not object_path.is_file():
                return None
            payload = object_path.read_bytes()
            digest = _sha256_bytes(payload)
            if digest != str(raw.get("content_sha256") or ""):
                return None
            if len(payload) != int(raw.get("size_bytes") or -1):
                return None
            return payload

    def has(self, *, source: str, ref: str, path: str) -> bool:
        return self.get(source=source, ref=ref, path=path) is not None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            index = self._load_index()
            entries = list(index.get("entries", {}).values())
            return {
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "entry_count": len(entries),
                "entries": sorted(
                    (dict(item) for item in entries if isinstance(item, dict)),
                    key=lambda item: (str(item.get("source")), str(item.get("ref")), str(item.get("path"))),
                ),
            }
