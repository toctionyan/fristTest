from __future__ import annotations

import importlib.util
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    path = WORKSPACE / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ephemeral_rag_seed_initializes_composition_before_module_knowledge() -> None:
    path = WORKSPACE / "services/agent-service/scripts/seed_ephemeral_rag_fixture.py"
    text = path.read_text(encoding="utf-8")
    assert "from agent_core.composition import get_runtime_registry" in text
    composition_call = text.index("    get_runtime_registry()\n")
    seed_call = text.index("    result = RagBootstrapService().seed_builtin_knowledge()\n")
    assert composition_call < seed_call


def test_real_model_child_diagnostic_is_bounded_and_secret_redacted() -> None:
    module = _load(
        "verify_production_real_model_bundle_wp08_attempt3",
        "scripts/verify_production_real_model_bundle.py",
    )
    secret = "sk-sensitive-production-value"
    result = module._safe_component_diagnostic(  # noqa: SLF001 - certification contract test
        {
            "reason": "semantic_prototype_certification_failed",
            "error_type": "RuntimeError",
            "error_category": "semantic",
            "error": "failure contained " + secret + " and " + ("x" * 3000),
            "unsafe_nested_payload": {"api_key": secret},
        },
        {"OPENAI_API_KEY": secret},
    )
    serialized = repr(result)
    assert secret not in serialized
    assert "unsafe_nested_payload" not in result
    assert set(result) <= {"reason", "error_code", "error_type", "error_category", "error"}
    assert len(result["error"]) <= 1600


def test_postgres_diagnostics_redact_database_credentials_and_known_secrets() -> None:
    module = _load(
        "verify_production_postgres_bundle_wp08_attempt3",
        "scripts/verify_production_postgres_bundle.py",
    )
    secret = "production-token-value"
    text = module._redact_diagnostic_text(  # noqa: SLF001 - certification contract test
        "connect postgresql+psycopg://dbuser:dbpass@127.0.0.1:5432/runtime token=" + secret,
        {"BUSINESS_SERVICE_TOKEN": secret},
    )
    assert "dbuser:dbpass" not in text
    assert secret not in text
    assert "postgresql+psycopg://***@127.0.0.1:5432/runtime" in text
