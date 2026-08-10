from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]


def _load_module():
    path = ROOT / "skill-system" / "controller" / "snapshot_cache.py"
    spec = importlib.util.spec_from_file_location("working_set_snapshot_cache_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_exact_remote_identity_is_reused_from_local_cache(tmp_path: Path) -> None:
    module = _load_module()
    cache = module.SnapshotCache(tmp_path / "cache")

    cache.put(
        source="github:toctionyan/fristTest",
        ref="abc123",
        path="scripts/quality_loop.py",
        content="print('cached')\n",
    )

    assert cache.get(
        source="github:toctionyan/fristTest",
        ref="abc123",
        path="scripts/quality_loop.py",
    ) == b"print('cached')\n"


def test_different_ref_never_reuses_stale_content(tmp_path: Path) -> None:
    module = _load_module()
    cache = module.SnapshotCache(tmp_path / "cache")

    cache.put(
        source="github:toctionyan/fristTest",
        ref="old-sha",
        path="scripts/quality_loop.py",
        content="old\n",
    )

    assert cache.get(
        source="github:toctionyan/fristTest",
        ref="new-sha",
        path="scripts/quality_loop.py",
    ) is None


def test_different_path_never_aliases_cached_object(tmp_path: Path) -> None:
    module = _load_module()
    cache = module.SnapshotCache(tmp_path / "cache")

    cache.put(
        source="github:toctionyan/fristTest",
        ref="abc123",
        path="a.py",
        content="same bytes\n",
    )

    assert cache.get(
        source="github:toctionyan/fristTest",
        ref="abc123",
        path="b.py",
    ) is None


def test_corrupted_cached_object_is_a_miss_not_trusted(tmp_path: Path) -> None:
    module = _load_module()
    cache = module.SnapshotCache(tmp_path / "cache")

    entry = cache.put(
        source="github:toctionyan/fristTest",
        ref="abc123",
        path="scripts/quality_loop.py",
        content="trusted\n",
    )
    object_path = cache.root / entry.cache_path
    object_path.write_bytes(b"tampered\n")

    assert cache.get(
        source="github:toctionyan/fristTest",
        ref="abc123",
        path="scripts/quality_loop.py",
    ) is None


def test_identical_content_is_content_addressed_once(tmp_path: Path) -> None:
    module = _load_module()
    cache = module.SnapshotCache(tmp_path / "cache")

    first = cache.put(
        source="github:toctionyan/fristTest",
        ref="abc123",
        path="a.py",
        content="shared\n",
    )
    second = cache.put(
        source="github:toctionyan/fristTest",
        ref="abc123",
        path="b.py",
        content="shared\n",
    )

    assert first.content_sha256 == second.content_sha256
    assert first.cache_path == second.cache_path
    assert cache.snapshot()["entry_count"] == 2
