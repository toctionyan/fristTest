from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
HARNESS_PATH = ROOT / "scripts" / "verify_full_lifecycle_canary.py"
MANAGED_PATH = ROOT / "scripts" / "run_managed_quality_integration.py"
RECOVERY_PATH = ROOT / "scripts" / "verify_managed_postgres_recovery.py"


def _load_harness_module():
    spec = importlib.util.spec_from_file_location("b16c_harness", HARNESS_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_product_runtime_harness_exposes_postgres_only_restart_boundary() -> None:
    module = _load_harness_module()
    assert hasattr(module.ProductRuntimeHarness, "restart_product_services")
    harness = module.ProductRuntimeHarness()
    try:
        with pytest.raises(RuntimeError, match="PostgreSQL"):
            harness.restart_product_services()
    finally:
        harness.stop()


def test_restart_boundary_preserves_model_and_restarts_public_services(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_harness_module()
    harness = module.ProductRuntimeHarness(
        persistence_url="postgresql+psycopg://quality:secret@127.0.0.1:5432/quality"
    )
    events: list[str] = []
    monkeypatch.setattr(harness, "_stop_named", lambda name: events.append(f"stop:{name}"))
    monkeypatch.setattr(harness, "_start_business", lambda: events.append("start:business"))
    monkeypatch.setattr(harness, "_start_agent", lambda: events.append("start:agent"))
    try:
        harness.restart_product_services()
    finally:
        harness.stop()
    assert events == ["stop:agent-secondary", "stop:agent", "stop:business", "start:business", "start:agent"]
    assert "model" not in " ".join(events)


def test_managed_integration_runs_restart_recovery_against_owned_postgres() -> None:
    source = MANAGED_PATH.read_text(encoding="utf-8")
    assert "run_managed_postgres_recovery(product)" in source
    assert "B16C_POSTGRES_RECOVERY_EVIDENCE" in source
    main_source = source[source.index("def main()") :]
    assert main_source.index("run_managed_postgres_recovery(product)") < main_source.index("completed = subprocess.run(")


def test_recovery_journey_proves_draft_and_receipt_across_two_restarts() -> None:
    source = RECOVERY_PATH.read_text(encoding="utf-8")
    for marker in (
        "AWAITING_AUTHORIZATION",
        "restart_product_services()",
        '"decision": "approved"',
        '"draft_state")) == "COMMITTED"',
        '"receipt_state")) == "SUCCESS"',
        '"restart_count": 2',
        "ThreadPoolExecutor(max_workers=2)",
        "start_secondary_agent()",
        '"concurrent_authority_attempts": 2',
        '"idempotency_replay": True',
    ):
        assert marker in source


def test_secondary_agent_is_postgres_only_and_uses_distinct_port(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_harness_module()
    local = module.ProductRuntimeHarness()
    try:
        with pytest.raises(RuntimeError, match="PostgreSQL"):
            local.start_secondary_agent()
    finally:
        local.stop()

    harness = module.ProductRuntimeHarness(
        persistence_url="postgresql+psycopg://quality:secret@127.0.0.1:5432/quality"
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        harness,
        "_start",
        lambda name, command, **kwargs: captured.update(name=name, command=command, kwargs=kwargs),
    )
    monkeypatch.setattr(module, "wait_http", lambda url, **kwargs: captured.update(url=url))
    try:
        returned = harness.start_secondary_agent()
    finally:
        harness.stop()
    assert returned == harness.secondary_agent_url
    assert captured["name"] == "agent-secondary"
    assert captured["url"] == f"{harness.secondary_agent_url}/health"
    assert captured["kwargs"]["env_override"]["PORT"] == str(harness.secondary_agent_port)
    assert harness.secondary_agent_port != harness.agent_port
