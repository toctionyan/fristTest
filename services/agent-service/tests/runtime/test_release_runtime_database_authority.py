from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_local_sqlite_harness_discards_inherited_postgres_authorities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "AGENT_DATABASE_URL",
        "DATABASE_URL",
        "CHECKPOINT_DATABASE_URL",
        "BUSINESS_DATABASE_URL",
        "RAG_DATABASE_URL",
        "DOCUMENT_JOB_DATABASE_URL",
    ):
        monkeypatch.setenv(
            name,
            "postgresql+psycopg://quality:secret@127.0.0.1:55432/quality",
        )
    monkeypatch.setenv("STRICT_PERSISTENCE", "true")

    module = _load(
        "release_runtime_database_harness",
        ROOT / "scripts" / "verify_full_lifecycle_canary.py",
    )
    harness = module.ProductRuntimeHarness()
    try:
        assert harness.env["AGENT_DB_BACKEND"] == "sqlite"
        assert harness.env["DATABASE_BACKEND"] == "sqlite"
        assert harness.env["CHECKPOINT_BACKEND"] == "sqlite"
        assert harness.env["STRICT_PERSISTENCE"] == "false"
        for name in (
            "AGENT_DATABASE_URL",
            "DATABASE_URL",
            "CHECKPOINT_DATABASE_URL",
            "BUSINESS_DATABASE_URL",
            "RAG_DATABASE_URL",
            "DOCUMENT_JOB_DATABASE_URL",
        ):
            assert name not in harness.env
    finally:
        harness.stop()


def test_postgres_checkpointer_normalizes_sqlalchemy_url_before_setup_and_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_src = ROOT / "services" / "agent-service" / "src"
    sys.path.insert(0, str(agent_src))
    fencing_module = types.ModuleType("agent_core.runtime.turn_fencing")
    fencing_module.AtomicallyFencedPostgresSaver = lambda conn: conn
    fencing_module.FencedCheckpointer = lambda saver: saver
    monkeypatch.setitem(
        sys.modules,
        "agent_core.runtime.turn_fencing",
        fencing_module,
    )
    try:
        config = _load(
            "agent_core.config_release_runtime_database",
            agent_src / "agent_core" / "config.py",
        )
    finally:
        sys.path.remove(str(agent_src))

    seen: dict[str, object] = {}

    class SetupContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def setup(self) -> None:
            seen["setup_called"] = True

    class PostgresSaver:
        @classmethod
        def from_conn_string(cls, url: str):
            seen["setup_url"] = url
            return SetupContext()

    memory_module = types.ModuleType("langgraph.checkpoint.memory")
    memory_module.InMemorySaver = object
    postgres_module = types.ModuleType("langgraph.checkpoint.postgres")
    postgres_module.PostgresSaver = PostgresSaver
    psycopg_module = types.ModuleType("psycopg")

    def connect(url: str, **kwargs):
        seen["connect_url"] = url
        seen["connect_kwargs"] = kwargs
        return object()

    psycopg_module.connect = connect
    rows_module = types.ModuleType("psycopg.rows")
    rows_module.dict_row = object()
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.memory", memory_module)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres", postgres_module)
    monkeypatch.setitem(sys.modules, "psycopg", psycopg_module)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows_module)
    monkeypatch.setattr(config, "AtomicallyFencedPostgresSaver", lambda conn: conn)
    monkeypatch.setattr(config, "FencedCheckpointer", lambda saver: saver)
    monkeypatch.setenv("APP_PROFILE", "local")
    monkeypatch.setenv("CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setenv(
        "CHECKPOINT_DATABASE_URL",
        "postgresql+psycopg://quality:secret@127.0.0.1:55432/quality",
    )
    monkeypatch.setenv("CHECKPOINT_SETUP", "true")

    config.clear_checkpointer_cache()
    try:
        config.build_checkpointer()
    finally:
        config.clear_checkpointer_cache()

    expected = "postgresql://quality:secret@127.0.0.1:55432/quality"
    assert seen["setup_url"] == expected
    assert seen["connect_url"] == expected
    assert seen["setup_called"] is True
