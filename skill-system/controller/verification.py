from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path
from typing import Iterable

IGNORED_PARTS = {".git", ".quality", "__pycache__", ".pytest_cache", ".venv", "node_modules"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def _eligible(path: Path, root: Path, patterns: Iterable[str]) -> bool:
    rel = path.relative_to(root).as_posix()
    if rel == "governance/active-change.json":
        return False
    if any(part in IGNORED_PARTS for part in path.relative_to(root).parts):
        return False
    if path.suffix in IGNORED_SUFFIXES:
        return False
    return any(fnmatch.fnmatchcase(rel, pattern) for pattern in patterns)


def source_fingerprint(root: Path, patterns: Iterable[str]) -> tuple[str, int]:
    root = root.resolve()
    digest = hashlib.sha256()
    count = 0
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix()):
        if not _eligible(path, root, patterns):
            continue
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8")); digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest()); digest.update(b"\0")
        count += 1
    return digest.hexdigest(), count
