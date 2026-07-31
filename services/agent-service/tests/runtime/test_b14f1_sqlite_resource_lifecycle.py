from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def test_reset_store_provider_cache_closes_cached_provider(monkeypatch) -> None:
    from agent_core.persistence import store_provider as module

    class Provider:
        def __init__(self) -> None:
            self.closed = 0
        def close(self) -> None:
            self.closed += 1

    module.reset_store_provider_cache()
    provider = Provider()
    monkeypatch.setattr(module, "build_store_provider", lambda: provider)
    assert module.get_store_provider() is provider
    module.reset_store_provider_cache()
    assert provider.closed == 1


def test_agent_service_close_releases_provider_and_checkpointer(monkeypatch) -> None:
    from app.services import agent_service as module

    closed: list[str] = []
    monkeypatch.setattr(module, "reset_store_provider_cache", lambda: closed.append("provider"))
    monkeypatch.setattr(module, "clear_checkpointer_cache", lambda: closed.append("checkpointer"))
    service = module.AgentService.__new__(module.AgentService)
    service.close()
    assert closed == ["provider", "checkpointer"]


def test_document_service_close_releases_owned_resources() -> None:
    from app.services.document_service import DocumentService

    closed: list[str] = []
    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name
        def close(self) -> None:
            closed.append(self.name)

    service = DocumentService.__new__(DocumentService)
    service.jobs = Resource("jobs")
    service.object_store = Resource("objects")
    service.close()
    assert closed == ["jobs", "objects"]


def test_local_sparse_operations_close_each_short_lived_store(monkeypatch, tmp_path: Path) -> None:
    from agent_core.rag.providers import local_sparse_provider as module

    entered: list[str] = []
    exited: list[str] = []
    class Store:
        def __enter__(self):
            entered.append("enter")
            return self
        def __exit__(self, exc_type, exc, tb):
            exited.append("exit")
        def add_document(self, **kwargs): return len(kwargs["chunks"])
        def search(self, *args, **kwargs): return []
        def get_document(self, *args, **kwargs): return None
        def list_documents(self, *args, **kwargs): return []
        def list_chunks(self, *args, **kwargs): return []

    provider = module.LocalSparseRagProvider(tmp_path / "vector.db")
    monkeypatch.setattr(provider, "_store", lambda: Store())
    provider.upsert_document("d", "t", "s", ["c"])
    provider.search("q")
    provider.get_document("d")
    provider.list_documents()
    provider.list_chunks("d")
    assert len(entered) == 5
    assert exited == ["exit"] * 5


def test_app_lifespan_closes_document_service() -> None:
    source = (Path(__file__).resolve().parents[2] / "app" / "main.py").read_text(encoding="utf-8")
    assert "close_agent" in source
    assert "close_documents" in source
    assert "app.state.document_service" in source


def test_preprod_diagnostics_closes_query_connection(monkeypatch, tmp_path: Path) -> None:
    from scripts import verify_preprod_full_lifecycle as module

    class Cursor:
        def fetchall(self):
            return []

    class Connection:
        def __init__(self) -> None:
            self.closed = False
        def execute(self, _sql: str):
            return Cursor()
        def close(self) -> None:
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(module.sqlite3, "connect", lambda _database: connection)
    database = tmp_path / "trace.db"
    database.touch()
    assert module._graph_diagnostics(database) == []
    assert connection.closed is True


