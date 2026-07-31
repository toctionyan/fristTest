from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.support.paths import workspace_root


def _load_script(name: str):
    root = workspace_root(__file__)
    path = root / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_product_runtime_harness_binds_public_services_to_one_postgres_url() -> None:
    script = _load_script("verify_full_lifecycle_canary.py")
    url = "postgresql+psycopg://quality:secret@127.0.0.1:55432/quality_loop"
    harness = script.ProductRuntimeHarness(persistence_url=url)
    try:
        assert harness.env["AGENT_DB_BACKEND"] == "postgres"
        assert harness.env["AGENT_DATABASE_URL"] == url
        assert harness.env["CHECKPOINT_BACKEND"] == "postgres"
        assert harness.env["CHECKPOINT_DATABASE_URL"] == url
        assert harness.env["BUSINESS_DB_BACKEND"] == "postgres"
        assert harness.env["BUSINESS_DATABASE_URL"] == url
        assert harness.env["AGENT_DB_CREATE_SCHEMA"] == "true"
        assert "BUSINESS_DB_PATH" not in harness.env
    finally:
        harness.stop()


def test_product_runtime_harness_rejects_non_postgres_managed_url() -> None:
    script = _load_script("verify_full_lifecycle_canary.py")
    with pytest.raises(ValueError, match="PostgreSQL URL"):
        script.ProductRuntimeHarness(persistence_url="sqlite:////tmp/fake-managed.db")


def test_managed_integration_passes_owned_postgres_to_public_runtime() -> None:
    root = workspace_root(__file__)
    source = (root / "scripts/run_managed_quality_integration.py").read_text(encoding="utf-8")
    assert "ProductRuntimeHarness(persistence_url=postgres.url)" in source
    assert '"AGENT_TEST_POSTGRES_URL": postgres.url' in source
    assert '"BUSINESS_TEST_POSTGRES_URL": postgres.url' in source
