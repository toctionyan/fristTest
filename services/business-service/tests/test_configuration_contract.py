from __future__ import annotations

from pathlib import Path

import pytest

from business_service.api import create_app


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def test_business_template_and_runner_load_service_local_env() -> None:
    template = SERVICE_ROOT / ".env.example"
    assert template.is_file()
    text = template.read_text(encoding="utf-8")
    for key in (
        "APP_PROFILE",
        "BUSINESS_SERVICE_HOST",
        "BUSINESS_SERVICE_PORT",
        "BUSINESS_SEED_DEMO_DATA",
        "BUSINESS_DB_BACKEND",
        "BUSINESS_DATABASE_URL",
        "BUSINESS_SERVICE_TOKEN",
        "BUSINESS_ACTOR_SIGNING_SECRET",
    ):
        assert f"{key}=" in text

    runner = (SERVICE_ROOT / "scripts" / "run_business_api.py").read_text(encoding="utf-8")
    assert 'load_dotenv(ROOT / \".env\")' in runner


def test_openapi_exposes_delivery_urge_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Route registration resolves its request model, rather than leaving a deferred NameError."""
    monkeypatch.setenv("APP_PROFILE", "local")
    monkeypatch.setenv("BUSINESS_SEED_DEMO_DATA", "false")

    schema = create_app(database_path=str(tmp_path / "openapi.db")).openapi()

    assert "/orders/{order_id}/delivery-urges" in schema["paths"]
    assert "DeliveryUrgeRequest" in schema["components"]["schemas"]


def test_production_rejects_local_sqlite_even_with_strong_security(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_PROFILE", "production")
    monkeypatch.setenv("BUSINESS_SERVICE_TOKEN", "production-service-token-1234567890")
    monkeypatch.setenv("BUSINESS_REQUIRE_ACTOR_SIGNATURE", "true")
    monkeypatch.setenv("BUSINESS_ACTOR_SIGNING_SECRET", "production-actor-signing-secret-1234567890")
    monkeypatch.setenv("BUSINESS_SEED_DEMO_DATA", "false")

    monkeypatch.setenv("BUSINESS_DB_BACKEND", "sqlite")
    monkeypatch.delenv("BUSINESS_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="BUSINESS_DB_BACKEND must be postgres"):
        create_app(database_path=str(tmp_path / "production.db"))


def test_protected_postgres_settings_pass_without_opening_a_local_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from business_service.config import BusinessSettings

    monkeypatch.setenv("APP_PROFILE", "production")
    monkeypatch.setenv("BUSINESS_SERVICE_TOKEN", "production-service-token-1234567890")
    monkeypatch.setenv("BUSINESS_REQUIRE_ACTOR_SIGNATURE", "true")
    monkeypatch.setenv("BUSINESS_ACTOR_SIGNING_SECRET", "production-actor-signing-secret-1234567890")
    monkeypatch.setenv("BUSINESS_SEED_DEMO_DATA", "false")
    monkeypatch.setenv("BUSINESS_DB_BACKEND", "postgres")
    monkeypatch.setenv(
        "BUSINESS_DATABASE_URL",
        "postgresql+psycopg://business:secret@db.example/business",
    )

    settings = BusinessSettings.from_env()
    settings.validate_security()
    assert settings.database_backend == "postgres"


def test_production_rejects_demo_seed_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "production")
    monkeypatch.setenv("BUSINESS_SERVICE_TOKEN", "production-service-token-1234567890")
    monkeypatch.setenv("BUSINESS_REQUIRE_ACTOR_SIGNATURE", "true")
    monkeypatch.setenv("BUSINESS_ACTOR_SIGNING_SECRET", "production-actor-signing-secret-1234567890")
    monkeypatch.setenv("BUSINESS_SEED_DEMO_DATA", "true")

    with pytest.raises(RuntimeError, match="BUSINESS_SEED_DEMO_DATA must be false"):
        create_app(database_path=str(tmp_path / "production-seed.db"))


def test_retired_app_env_cannot_downgrade_production_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "production")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("BUSINESS_SERVICE_TOKEN", "dev-service-token")
    monkeypatch.setenv("BUSINESS_REQUIRE_ACTOR_SIGNATURE", "false")
    monkeypatch.delenv("BUSINESS_ACTOR_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("BUSINESS_SEED_DEMO_DATA", "false")

    with pytest.raises(RuntimeError, match="production security invalid"):
        create_app(database_path=str(tmp_path / "must-not-downgrade.db"))


def test_business_service_requires_shared_app_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_PROFILE", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="APP_PROFILE is required"):
        create_app(database_path=str(tmp_path / "missing-profile.db"))


def test_preprod_uses_protected_business_security(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "preprod")
    monkeypatch.setenv("BUSINESS_SERVICE_TOKEN", "dev-service-token")
    monkeypatch.setenv("BUSINESS_REQUIRE_ACTOR_SIGNATURE", "false")
    monkeypatch.setenv("BUSINESS_SEED_DEMO_DATA", "false")
    with pytest.raises(RuntimeError, match="production security invalid"):
        create_app(database_path=str(tmp_path / "preprod.db"))


@pytest.mark.parametrize(
    ("token", "secret", "message"),
    [
        ("short", "production-actor-signing-secret-1234567890", "BUSINESS_SERVICE_TOKEN"),
        ("production-service-token-1234567890", "short", "BUSINESS_ACTOR_SIGNING_SECRET"),
        ("dev-service-token-but-long-enough-123", "production-actor-signing-secret-1234567890", "BUSINESS_SERVICE_TOKEN"),
        ("production-service-token-1234567890", "change-me-actor-signing-secret-123456", "BUSINESS_ACTOR_SIGNING_SECRET"),
    ],
)
def test_protected_business_rejects_weak_or_placeholder_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    token: str,
    secret: str,
    message: str,
) -> None:
    monkeypatch.setenv("APP_PROFILE", "production")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("BUSINESS_SERVICE_TOKEN", token)
    monkeypatch.setenv("BUSINESS_REQUIRE_ACTOR_SIGNATURE", "true")
    monkeypatch.setenv("BUSINESS_ACTOR_SIGNING_SECRET", secret)
    monkeypatch.setenv("BUSINESS_SEED_DEMO_DATA", "false")

    with pytest.raises(RuntimeError, match=message):
        create_app(database_path=str(tmp_path / "weak-secret.db"))