def _load_product_canary_module():
    workspace = Path(__file__).resolve().parents[4]
    path = workspace / "scripts" / "verify_full_lifecycle_canary.py"
    spec = importlib.util.spec_from_file_location("b14f1_product_canary", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_lifecycle_canary_resolves_declared_or_current_python(monkeypatch, tmp_path: Path) -> None:
    module = _load_product_canary_module()
    executable = tmp_path / "python"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("B14F1_TEST_PYTHON", str(executable))
    assert module._resolve_python("B14F1_TEST_PYTHON", tmp_path / "missing") == executable.resolve()
    monkeypatch.delenv("B14F1_TEST_PYTHON")
    assert module._resolve_python("B14F1_TEST_PYTHON", tmp_path / "missing") == Path(sys.executable).resolve()


def test_product_browser_journey_uses_portable_python_and_canonical_plan_projection() -> None:
    workspace = Path(__file__).resolve().parents[4]
    source = (workspace / "scripts" / "verify_product_browser_journey.py").read_text(encoding="utf-8")
    assert "AGENT_PYTHON = AGENT_ROOT / \".venv/bin/python\"" not in source
    assert "from verify_full_lifecycle_canary import (\n    AGENT_PYTHON," in source
    assert "read_plan_projection(state)" in source
    assert 'state.get("turn_goal_plan")' not in source
    assert 'state.get("workflow_plan")' not in source
    assert "closing(sqlite3.connect(database))" in source


def _load_browser_verifier_module(monkeypatch):
    workspace = Path(__file__).resolve().parents[4]
    scripts = workspace / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    path = scripts / "verify_product_browser_journey.py"
    spec = importlib.util.spec_from_file_location("b14f1_browser_verifier", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_browser_verifier_uses_declared_chromium(monkeypatch, tmp_path: Path) -> None:
    module = _load_browser_verifier_module(monkeypatch)
    browser = tmp_path / "chromium"
    browser.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", str(browser))
    assert module._browser_executable() == browser.resolve()
    journey = (Path(__file__).resolve().parents[2] / "frontend/e2e/product_journey.mjs").read_text(encoding="utf-8")
    assert "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH" in journey
    assert "executablePath: chromiumExecutable" in journey



def test_browser_verifier_prefers_lock_matched_playwright_browser(monkeypatch, tmp_path: Path) -> None:
    module = _load_browser_verifier_module(monkeypatch)
    bundled = tmp_path / "playwright-chromium"
    bundled.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", raising=False)
    monkeypatch.delenv("CHROMIUM_EXECUTABLE_PATH", raising=False)
    monkeypatch.setattr(module, "_playwright_browser_executable", lambda: bundled.resolve())
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/chromium")
    assert module._browser_executable() == bundled.resolve()


def test_browser_verifier_classifies_managed_policy_as_environment_block(monkeypatch) -> None:
    module = _load_browser_verifier_module(monkeypatch)
    failure = module._browser_environment_failure(
        "",
        "page.goto: net::ERR_BLOCKED_BY_ADMINISTRATOR at http://127.0.0.1:4173/web/",
    )
    assert failure == {
        "reason": "browser_managed_policy_blocked_local_runtime",
        "signal": "ERR_BLOCKED_BY_ADMINISTRATOR",
    }


def test_browser_verifier_classifies_missing_playwright_browser_as_environment_block(monkeypatch) -> None:
    module = _load_browser_verifier_module(monkeypatch)
    failure = module._browser_environment_failure(
        "",
        "Executable doesn't exist at /tmp/ms-playwright/chromium-1228/chrome-linux64/chrome",
    )
    assert failure == {
        "reason": "playwright_browser_not_installed",
        "signal": "playwright_executable_missing",
    }

def test_browser_diagnostics_closes_query_connection(monkeypatch, tmp_path: Path) -> None:
    module = _load_browser_verifier_module(monkeypatch)

    class Cursor:
        def fetchall(self):
            return []

    class Connection:
        def __init__(self) -> None:
            self.closed = False
        def execute(self, _sql: str, _params):
            return Cursor()
        def close(self) -> None:
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(module.sqlite3, "connect", lambda _database: connection)
    database = tmp_path / "trace.db"
    database.touch()
    assert module._graph_diagnostics(database) == []
    assert connection.closed is True


def test_browser_journeys_accept_system_chromium_fallback() -> None:
    workspace = Path(__file__).resolve().parents[4]
    verifier = (workspace / "scripts" / "verify_product_browser_journey.py").read_text(encoding="utf-8")
    assert "def _browser_executable()" in verifier
    assert "def _playwright_browser_executable()" in verifier
    assert "ERR_BLOCKED_BY_ADMINISTRATOR" in verifier
    assert '"PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH": str(_browser_executable())' in verifier
    for relative in (
        "services/agent-service/frontend/e2e/product_journey.mjs",
        "services/agent-service/frontend/e2e/strong_context_journey.mjs",
        "services/agent-service/frontend/e2e/strong_context_campaign_journey.mjs",
    ):
        source = (workspace / relative).read_text(encoding="utf-8")
        assert "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH" in source
